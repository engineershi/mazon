# -*- coding: utf-8 -*-
"""Offline integration tests for the lead-segment + price-drop engines wired
into the server (admin pages + JSON APIs)."""

import json
import os
import shutil
import sys
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import indexnow
import mailer
import amazon
import security
import server
import market_engine


def _no_network(req, timeout=None):
    """Plays amazon._urlopen; raises OSError for every route (no network)."""
    raise OSError("offline test stub")


class TestSegmentsAndPricedropServer(unittest.TestCase):

    IPKEY = "127.0.0.1|127.0.0.1"

    @classmethod
    def setUpClass(cls):
        cls.db = "/tmp/pstore_test_seg_%s.db" % uuid.uuid4().hex[:8]
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "pstore.db"), cls.db)
        os.environ["PSTORE_DB"] = cls.db
        os.environ["PSTORE_ADMIN_EMAIL"] = "owner@test.example"
        os.environ["PSTORE_ADMIN_PASSWORD"] = "test-pass-123"
        os.environ.pop("PSTORE_URL", None)
        import importlib
        importlib.reload(server)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.PORT = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls._saved_send = mailer._send
        cls._saved_indexnow_post = indexnow._post
        mailer._send = lambda *a, **k: True
        indexnow._post = lambda *a, **k: None
        cls.cookie = cls._login()

    @classmethod
    def tearDownClass(cls):
        mailer._send = cls._saved_send
        indexnow._post = cls._saved_indexnow_post
        security.SUBSCRIBE_LIMITER.clear("sub|" + cls.IPKEY)
        security.TRACK_LIMITER.clear("trk|" + cls.IPKEY)
        security.PAGEVIEW_LIMITER.clear("pv|" + cls.IPKEY)
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        cls.httpd.server_close()
        if os.path.exists(cls.db):
            os.unlink(cls.db)

    @classmethod
    def _login(cls):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=60)
        conn.request("POST", "/admin/login",
                     body=b"email=owner@test.example&password=test-pass-123",
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        sc = resp.getheader("Set-Cookie")
        resp.read()
        conn.close()
        assert sc and sc.startswith("pstore_admin="), sc
        return sc.split(";")[0]

    @classmethod
    def _raw(cls, path, method="GET", body=None, cookie=None, headers=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=60)
        hdrs = dict(headers or {})
        if cookie:
            hdrs["Cookie"] = cookie
        if body is not None:
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        out = (resp.status, resp.getheader("Content-Type"), resp.read())
        conn.close()
        return out

    def setUp(self):
        security.SUBSCRIBE_LIMITER.clear("sub|" + self.IPKEY)
        security.TRACK_LIMITER.clear("trk|" + self.IPKEY)
        security.API_LIMITER.clear("api|" + self.IPKEY)
        # offline: never hit the network from /api/pricedrop/run
        amazon._scraper_search = lambda *a, **k: ([], "")
        amazon._urlopen = _no_network
        # offline: even if a prior module left a runtime AI key behind, the
        # sequence's AI-copy rewrite must not make a real network call (ai._urlopen
        # raising OSError makes ai.generate return [] -> deterministic fallback)
        import ai as _ai
        self._saved_ai_urlopen = _ai._urlopen
        _ai._urlopen = _no_network
        with server._lock:
            conn = server._db()
            conn.execute("DELETE FROM subscribers")
            conn.execute("DELETE FROM sent_emails")
            conn.execute("DELETE FROM email_events")
            conn.execute("DELETE FROM clicks")
            conn.execute("DELETE FROM niches")
            conn.commit()
            conn.close()

    def tearDown(self):
        import ai as _ai
        _ai._urlopen = self._saved_ai_urlopen
        amazon._urlopen = _no_network

    def _seed(self):
        """Create 5 subscribers with distinct engagement, plus a saved niche."""
        with server._lock:
            conn = server._db()
            subs = [
                ("hot@x", "keto snacks", 1, 1, 1),
                ("click2@x", "keto snacks", 1, 1, 1),
                ("warm@x", "keto snacks", 1, 1, 0),
                ("cold@x", "keto snacks", 1, 0, 0),
                ("gone@x", "keto snacks", 1, 1, 1),
            ]
            insertion = []
            for email, kw, _u, _c, _ in subs:
                c = conn.execute(
                    "INSERT INTO subscribers (email, keyword, confirmed, unsubscribed, sent_index) "
                    "VALUES (?,?,1,0,0)", (email, kw)).lastrowid
                insertion.append((email, c))
            # opens: hot, click2, warm (cold never opens, gone unsubscribed)
            for email, sid in insertion:
                if email == "gone@x":
                    conn.execute("UPDATE subscribers SET unsubscribed=1 WHERE id=?",
                                 (sid,))
                if email in ("hot@x", "click2@x", "warm@x"):
                    conn.execute(
                        "INSERT INTO email_events (type, subscriber_id, email_index, keyword, asin) "
                        "VALUES ('open',?,1,?,?)", (sid, "keto snacks", ""))
            # email clicks: hot + click2 (clicked), hot on a product ASIN
            for email, sid in insertion:
                if email == "hot@x":
                    conn.execute(
                        "INSERT INTO clicks (slug, source, referrer, asin) "
                        "VALUES ('keto snacks','email',?,?)",
                        ("%s|1" % sid, "B012345678"))
                elif email == "click2@x":
                    conn.execute(
                        "INSERT INTO clicks (slug, source, referrer, asin) "
                        "VALUES ('keto snacks','email',?,?)",
                        ("%s|1" % sid, ""))
            # a saved niche with one product so pricedrop has something to watch
            conn.execute(
                "INSERT INTO niches (keyword, market, products, created_at) "
                "VALUES (?,?,?,datetime('now'))",
                ("keto snacks", "com", json.dumps([
                    {"asin": "B012345678", "title": "Keto Gummies",
                     "price": 19.99}])))
            conn.commit()
            conn.close()
        return f"https://{self.IPKEY}"

    def test_segments_api_counts(self):
        self._seed()
        st, ct, body = self._raw("/api/segments", cookie=self.cookie)
        self.assertEqual(st, 200)
        data = json.loads(body)
        # hot@x opened + clicked a product ASIN -> CONVERTED (real bucket, was dead)
        self.assertEqual(data["counts"]["converted"], 1)
        # click2@x opened + clicked (no ASIN) -> HOT
        self.assertEqual(data["counts"]["hot"], 1)
        self.assertEqual(data["counts"]["warm"], 1)
        self.assertEqual(data["counts"]["cold"], 1)
        self.assertEqual(data["counts"]["inactive"], 1)
        self.assertEqual(data["total"], 5)
        # per-segment engagement stats surfaced for the ROI lens
        self.assertIn("stats", data)
        self.assertIn("open_rate", data["stats"]["converted"])

    def test_admin_segments_page(self):
        self._seed()
        st, ct, body = self._raw("/admin/segments", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertIn(b"Lead lifecycle", body)
        self.assertIn(b"hot@x", body)

    def test_pricedrop_api_watched(self):
        self._seed()
        st, ct, body = self._raw("/api/pricedrop", cookie=self.cookie)
        self.assertEqual(st, 200)
        data = json.loads(body)
        self.assertIn("watched", data)
        self.assertIn("count", data)

    def test_pricedrop_admin_page(self):
        self._seed()
        st, ct, body = self._raw("/admin/pricedrop", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertIn(b"Price-drop", body)

    def test_pricedrop_run_no_network(self):
        """Offline: /api/pricedrop/run must not raise even with no network."""
        self._seed()
        st, ct, body = self._raw("/api/pricedrop/run", method="POST",
                                 body=b"{}", cookie=self.cookie)
        # request always answers 200 with a result dict (best-effort)
        self.assertEqual(st, 200)
        data = json.loads(body)
        self.assertIn("drops", data)

    def test_reengage_cold_sends_only_cold(self):
        """Re-engagement must target only COLD leads and be deduped."""
        self._seed()
        saved = (mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD)
        mailer.SMTP_HOST = "smtp.test.local"
        mailer.SMTP_USER = "x@x"
        mailer.SMTP_PASSWORD = "pw"
        try:
            captured = []
            mailer._send = lambda subject, body, to, attachments=None, pixel_url=None: (
                captured.append({"to": to}) or True)
            st, ct, body = self._raw("/api/segments/reengage", method="POST",
                                     body=b"{}", cookie=self.cookie)
            self.assertEqual(st, 200)
            data = json.loads(body)
            self.assertEqual(data["sent"], 1)      # only cold@x
            self.assertEqual(data["already_sent"], 0)
            self.assertEqual([c["to"] for c in captured], ["cold@x"])
            # second run: deduped, nothing new sent
            mailer._send = lambda *a, **k: (captured.append({"to": "x"}) or True)
            st2, _, body2 = self._raw("/api/segments/reengage", method="POST",
                                      body=b"{}", cookie=self.cookie)
            d2 = json.loads(body2)
            self.assertEqual(d2["sent"], 0)
            self.assertEqual(d2["already_sent"], 1)
        finally:
            mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD = saved

    def test_pricedrop_send_offline_no_raises(self):
        """Auto price-drop push must answer 200 and not raise with no network."""
        self._seed()
        st, ct, body = self._raw("/api/pricedrop/send", method="POST",
                                 body=b"{}", cookie=self.cookie)
        self.assertEqual(st, 200)
        data = json.loads(body)
        self.assertEqual(data["ok"], True)
        self.assertIn("drops", data)

    def test_sequence_send_branches_converted_to_upsell(self):
        """Segment-aware sequence: a lead who clicked a product ASIN (hot@x in
        _seed -> CONVERTED) gets the review + value-ladder upsell follow-up, not
        another nurture email, and is marked fully nurtured (sent_index reaches
        SEQUENCE_LENGTH). Nothing is sent to the unsubscribed lead."""
        self._seed()
        saved = (mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD)
        mailer.SMTP_HOST = "smtp.test.local"
        mailer.SMTP_USER = "x@x"
        mailer.SMTP_PASSWORD = "pw"
        captured = []
        mailer._send = lambda subject, body, to, attachments=None, pixel_url=None: (
            captured.append({"to": to, "subject": subject}) or True)
        try:
            st, ct, body = self._raw("/api/sequence/send", method="POST",
                                     body=b"{}", cookie=self.cookie)
            self.assertEqual(st, 200)
            data = json.loads(body)
            self.assertTrue(data["ok"], data)
            # 4 eligible (converted+hot+warm+cold); gone@x is unsubscribed/inactive
            self.assertEqual(data["sent"], 4)
            self.assertEqual(data["converted"], 1)
            by_to = {c["to"]: c["subject"] for c in captured}
            self.assertIn("hot@x", by_to)
            self.assertIn("ladder", by_to["hot@x"].lower())
            # the converted lead is fully nurtured, not advanced by one
            with server._lock:
                conn = server._db()
                hot = conn.execute(
                    "SELECT sent_index FROM subscribers WHERE email='hot@x'").fetchone()
                cold = conn.execute(
                    "SELECT sent_index FROM subscribers WHERE email='cold@x'").fetchone()
                gone = conn.execute(
                    "SELECT sent_index FROM subscribers WHERE email='gone@x'").fetchone()
                conn.close()
            self.assertEqual(hot["sent_index"], mailer.SEQUENCE_LENGTH)
            self.assertEqual(cold["sent_index"], 1)
            self.assertEqual(gone["sent_index"], 0)
        finally:
            mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD = saved
        # and the converted follow-up copy exists as a deterministic template
        items = json.loads(self._seed_get_products())
        mail = market_engine.build_converted_followup("keto snacks", items)
        self.assertIsNotNone(mail)
        self.assertIn("ladder", mail["subject"].lower())

    def test_subjects_autoclean_disables_low_opener(self):
        """Email-subject A/B auto-cleanup keeps the high-opening variant and
        disables the one opening below 25% of the winner, once a position has
        enough lifetime sends."""
        server._set_setting("ab.subjects_min_sends", "5")
        try:
            with server._lock:
                conn = server._db()
                conn.execute(
                    "INSERT INTO email_subjects (keyword, email_index, variant, subject, enabled) "
                    "VALUES ('keto snacks',1,1,'Variant One',1),('keto snacks',1,2,'Variant Two',1)")
                for i in range(1, 31):
                    sid = conn.execute(
                        "INSERT INTO subscribers (email, keyword, confirmed, unsubscribed, sent_index) "
                        "VALUES (?,?,1,0,0)", ("v1_%s@x" % i, "keto snacks")).lastrowid
                    conn.execute(
                        "INSERT INTO sent_emails (subscriber_id, email_index, subject, subject_variant) "
                        "VALUES (?,1,'Variant One',1)", (sid,))
                    conn.execute(
                        "INSERT INTO email_events (type, subscriber_id, email_index, keyword, asin) "
                        "VALUES ('open',?,1,?,'')", (sid, "keto snacks"))
                for i in range(1, 31):
                    sid = conn.execute(
                        "INSERT INTO subscribers (email, keyword, confirmed, unsubscribed, sent_index) "
                        "VALUES (?,?,1,0,0)", ("v2_%s@x" % i, "keto snacks")).lastrowid
                    conn.execute(
                        "INSERT INTO sent_emails (subscriber_id, email_index, subject, subject_variant) "
                        "VALUES (?,1,'Variant Two',2)", (sid,))
                    if i <= 2:  # only 2 of 30 open variant Two
                        conn.execute(
                            "INSERT INTO email_events (type, subscriber_id, email_index, keyword, asin) "
                            "VALUES ('open',?,1,?,'')", (sid, "keto snacks"))
                conn.commit()
                conn.close()
            st, ct, body = self._raw("/api/subjects/autoclean", method="POST",
                                     body=b"{}", cookie=self.cookie)
            self.assertEqual(st, 200)
            data = json.loads(body)
            self.assertTrue(data["ok"])
            self.assertEqual(len(data["changed"]), 1)
            self.assertEqual(data["changed"][0]["disabled"], [2])
            self.assertEqual(data["changed"][0]["kept"], 1)
            with server._lock:
                conn = server._db()
                en1 = conn.execute("SELECT enabled FROM email_subjects "
                                   "WHERE keyword='keto snacks' AND variant=1").fetchone()
                en2 = conn.execute("SELECT enabled FROM email_subjects "
                                   "WHERE keyword='keto snacks' AND variant=2").fetchone()
                conn.close()
            self.assertEqual(en1["enabled"], 1)
            self.assertEqual(en2["enabled"], 0)
        finally:
            server._set_setting("ab.subjects_min_sends", "")

    def _seed_get_products(self):
        with server._lock:
            conn = server._db()
            r = conn.execute("SELECT products FROM niches WHERE keyword=?",
                             ("keto snacks",)).fetchone()
            conn.close()
        return r["products"] if r else "[]"


if __name__ == "__main__":
    unittest.main()

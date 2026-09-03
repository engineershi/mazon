# -*- coding: utf-8 -*-
"""Offline tests for the pstore email suite (opt-in, unsubscribe, click
analytics, sequence auto-send, admin email/ebook/analytics pages)."""

import json
import os
import shutil
import sys
import threading
import unittest
import urllib.parse
import uuid
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai
import ebook
import indexnow
import mailer
import market_engine
import security
import server
import seo


class TestEmailSuite(unittest.TestCase):
    """Boots a real HTTP server against a copied DB; stubs SMTP so nothing
    leaves the box. Each test starts from a clean subscriber/click state."""

    IPKEY = "127.0.0.1|127.0.0.1"

    @classmethod
    def setUpClass(cls):
        cls.db = "/tmp/pstore_test_mail_%s.db" % uuid.uuid4().hex[:8]
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
        cls.sent = []
        mailer._send = cls._fake_send
        cls._saved_indexnow_post = indexnow._post
        indexnow._post = cls._fake_indexnow_post
        cls.cookie = cls._login()

    @classmethod
    def _fake_send(cls, subject, body, to, attachments=None, pixel_url=None):
        cls.sent.append({"subject": subject, "body": body, "to": to,
                         "attachments": attachments, "pixel_url": pixel_url})
        return True

    @classmethod
    def _fake_indexnow_post(cls, url, payload, timeout=20):
        return None

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
        conn = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=5)
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
        conn = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=8)
        hdrs = dict(headers or {})
        if cookie:
            hdrs["Cookie"] = cookie
        if body is not None:
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        out = (resp.status, resp.getheader("Location"), resp.getheader("Content-Type"),
               resp.read())
        conn.close()
        return out

    def setUp(self):
        security.SUBSCRIBE_LIMITER.clear("sub|" + self.IPKEY)
        security.TRACK_LIMITER.clear("trk|" + self.IPKEY)
        with server._lock:
            conn = server._db()
            conn.execute("DELETE FROM subscribers")
            conn.execute("DELETE FROM sent_emails")
            conn.execute("DELETE FROM email_events")
            conn.execute("DELETE FROM clicks")
            conn.commit()
            conn.close()
        self.sent.clear()

    def _subscribe(self, email, keyword="keto snacks", extra=""):
        body = "email=%s&keyword=%s%s" % (
            urllib.parse.quote(email),
            urllib.parse.quote(keyword),
            ("&" + extra) if extra else "")
        return self._raw("/subscribe", "POST", body=body)

    def _sub(self, email):
        with server._lock:
            conn = server._db()
            row = conn.execute("SELECT * FROM subscribers WHERE email=?", (email,)).fetchone()
            conn.close()
        return dict(row) if row else None

    def _clicks(self):
        with server._lock:
            conn = server._db()
            rows = conn.execute("SELECT * FROM clicks").fetchall()
            conn.close()
        return [dict(r) for r in rows]

    def _bs_items(self, keyword="keto snacks"):
        with server._lock:
            conn = server._db()
            r = conn.execute("SELECT products FROM niches WHERE keyword=?", (keyword,)).fetchone()
            conn.close()
        return json.loads(r["products"] or "[]") if r else []

    # ---------------------------------------------------------------- subscribe

    def test_subscribe_public_and_records(self):
        st, _, _, data = self._subscribe("sub@example.com")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(data)["ok"])
        row = self._sub("sub@example.com")
        self.assertEqual(row["keyword"], "keto snacks")
        self.assertEqual(row["unsubscribed"], 0)
        self.assertEqual(row["confirmed"], 1)

    def test_subscribe_rejects_bad_email(self):
        st, _, _, data = self._subscribe("notanemail")
        self.assertEqual(st, 200)
        self.assertFalse(json.loads(data)["ok"])

    def test_subscribe_resubscribes_after_unsubscribe(self):
        mail = "again@example.com"
        self._subscribe(mail)
        with server._lock:
            conn = server._db()
            conn.execute("UPDATE subscribers SET unsubscribed=1, confirmed=0 WHERE email=?", (mail,))
            conn.commit()
            conn.close()
        self.assertEqual(self._sub(mail)["unsubscribed"], 1)
        st, _, _, data = self._subscribe(mail, extra="first_name=Ann")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(data)["ok"])
        row = self._sub(mail)
        self.assertEqual(row["unsubscribed"], 0)
        self.assertEqual(row["first_name"], "Ann")

    def test_subscribe_rate_limited(self):
        base = "rl%d@example.com"
        last = 200
        for n in range(security.SUBSCRIBE_LIMITER.limit + 1):
            st, _, _, _ = self._subscribe(base % n)
            last = st
        self.assertEqual(last, 429)

    # -------------------------------------------------------------- unsubscribe

    def test_unsubscribe_with_valid_token(self):
        self._subscribe("out@example.com")
        url = mailer.unsubscribe_url("out@example.com").split("://", 1)[-1]
        path = url.split("/", 1)[1]
        st, _, ctype, data = self._raw("/" + path)
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("text/html"))
        self.assertIn("unsubscribed", data.decode("utf-8", "replace").lower())
        self.assertEqual(self._sub("out@example.com")["unsubscribed"], 1)

    def test_unsubscribe_rejects_bad_token(self):
        self._subscribe("bad@example.com")
        st, _, _, data = self._raw("/unsubscribe?e=bad@example.com&t=forged")
        self.assertEqual(st, 200)
        self.assertIn("didn't work", data.decode("utf-8", "replace"))
        self.assertEqual(self._sub("bad@example.com")["unsubscribed"], 0)

    # ------------------------------------------------------------------- beacon

    def test_track_click_records_hashed_ip(self):
        st, _, _, data = self._raw(
            "/api/track", "POST",
            body="slug=keto-snacks&source=niche&referrer=https%3A%2F%2Fexample.com%2Fx&asin=b0keto1234")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(data)["ok"])
        rows = self._clicks()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["slug"], "keto-snacks")
        self.assertEqual(rows[0]["source"], "niche")
        self.assertEqual(rows[0]["asin"], "B0KETO1234")
        self.assertNotEqual(rows[0]["ip"], "127.0.0.1")
        self.assertEqual(len(rows[0]["ip"]), 16)
        self.assertIn("example.com", rows[0]["referrer"])

    def test_track_click_get_pixel_alias(self):
        st, _, _, data = self._raw("/api/track?slug=home&source=home")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(data)["ok"])
        self.assertTrue(any(r["slug"] == "home" for r in self._clicks()))

    def test_analytics_reports_most_clicked_products(self):
        for asin in ("B0WINNER", "B0WINNER", "B0LOSER"):
            self._raw("/api/track", "POST",
                      body="slug=keto-snacks&source=niche&asin=%s" % asin)
        st, _, _, html = self._raw("/admin/analytics", cookie=self.cookie)
        self.assertEqual(st, 200)
        body = html.decode("utf-8", "replace")
        self.assertIn("Most-clicked products", body)
        self.assertIn("B0WINNER", body)
        self.assertIn("B0LOSER", body)
        self.assertIn("<td>B0WINNER</td><td>keto-snacks</td><td class='ct'>2</td>", body)

    # ----------------------------------------------------------------- admin api

    def test_subscribers_api_requires_auth(self):
        st, _, _, body = self._raw("/api/subscribers")
        self.assertEqual(st, 401)
        self.assertIn(b"unauthorized", body)
        self._subscribe("api@example.com")
        st, _, _, data = self._raw("/api/subscribers", cookie=self.cookie)
        self.assertEqual(st, 200)
        payload = json.loads(data)
        self.assertEqual(payload["stats"]["total"], 1)
        self.assertEqual(payload["stats"]["active"], 1)
        self.assertEqual(payload["subscribers"][0]["email"], "api@example.com")

    def test_sequence_send_requires_auth(self):
        st, _, _, _ = self._raw("/api/sequence/send")
        self.assertEqual(st, 401)

    def test_sequence_send_refuses_when_smtp_unconfigured(self):
        saved = (mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD)
        mailer.SMTP_HOST = mailer.SMTP_USER = mailer.SMTP_PASSWORD = ""
        try:
            st, _, _, data = self._raw("/api/sequence/send", "POST", body="{}", cookie=self.cookie)
            self.assertEqual(st, 200)
            self.assertFalse(json.loads(data)["ok"])
            self.assertIn("SMTP", json.loads(data)["error"])
        finally:
            mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD = saved

    def test_sequence_send_smokes_next_emails(self):
        saved = (mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD)
        mailer.SMTP_HOST = "smtp.test.local"
        mailer.SMTP_USER = "u@example.com"
        mailer.SMTP_PASSWORD = "pw"
        self._subscribe("seq@example.com")
        try:
            for expect_idx in (1, 2):
                st, _, _, data = self._raw(
                    "/api/sequence/send", "POST", body="{}", cookie=self.cookie)
                self.assertEqual(st, 200)
                self.assertTrue(json.loads(data)["ok"])
                self.assertEqual(json.loads(data)["sent"], 1)
                self.assertEqual(self._sub("seq@example.com")["sent_index"], expect_idx)
            self.assertGreaterEqual(len(self.sent), 2)
            self.assertTrue(all(s["to"] == "seq@example.com" for s in self.sent[:2]))
            with server._lock:
                conn = server._db()
                cnt = conn.execute("SELECT COUNT(*) c FROM sent_emails").fetchone()["c"]
                conn.close()
            self.assertEqual(cnt, 2)
            self.assertIn("unsubscribe", self.sent[0]["body"].lower())
        finally:
            mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD = saved

    # ----------------------------------------------- tracked email links + opens

    def _items_by_asin(self, asin):
        for it in self._bs_items():
            if (it.get("asin") or "").upper() == asin.upper():
                return it
        return None

    def test_sequence_email_wraps_links_in_tracked_url(self):
        saved = (mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD)
        mailer.SMTP_HOST = "smtp.test.local"; mailer.SMTP_USER = "u@example.com"
        mailer.SMTP_PASSWORD = "pw"
        self._subscribe("trk@example.com")
        try:
            self._raw("/api/sequence/send", "POST", body="{}", cookie=self.cookie)
            self.assertEqual(len(self.sent), 1)
            body = self.sent[0]["body"]
            self.assertIn("/e/", body)  # click-tracked outbound link
            self.assertIn("pixel", "") if False else None
        finally:
            mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD = saved

    def test_sequence_email_attaches_pdf_to_first_email(self):
        saved = (mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD)
        mailer.SMTP_HOST = "smtp.test.local"; mailer.SMTP_USER = "u@example.com"
        mailer.SMTP_PASSWORD = "pw"
        self._subscribe("pdf@example.com")
        try:
            self._raw("/api/sequence/send", "POST", body="{}", cookie=self.cookie)
            att = self.sent[0]["attachments"] if self.sent else None
            self.assertTrue(att, "email #1 should carry the lead-magnet PDF")
            name, data = att[0]
            self.assertTrue(name.endswith(".pdf"))
            self.assertTrue(data.startswith(b"%PDF-"))
        finally:
            mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD = saved

    def test_email_click_redirect_records_and_302s(self):
        items = self._bs_items()
        self.assertTrue(items, "need at least one stored product")
        asin = items[0]["asin"]
        url = mailer.tracked_url("keto-snacks", asin, 7, 1)
        path = url.split("://", 1)[-1]
        path = "/" + path.split("/", 1)[1]
        self._subscribe("go@example.com")
        st, location, ctype, data = self._raw(path)
        self.assertEqual(st, 302)
        self.assertIn("amazon", location, location)
        rows = self._clicks()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["slug"], "keto-snacks")
        self.assertEqual(rows[0]["source"], "email")
        self.assertEqual(rows[0]["asin"], asin.upper())

    def test_email_click_rejects_forged_token(self):
        self._subscribe("forged@example.com")
        st, _, _, _ = self._raw("/e/forged-token")
        self.assertEqual(st, 404)
        self.assertEqual(self._clicks(), [])

    def test_email_open_pixel_records_event(self):
        url = mailer.open_pixel_url("keto-snacks", "B0SNACK123", 9, 3)
        path = "/" + url.split("://", 1)[-1].split("/", 1)[1]
        st, _, ctype, data = self._raw(path)
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("image/gif"))
        with server._lock:
            conn = server._db()
            row = conn.execute(
                "SELECT * FROM email_events WHERE type='open' ORDER BY id DESC LIMIT 1").fetchone()
            conn.close()
        self.assertTrue(row)
        self.assertEqual(row["subscriber_id"], 9)
        self.assertEqual(row["email_index"], 3)
        self.assertEqual(row["keyword"], "keto-snacks")

    def test_email_open_pixel_ignores_forged_token(self):
        st, _, ctype, data = self._raw("/e/o/forged")
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("image/gif"))
        with server._lock:
            conn = server._db()
            cnt = conn.execute("SELECT COUNT(*) c FROM email_events").fetchone()["c"]
            conn.close()
        self.assertEqual(cnt, 0)

    def test_sequence_email_embeds_open_pixel(self):
        saved = (mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD)
        mailer.SMTP_HOST = "smtp.test.local"; mailer.SMTP_USER = "u@example.com"
        mailer.SMTP_PASSWORD = "pw"
        self._subscribe("pix@example.com")
        try:
            self._raw("/api/sequence/send", "POST", body="{}", cookie=self.cookie)
            self.assertTrue(self.sent)
            self.assertTrue(self.sent[0]["pixel_url"])
            self.assertIn("/e/o/", self.sent[0]["pixel_url"])
        finally:
            mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD = saved

    def test_tracked_token_round_trip_and_forgery(self):
        for scope in ("e", "o"):
            kw, asin, sid, idx = "keto-snacks", "B0KETO123", 11, 4
            url = mailer.tracked_url(kw, asin, sid, idx) if scope == "e" \
                else mailer.open_pixel_url(kw, asin, sid, idx)
            tok = url.split("/%s/" % scope, 1)[-1] if scope == "o" else url.split("/e/", 1)[-1]
            payload = mailer.decode_track_token(tok, scope=scope)
            self.assertEqual(payload, (kw, asin, str(sid), str(idx)))
        self.assertIsNone(mailer.decode_track_token("e:evil|B0X|1|1:99999:n:s"))

    def test_email_deliverability_headers_in_message(self):
        saved = mailer.REPLY_TO
        mailer.REPLY_TO = "replies@example.com"
        try:
            pix = "http://p.example/e/o/xyz"
            msg = mailer._build_message("Subj", "Body", "to@example.com",
                                        "from@example.com", pixel_url=pix)
            self.assertEqual(msg["Reply-To"], "replies@example.com")
            self.assertEqual(msg["Return-Path"], "replies@example.com")
            self.assertTrue(msg["List-Unsubscribe"])
            self.assertIn('<img src="%s"' % pix,
                          msg.get_payload()[0].get_payload(decode=True).decode("utf-8"))
            msg2 = mailer._build_message("Subj", "Body", "to@example.com", "from@example.com",
                                         attachments=[("lead.pdf", b"%PDF-1.4")])
            self.assertEqual(msg2["Reply-To"], "replies@example.com")
            for part in msg2.walk():
                if part.get_filename() and part.get_filename().endswith(".pdf"):
                    self.assertEqual(part.get_payload(decode=True), b"%PDF-1.4")
                    break
            else:
                self.fail("PDF attachment missing from multipart")
        finally:
            mailer.REPLY_TO = saved

    def test_sequence_send_dry_run_sends_nothing(self):
        saved = (mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD)
        mailer.SMTP_HOST = "smtp.test.local"
        mailer.SMTP_USER = "u@example.com"
        mailer.SMTP_PASSWORD = "pw"
        self._subscribe("dry@example.com")
        try:
            st, _, _, data = self._raw(
                "/api/sequence/send", "POST", body='{"dry_run":true}',
                headers={"Content-Type": "application/json"}, cookie=self.cookie)
            payload = json.loads(data)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["sent"], 0)
            self.assertEqual(payload["ready"], 1)
            self.assertEqual(self.sent, [])
        finally:
            mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD = saved

    def test_sequence_send_scoped_to_one_keyword(self):
        saved = (mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD)
        mailer.SMTP_HOST = "smtp.test.local"
        mailer.SMTP_USER = "u@example.com"
        mailer.SMTP_PASSWORD = "pw"
        self._subscribe("keto1@example.com", keyword="keto snacks")
        self._subscribe("keto2@example.com", keyword="keto snacks")
        self._subscribe("yoga1@example.com", keyword="yoga mat")
        try:
            def _dry(kw):
                st, _, _, data = self._raw(
                    "/api/sequence/send", "POST",
                    body=json.dumps({"dry_run": True, "keyword": kw}),
                    headers={"Content-Type": "application/json"}, cookie=self.cookie)
                self.assertEqual(st, 200)
                return json.loads(data)

            p = _dry("keto snacks")
            self.assertEqual(p["ready"], 2)
            self.assertEqual(p["keyword"], "keto snacks")
            p = _dry("yoga mat")
            self.assertEqual(p["ready"], 1)
            p = _dry("no such niche")
            self.assertEqual(p["ready"], 0)
            # a real (non-dry) scoped send only touches that niche's subscribers
            st, _, _, data = self._raw(
                "/api/sequence/send", "POST",
                body=json.dumps({"keyword": "keto snacks"}),
                headers={"Content-Type": "application/json"}, cookie=self.cookie)
            self.assertEqual(st, 200)
            self.assertEqual(json.loads(data)["sent"], 2)
            self.assertEqual(self._sub("keto1@example.com")["sent_index"], 1)
            self.assertEqual(self._sub("keto2@example.com")["sent_index"], 1)
            self.assertEqual(self._sub("yoga1@example.com")["sent_index"], 0)
        finally:
            mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD = saved

    def test_sequence_send_honors_limit(self):
        saved = (mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD)
        mailer.SMTP_HOST = "smtp.test.local"
        mailer.SMTP_USER = "u@example.com"
        mailer.SMTP_PASSWORD = "pw"
        for i in range(4):
            self._subscribe("lim%d@example.com" % i)
        try:
            st, _, _, data = self._raw(
                "/api/sequence/send", "POST", body='{"dry_run":true,"limit":2}',
                headers={"Content-Type": "application/json"}, cookie=self.cookie)
            p = json.loads(data)
            self.assertEqual(p["ready"], 2)
            self.assertEqual(p["limit"], 2)
            # a real limited send touches exactly the requested count
            st, _, _, data = self._raw(
                "/api/sequence/send", "POST", body='{"limit":2}',
                headers={"Content-Type": "application/json"}, cookie=self.cookie)
            self.assertEqual(json.loads(data)["sent"], 2)
            sent_idx = [self._sub("lim%d@example.com" % i)["sent_index"] for i in range(4)]
            self.assertEqual(sum(1 for s in sent_idx if s == 1), 2)
        finally:
            mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD = saved

    def test_limit_zero_means_all_up_to_max(self):
        saved = (mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD)
        mailer.SMTP_HOST = "smtp.test.local"
        mailer.SMTP_USER = "u@example.com"
        mailer.SMTP_PASSWORD = "pw"
        for i in range(3):
            self._subscribe("all%d@example.com" % i)
        try:
            st, _, _, data = self._raw(
                "/api/sequence/send", "POST", body='{"dry_run":true,"limit":0}',
                headers={"Content-Type": "application/json"}, cookie=self.cookie)
            p = json.loads(data)
            self.assertEqual(p["ready"], 3)
        finally:
            mailer.SMTP_HOST, mailer.SMTP_USER, mailer.SMTP_PASSWORD = saved

    # --------------------------------------------------------------- personalisation

    def test_render_body_derives_first_name_from_email(self):
        mail = market_engine.build_email_sequence("keto snacks", self._bs_items())[0]
        body = mailer.render_body(mail, to_name="", email="jane.doe@example.com")
        self.assertIn("Hi Jane Doe", body)

    def test_render_body_uses_captured_name_first(self):
        mail = market_engine.build_email_sequence("keto snacks", self._bs_items())[0]
        body = mailer.render_body(mail, to_name="Ann", email="jane@example.com")
        self.assertIn("Hi Ann", body)
        self.assertNotIn("Hi Jane", body)

    def test_render_body_your_name_signature(self):
        mail = market_engine.build_email_sequence("keto snacks", self._bs_items())[0]
        saved = mailer.STORE_NAME
        mailer.STORE_NAME = "The Pick Crew"
        try:
            body = mailer.render_body(mail, email="u@example.com")
            self.assertIn("The Pick Crew", body)
            self.assertNotIn("{your_name}", body)
        finally:
            mailer.STORE_NAME = saved

    def test_render_body_greets_every_sequence_step(self):
        for i, mail in enumerate(market_engine.build_email_sequence(
                "keto snacks", self._bs_items())):
            body = mailer.render_body(mail, to_name="", email="sam@example.com")
            self.assertIn("Sam", body, "step %d" % i)
            self.assertIn("unsubscribe", body.lower(), "step %d" % i)

    def test_optin_widget_captures_first_name(self):
        html = seo.optin_html("keto snacks")
        self.assertIn('name="first_name"', html)

    # --------------------------------------------------------------- admin pages

    def test_admin_pages_redirect_unauth(self):
        for route in ("/admin/emails", "/admin/ebooks", "/admin/analytics",
                      "/admin/ebooks/pdf?keyword=keto%20snacks", "/api/subscribers"):
            st, location, _, _ = self._raw(route)
            status = 401 if route.startswith("/api/") else 302
            self.assertEqual(st, status, route)
            if status == 302:
                self.assertTrue(location.startswith("/admin/login"), (route, location))

    def test_admin_emails_page(self):
        self._subscribe("page@example.com")
        st, _, ctype, data = self._raw("/admin/emails", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("text/html"))
        html = data.decode("utf-8", "replace")
        self.assertIn("Send next batch", html)
        self.assertIn("/api/sequence/send", html)
        self.assertIn("page@example.com", html)
        self.assertIn("Sequence preview", html)
        self.assertIn("Email 1", html)

    def test_admin_ebooks_page_and_pdf(self):
        st, _, _, data = self._raw("/admin/ebooks?keyword=keto+snacks", cookie=self.cookie)
        self.assertEqual(st, 200)
        html = data.decode("utf-8", "replace")
        self.assertIn("Generate a guide", html)
        self.assertIn("Download PDF", html)
        st, _, ctype, pdf = self._raw(
            "/admin/ebooks/pdf?keyword=keto+snacks", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("application/pdf"))
        self.assertTrue(pdf.startswith(b"%PDF-"))

    def test_admin_ebooks_unknown_keyword_404(self):
        st, _, _, data = self._raw(
            "/admin/ebooks/pdf?keyword=no-such-niche", cookie=self.cookie)
        self.assertEqual(st, 404)

    def test_admin_manual_redirects_unauth(self):
        st, location, _, _ = self._raw("/admin/manual")
        self.assertEqual(st, 302)
        self.assertTrue(location.startswith("/admin/login"), location)

    def test_admin_manual_page_cross_links(self):
        st, _, ctype, data = self._raw("/admin/manual", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("text/html"))
        html = data.decode("utf-8", "replace")
        self.assertIn("User", html)
        self.assertIn("manual", html.lower())
        for tool in ("/dashboard", "/tool", "/keys", "/admin/emails",
                     "/admin/cms", "/admin/ebooks", "/admin/analytics",
                     "/admin/social", "/admin/sem", "/admin/seo",
                     "/admin/refresh",
                     "/admin/manual.pdf", "/n/air-fryer", "/lp/air-fryer"):
            self.assertIn(tool, html, tool)
        self.assertIn("Highest-form", html)
        self.assertIn("Landing pages", html)
        self.assertIn("Style templates in one click", html)

    def test_admin_manual_pdf_served(self):
        st, _, ctype, pdf = self._raw("/admin/manual.pdf", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("application/pdf"))
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))

    def test_ai_provider_panel_api(self):
        saved_url = ai._urlopen
        saved_runtime = dict(ai._RUNTIME)

        def fake(req):
            if getattr(req, "full_url", "").endswith("/models"):
                return {"data": [{"id": "m1"}, {"id": "m2"}]}
            return {"choices": [{"message": {"content": "pstore-ok"}}]}

        ai._urlopen = fake
        try:
            st, _, _, data = self._raw("/api/ai/providers", cookie=self.cookie)
            self.assertEqual(st, 200)
            names = [p["name"] for p in json.loads(data)["providers"]]
            self.assertEqual(names, ["openai", "opencode", "mistral", "nvidia"])
            st, _, _, data = self._raw(
                "/api/ai/test", "POST",
                body=json.dumps({"provider": "nvidia", "api_key": "nvapi-x",
                                 "model": "meta/llama-3.3-70b-instruct"}),
                cookie=self.cookie,
                headers={"Content-Type": "application/json"})
            self.assertEqual(st, 200)
            self.assertTrue(json.loads(data)["ok"])
            self.assertEqual(json.loads(data)["reply"], "pstore-ok")
            st, _, _, data = self._raw(
                "/api/ai/models", "POST",
                body=json.dumps({"provider": "opencode", "api_key": "oc-x"}),
                cookie=self.cookie,
                headers={"Content-Type": "application/json"})
            self.assertEqual(st, 200)
            self.assertEqual(json.loads(data)["models"], ["m1", "m2"])
            st, _, _, data = self._raw(
                "/api/ai/config", "POST",
                body=json.dumps({"provider": "mistral", "api_key": "mk-x",
                                 "model": "mistral-small-latest"}),
                cookie=self.cookie,
                headers={"Content-Type": "application/json"})
            self.assertEqual(st, 200)
            self.assertTrue(json.loads(data)["ok"])
            self.assertEqual(ai.active_provider(), "mistral")
            self.assertTrue(ai.configured())
        finally:
            ai._RUNTIME.clear()
            ai._RUNTIME.update(saved_runtime)
            ai._urlopen = saved_url

    def test_admin_analytics_page(self):
        self._raw("/api/track", "POST", body="slug=keto-snacks&source=niche")
        st, _, _, data = self._raw("/admin/analytics", cookie=self.cookie)
        self.assertEqual(st, 200)
        html = data.decode("utf-8", "replace")
        self.assertIn("analytics", html.lower())
        self.assertIn("keto-snacks", html)
        self.assertIn("privacy-first", html)

    def test_pageview_beacon_public_and_counted(self):
        self._raw("/api/pageview", "POST",
                  body="slug=keto-snacks&page=%2Fn%2Fketo-snacks&name=view&source=niche")
        self._raw("/api/pageview", "POST",
                  body="slug=keto-snacks&page=%2Flp%2Fketo-snacks&name=promo&source=niche")
        st, _, _, data = self._raw("/admin/analytics", cookie=self.cookie)
        self.assertEqual(st, 200)
        html = data.decode("utf-8", "replace")
        self.assertIn("Page views by URL", html)
        self.assertIn("/n/keto-snacks", html)
        self.assertIn("promo", html)
        self.assertIn("Lead-page interactions", html)

    def test_courier_js_tracks_views_and_interactions(self):
        st, _, _, data = self._raw("/courier.js")
        self.assertEqual(st, 200)
        js = data.decode("utf-8", "replace")
        self.assertIn("/api/pageview", js)
        self.assertIn("beacon(\"view\")", js)
        self.assertIn("data-ev", js)
        self.assertIn("_gated/pdf", js)

    # ---------------------------------------------------------- public surfaces

    def test_niche_page_has_optin_and_beacon(self):
        st, _, _, data = self._raw("/n/keto-snacks")
        self.assertEqual(st, 200)
        html = data.decode("utf-8", "replace")
        self.assertIn('class="courier card"', html)
        self.assertIn('data-niche="keto-snacks"', html)
        self.assertIn('/courier.js', html)

    def test_landing_has_optin_and_beacon(self):
        st, _, _, data = self._raw("/")
        self.assertEqual(st, 200)
        html = data.decode("utf-8", "replace")
        self.assertIn('class="courier card"', html)
        self.assertIn('data-niche="home"', html)
        self.assertIn('/courier.js', html)

    def test_courier_js_served_publicly(self):
        st, _, ctype, data = self._raw("/courier.js")
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("application/javascript"))
        self.assertIn("sendBeacon", data.decode("utf-8", "replace"))

    def test_email_sequence_has_unsubscribe_footer(self):
        items = self._bs_items()
        seq = market_engine.build_email_sequence("keto snacks", items)
        self.assertEqual(len(seq), 5)
        for mail in seq:
            body = mailer.render_body(mail, email="u@example.com")
            self.assertIn("unsubscribe", body.lower())
            self.assertTrue(mail["subject"])
            self.assertTrue(mailer.unsubscribe_url("u@example.com").startswith(
                "/unsubscribe?e=") or "unsubscribe" in mailer.unsubscribe_url("u@example.com"))


class TestEbookModule(unittest.TestCase):
    def test_build_ebook_without_ai(self):
        saved = ai.API_KEY
        ai.API_KEY = ""
        try:
            book = ebook.build_ebook("keto snacks")
            self.assertTrue(book["pdf"].startswith(b"%PDF-"))
            self.assertTrue(book["title"])
            self.assertGreaterEqual(len(book["chapters"]), 3)
            self.assertTrue(book["pdf_name"].endswith(".pdf"))
        finally:
            ai.API_KEY = saved

    def test_ai_fallback_is_offline(self):
        saved = ai._urlopen
        ai._urlopen = None
        ai.API_KEY = ""
        try:
            self.assertEqual(ai.generate("headline", "keto"), [])
            pair = ai.headline_and_subheadline("keto")
            self.assertTrue(pair["headline"] and pair["subheadline"])
        finally:
            ai._urlopen = saved


class TestAiProviders(unittest.TestCase):
    """Provider registry + runtime key tests (offline)."""

    def setUp(self):
        self._saved = {"API_KEY": ai.API_KEY, "BASE_URL": ai.BASE_URL, "MODEL": ai.MODEL}
        ai.API_KEY = ""
        ai.BASE_URL = "https://api.openai.com/v1"
        ai.MODEL = "gpt-4o-mini"
        self._env_saved = {}
        for k in ("AI_PROVIDER", "AI_BASE_URL", "AI_MODEL", "OPENCODE_API_KEY",
                  "MISTRAL_API_KEY", "NVIDIA_API_KEY", "OPENCODE_MODEL",
                  "MISTRAL_MODEL", "NVIDIA_MODEL"):
            if k in os.environ:
                self._env_saved[k] = os.environ[k]
                del os.environ[k]
        self._runtime = dict(ai._RUNTIME)
        ai._RUNTIME.clear()
        self._urlopen = ai._urlopen
        ai._urlopen = None
        self._env_keys = ("AI_PROVIDER", "AI_BASE_URL", "AI_MODEL", "OPENCODE_API_KEY",
                          "MISTRAL_API_KEY", "NVIDIA_API_KEY", "OPENCODE_MODEL",
                          "MISTRAL_MODEL", "NVIDIA_MODEL")

    def tearDown(self):
        ai._RUNTIME.clear()
        ai._RUNTIME.update(self._runtime)
        ai.API_KEY = self._saved["API_KEY"]
        ai.BASE_URL = self._saved["BASE_URL"]
        ai.MODEL = self._saved["MODEL"]
        for k in self._env_keys:
            if k in self._env_saved:
                os.environ[k] = self._env_saved[k]
            else:
                os.environ.pop(k, None)
        ai._urlopen = self._urlopen

    def test_no_keys_means_unconfigured(self):
        self.assertIsNone(ai.active_provider())
        self.assertFalse(ai.configured())
        self.assertEqual(ai.generate("headline", "keto"), [])

    def test_openai_env_is_autodetected(self):
        ai.API_KEY = "sk-test"
        self.assertEqual(ai.active_provider(), "openai")
        self.assertTrue(ai.configured())

    def test_other_provider_env_is_autodetected(self):
        os.environ["MISTRAL_API_KEY"] = "mk-test"
        self.assertEqual(ai.active_provider(), "mistral")
        self.assertTrue(ai.configured())

    def test_ai_provider_env_force(self):
        os.environ["MISTRAL_API_KEY"] = "mk-test"
        os.environ["AI_PROVIDER"] = "nvidia"
        self.assertIsNone(ai.active_provider())  # forced but no nvidia key
        os.environ["NVIDIA_API_KEY"] = "nv-test"
        self.assertEqual(ai.active_provider(), "nvidia")

    def test_runtime_key_wins_over_env(self):
        ai.API_KEY = "sk-test"
        out = ai.configure_runtime("mistral", "mk-test", "mistral-small-latest")
        self.assertTrue(out["ok"])
        self.assertEqual(ai.active_provider(), "mistral")
        self.assertEqual(ai.model_for("mistral"), "mistral-small-latest")

    def test_configured_with_runtime_only(self):
        ai.configure_runtime("opencode", "oc-x", "kimi-k2.5-free")
        self.assertEqual(ai.active_provider(), "opencode")
        self.assertTrue(ai.configured())
        self.assertEqual(ai.model_for("opencode"), "kimi-k2.5-free")
        self.assertIn("opencode.ai", ai.base_for("opencode"))

    def test_generate_uses_stubbed_provider(self):
        ai.API_KEY = "sk-test"
        ai._urlopen = lambda req: {"choices": [{"message": {"content": "Buy better keto bites\nTrust reviews"}}]}
        self.assertEqual(ai.generate("headline", "keto"),
                         ["Buy better keto bites", "Trust reviews"])

    def test_test_key_ok_and_failure(self):
        ai._urlopen = lambda req: {"choices": [{"message": {"content": "pstore-ok"}}]}
        r = ai.test("nvidia", "nvapi-x", "meta/llama-3.3-70b-instruct")
        self.assertTrue(r["ok"])
        self.assertEqual(r["reply"], "pstore-ok")
        self.assertIn("latency_ms", r)
        ai._urlopen = lambda req: (_ for _ in ()).throw(RuntimeError("401 unauthorized"))
        r = ai.test("openai", "sk-bad", "gpt-4o-mini")
        self.assertFalse(r["ok"])
        self.assertIn("401", r["error"])

    def test_list_models_parses(self):
        ai._urlopen = lambda req: {"data": [{"id": "b"}, {"id": "a"}]}
        self.assertEqual(ai.list_models("opencode", "oc-x"), ["a", "b"])
        ai._urlopen = lambda req: {"error": "bad"}
        self.assertEqual(ai.list_models("opencode", "oc-x"), [])


if __name__ == "__main__":
    unittest.main()
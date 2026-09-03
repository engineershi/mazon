# -*- coding: utf-8 -*-
"""Offline tests for the pstore social suite: UTM-tracked post kits, the
/og/<slug> share-card SVG, one-click publishing (+ optional webhook), the
/admin/social page and per-post click attribution via /api/track."""

import json
import os
import shutil
import sys
import threading
import unittest
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import indexnow
import mailer
import market_engine
import security
import server
import seo
import social


class _WebhookHandler(BaseHTTPRequestHandler):
    received = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        self.__class__.received.append(json.loads(raw))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


class TestSocialSuite(unittest.TestCase):
    """Boots a real HTTP server against a copied DB so kit generation, publish
    and attribution run against the same handlers pstore ships."""

    IPKEY = "127.0.0.1|127.0.0.1"

    @classmethod
    def setUpClass(cls):
        cls.db = "/tmp/pstore_test_social_%s.db" % uuid.uuid4().hex[:8]
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "pstore.db"), cls.db)
        os.environ["PSTORE_DB"] = cls.db
        os.environ["PSTORE_ADMIN_EMAIL"] = "owner@test.example"
        os.environ["PSTORE_ADMIN_PASSWORD"] = "test-pass-123"
        os.environ.pop("PSTORE_URL", None)
        os.environ.pop("SOCIAL_WEBHOOK", None)
        import importlib
        importlib.reload(server)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.PORT = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls._saved_indexnow_post = indexnow._post
        indexnow._post = cls._fake_indexnow_post
        cls.cookie = cls._login()

    @classmethod
    def _fake_indexnow_post(cls, url, payload, timeout=20):
        return None

    @classmethod
    def tearDownClass(cls):
        indexnow._post = cls._saved_indexnow_post
        security.SUBSCRIBE_LIMITER.clear("sub|" + cls.IPKEY)
        security.TRACK_LIMITER.clear("trk|" + cls.IPKEY)
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
            hdrs["Content-Type"] = "application/json"
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
            conn.execute("DELETE FROM clicks")
            conn.execute("DELETE FROM social_posts")
            conn.commit()
            conn.close()

    def _api(self, q="keto snacks"):
        st, _, ctype, data = self._raw("/api/social?keyword=" + urllib.parse.quote(q),
                                       cookie=self.cookie)
        self.assertEqual(st, 200)
        return json.loads(data)

    def test_social_api_requires_admin(self):
        st, _, _, data = self._raw("/api/social?keyword=keto+snacks")
        self.assertEqual(st, 401)
        self.assertIn(b"unauthorized", data)

    def test_admin_social_page_requires_login(self):
        st, location, _, _ = self._raw("/admin/social")
        self.assertEqual(st, 302)
        self.assertTrue(location.startswith("/admin/login"))

    def test_admin_social_page_renders_kits(self):
        st, _, _, data = self._raw("/admin/social", cookie=self.cookie)
        self.assertEqual(st, 200)
        html = data.decode("utf-8", "replace")
        for platform in social.PLATFORMS:
            self.assertIn(platform, html)
        self.assertIn("Publish", html)
        self.assertIn("/api/social/publish", html)
        self.assertIn("View live", html)
        self.assertIn("/lp/keto?utm_source=", html)

    def test_published_post_carries_live_link(self):
        st, _, _, data = self._raw(
            "/api/social/publish", "POST",
            body=json.dumps({"keyword": "keto snacks", "platform": "Twitter / X"}),
            cookie=self.cookie)
        self.assertEqual(st, 200)
        kit = json.loads(data)["posts"][0]
        st, _, _, page = self._raw("/admin/social?keyword=keto+snacks", cookie=self.cookie)
        html = page.decode("utf-8", "replace")
        self.assertIn("open ↗", html)
        self.assertIn(seo._clean(kit["link"]), html)
        st, _, _, api = self._raw(
            "/api/social?keyword=" + urllib.parse.quote("keto snacks"), cookie=self.cookie)
        res = json.loads(api)
        pub = next(p for p in res["published"] if p["utm_content"] == kit["utm_content"])
        self.assertIn("utm_source=", pub["link"])
        self.assertEqual(pub["link"], kit["link"])

    def test_kits_are_utm_tracked_to_landing(self):
        d = self._api()
        self.assertEqual(len(d["kits"]), len(social.PLATFORMS))
        for kit in d["kits"]:
            self.assertIn("utm_source=", kit["link"])
            self.assertIn("utm_campaign=keto-snacks", kit["link"])
            self.assertIn("/lp/keto-snacks?", kit["link"])
            self.assertTrue(kit["utm_content"])
            self.assertEqual(kit["target"], "landing")

    def test_publish_single_platform(self):
        d = self._api()
        core = social._ALPHABET.lower()

        def code_of(kit):
            return kit["link"].split("utm_content=")[-1]
        for kit in d["kits"]:
            self.assertTrue(all(ch in core for ch in code_of(kit)))
        st, _, ctype, data = self._raw(
            "/api/social/publish", "POST",
            body=json.dumps({"keyword": "keto snacks",
                             "platform": "Twitter / X"}),
            cookie=self.cookie)
        self.assertEqual(st, 200)
        res = json.loads(data)
        self.assertTrue(res["ok"])
        self.assertEqual(res["published"], 1)
        kit = res["posts"][0]
        self.assertTrue(kit["platform"], "Twitter / X")
        with server._lock:
            conn = server._db()
            row = conn.execute(
                "SELECT status FROM social_posts WHERE utm_content=?",
                (kit["utm_content"],)).fetchone()
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "published")

    def test_publish_all_platforms_idempotent(self):
        st, _, _, data = self._raw(
            "/api/social/publish", "POST",
            body=json.dumps({"keyword": "keto snacks", "platform": "all"}),
            cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(data)["published"], len(social.PLATFORMS))
        st, _, _, data2 = self._raw(
            "/api/social/publish", "POST",
            body=json.dumps({"keyword": "keto snacks", "platform": "all"}),
            cookie=self.cookie)
        self.assertEqual(st, 200)
        with server._lock:
            conn = server._db()
            n = conn.execute("SELECT COUNT(*) c FROM social_posts").fetchone()["c"]
            conn.close()
        self.assertEqual(n, len(social.PLATFORMS))

    def test_publish_unknown_platform_rejected(self):
        st, _, _, data = self._raw(
            "/api/social/publish", "POST",
            body=json.dumps({"keyword": "keto snacks", "platform": "Blogger"}),
            cookie=self.cookie)
        self.assertEqual(st, 400)
        self.assertIn(b"unknown platform", data)

    def test_webhook_fires_per_published_post(self):
        saved = os.environ.get("SOCIAL_WEBHOOK")
        handler = _WebhookHandler
        handler.received = []
        stub = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = stub.server_address[1]
        t = threading.Thread(target=stub.serve_forever, daemon=True)
        t.start()
        try:
            os.environ["SOCIAL_WEBHOOK"] = "http://127.0.0.1:%d/hook" % port
            st, _, _, data = self._raw(
                "/api/social/publish", "POST",
                body=json.dumps({"keyword": "keto snacks", "platform": "all"}),
                cookie=self.cookie)
            self.assertEqual(st, 200)
            self.assertTrue(json.loads(data)["ok"])
            deadline = 0
            while len(handler.received) < len(social.PLATFORMS) and deadline < 50:
                import time as _t
                _t.sleep(0.05)
                deadline += 1
            self.assertEqual(len(handler.received), len(social.PLATFORMS))
            first = handler.received[0]
            for key in ("body", "link", "platform"):
                self.assertIn(key, first)
        finally:
            if saved is None:
                os.environ.pop("SOCIAL_WEBHOOK", None)
            else:
                os.environ["SOCIAL_WEBHOOK"] = saved
            stub.shutdown()
            t.join(timeout=2)
            stub.server_close()

    def test_schedule_single_platform_spaced(self):
        st, _, ctype, data = self._raw(
            "/api/social/schedule", "POST",
            body=json.dumps({"keyword": "keto snacks", "platform": "all", "hours": 24}),
            cookie=self.cookie)
        self.assertEqual(st, 200)
        res = json.loads(data)
        self.assertTrue(res["ok"])
        self.assertEqual(res["scheduled"], len(social.PLATFORMS))
        self.assertEqual(res["platform"], "all")
        with server._lock:
            conn = server._db()
            rows = conn.execute(
                "SELECT platform, status, scheduled_at FROM social_posts "
                "WHERE slug=? AND status='scheduled' ORDER BY scheduled_at",
                (seo._slugify("keto snacks"),)).fetchall()
            conn.close()
        self.assertEqual(len(rows), len(social.PLATFORMS))
        ats = [r["scheduled_at"] for r in rows]
        self.assertEqual(sorted(ats), ats)          # spaced, not all at once
        self.assertEqual(set(r["status"] for r in rows), {"scheduled"})
        self.assertNotIn("", ats)

    def test_schedule_requires_auth(self):
        st, _, _, _ = self._raw(
            "/api/social/schedule", "POST",
            body=json.dumps({"keyword": "keto snacks", "platform": "all"}))
        self.assertEqual(st, 401)

    def test_flush_publishes_only_due_scheduled_posts(self):
        # schedule kit 1 of 'all' to a past time so only it is due
        st, _, _, data = self._raw(
            "/api/social/schedule", "POST",
            body=json.dumps({"keyword": "keto snacks", "platform": "all", "hours": 24}),
            cookie=self.cookie)
        self.assertEqual(st, 200)
        with server._lock:
            conn = server._db()
            kinfo = conn.execute(
                "SELECT id FROM social_posts WHERE status='scheduled' "
                "ORDER BY scheduled_at LIMIT 1").fetchone()
            k = conn.execute(
                "SELECT MIN(scheduled_at) AS least FROM social_posts "
                "WHERE status='scheduled'").fetchone()["least"]
            conn.execute("UPDATE social_posts SET scheduled_at=? WHERE id=?",
                         ("2000-01-01 00:00:00", kinfo["id"]))
            conn.commit()
            conn.close()
        st, _, _, data = self._raw(
            "/api/social/flush", "POST", body=b"{}",
            headers={"Content-Type": "application/json"}, cookie=self.cookie)
        self.assertEqual(st, 200)
        res = json.loads(data)
        self.assertEqual(res["published_now"], 1)
        with server._lock:
            conn = server._db()
            due = conn.execute(
                "SELECT status FROM social_posts WHERE id=?",
                (kinfo["id"],)).fetchone()["status"]
            still = conn.execute(
                "SELECT COUNT(*) AS n FROM social_posts WHERE status='scheduled'"
            ).fetchone()["n"]
            conn.close()
        self.assertEqual(due, "published")
        self.assertEqual(res["still_pending"], still)   # the rest remain queued

    def test_auto_flush_module_function_due_only(self):
        # queue via the API, then force one post past-due and call the
        # handler-free module function (the timer path) with a captured hook.
        st, _, _, data = self._raw(
            "/api/social/schedule", "POST",
            body=json.dumps({"keyword": "keto snacks", "platform": "all", "hours": 24}),
            cookie=self.cookie)
        self.assertEqual(st, 200)
        with server._lock:
            conn = server._db()
            kinfo = conn.execute(
                "SELECT id FROM social_posts WHERE status='scheduled' "
                "ORDER BY scheduled_at LIMIT 1").fetchone()
            conn.execute("UPDATE social_posts SET scheduled_at='2000-01-01 00:00:00' "
                         "WHERE id=?", (kinfo["id"],))
            conn.commit()
            conn.close()
        fired = []
        n, pending = server._flush_due_social(lambda kits: fired.extend(kits))
        self.assertEqual(n, 1)
        self.assertEqual(len(fired), 1)
        self.assertTrue(fired[0]["platform"])
        self.assertTrue(fired[0]["body"])
        with server._lock:
            conn = server._db()
            st2 = conn.execute("SELECT status FROM social_posts WHERE id=?",
                               (kinfo["id"],)).fetchone()["status"]
            conn.close()
        self.assertEqual(st2, "published")

    def test_og_image_served_for_saved_niche(self):
        st, _, ctype, data = self._raw("/og/keto-snacks")
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("image/svg+xml"))
        self.assertTrue(data.lstrip().startswith(b"<svg"))

    def test_og_image_404_unknown_slug(self):
        st, _, _, _ = self._raw("/og/not-a-real-niche")
        self.assertEqual(st, 404)

    def test_niche_page_points_og_at_generated_card(self):
        st, _, _, data = self._raw("/n/keto-snacks")
        self.assertEqual(st, 200)
        self.assertIn(b'property="og:image"', data)
        self.assertIn(b"/og/keto-snacks", data)

    def test_landing_page_carries_beacon_and_og(self):
        st, _, _, data = self._raw("/lp/keto-snacks")
        self.assertEqual(st, 200)
        html = data.decode("utf-8", "replace")
        self.assertIn('data-niche="keto-snacks"', html)
        self.assertIn('data-source="landing"', html)
        self.assertIn("/courier.js", html)
        self.assertIn("data-asin=", html)
        self.assertIn('property="og:image"', html)

    def test_track_stores_utm_content_for_attribution(self):
        with server._lock:
            conn = server._db()
            conn.execute("DELETE FROM clicks")
            conn.commit()
            conn.close()
        st, _, _, _ = self._raw(
            "/api/track", "POST",
            body=json.dumps({"slug": "keto-snacks", "source": "twitter",
                             "content": "abc123", "asin": "B0KETO1234"}))
        self.assertEqual(st, 200)
        with server._lock:
            conn = server._db()
            row = conn.execute(
                "SELECT source, content FROM clicks ORDER BY id DESC LIMIT 1").fetchone()
            conn.close()
        self.assertEqual(row["source"], "twitter")
        self.assertEqual(row["content"], "abc123")

    def test_track_utm_get_fallback(self):
        st, _, _, _ = self._raw(
            "/api/track?slug=keto-snacks&source=facebook&content=x1y2&asin=B0KETO1234")
        self.assertEqual(st, 200)
        with server._lock:
            conn = server._db()
            row = conn.execute(
                "SELECT source, content FROM clicks ORDER BY id DESC LIMIT 1").fetchone()
            conn.close()
        self.assertEqual(row["content"], "x1y2")

    def test_published_post_shows_click_count(self):
        self._raw("/api/social/publish", "POST",
                  body=json.dumps({"keyword": "keto snacks", "platform": "Twitter / X"}),
                  cookie=self.cookie)
        d = self._api()
        code = d["kits"][0]["utm_content"]
        self._raw("/api/track", "POST",
                  body=json.dumps({"slug": "keto-snacks", "source": "twitter",
                                   "content": code}))
        self._raw("/api/track", "POST",
                  body=json.dumps({"slug": "keto-snacks", "source": "twitter",
                                   "content": code}))
        d2 = self._api()
        self.assertEqual(d2["stats"].get(code), 2)
        for p in d2["published"]:
            self.assertEqual(p["status"], "published")

    def test_workbench_includes_tracked_kits(self):
        st, _, _, data = self._raw(
            "/api/tools?keyword=" + urllib.parse.quote("keto snacks"), cookie=self.cookie)
        self.assertEqual(st, 200)
        payload = json.loads(data)
        self.assertEqual(len(payload["social_kit"]), len(social.PLATFORMS))
        for kit in payload["social_kit"]:
            self.assertIn("utm_source=", kit["link"])

    def test_social_generation_needs_top_pick(self):
        kits = social.post_kits("empty niche", [], base_url="http://x.example")
        self.assertEqual(kits, [])

    def test_socialwebhook_not_configured_is_honest(self):
        os.environ.pop("SOCIAL_WEBHOOK", None)
        st, _, _, data = self._raw(
            "/api/social/publish", "POST",
            body=json.dumps({"keyword": "keto snacks", "platform": "Facebook"}),
            cookie=self.cookie)
        res = json.loads(data)
        self.assertEqual(st, 200)
        self.assertFalse(res.get("webhook"))

    def test_auto_amplify_requeues_winner_to_scheduled(self):
        # seed a published post with clicks, old enough to be re-amplified
        import datetime as _dt
        with server._lock:
            c = server._db()
            c.execute(
                "INSERT INTO social_posts (slug, keyword, platform, name, body, link, "
                "utm_content, status, published_at) VALUES ('keto-snacks','keto snacks',"
                "'Twitter','win','b','http://x/lp/keto','code-amp','published','2020-01-01 08:00:00')")
            pid = c.execute("SELECT last_insert_rowid() id").fetchone()["id"]
            c.commit()
            c.close()
        with server._lock:
            c = server._db()
            c.execute("INSERT INTO clicks (slug, source, ip, referrer, asin, content) "
                      "VALUES ('keto-snacks','social','t','','','code-amp')")
            c.commit()
            c.close()
        now = _dt.datetime(2020, 1, 3, 12, 0, 0)  # 2 days after publish
        res = server._auto_amplify_winners(now=now)
        self.assertTrue(res["on"])
        self.assertEqual(res["requeued"], 1)
        self.assertEqual(res["winners"][0]["slug"], "keto-snacks")
        self.assertEqual(res["winners"][0]["amp"], 1)
        with server._lock:
            c = server._db()
            row = c.execute("SELECT id,status,scheduled_at,amplify_count FROM social_posts "
                            "WHERE id=?", (pid,)).fetchone()
            c.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "scheduled")
        self.assertIsNotNone(row["scheduled_at"])
        self.assertEqual(row["amplify_count"], 1)

    def test_auto_amplify_caps_runs(self):
        # a post already amplified to max_runs is not requeued again
        import datetime as _dt
        server._set_setting("social.amplify.max_runs", "1")
        server._set_setting("social.amplify.min_age_hours", "24")
        try:
            with server._lock:
                c = server._db()
                c.execute(
                    "INSERT INTO social_posts (slug, keyword, platform, name, body, link, "
                    "utm_content, status, published_at, amplify_count) VALUES "
                    "('keto-snacks','keto snacks','Twitter','win','b','http://x/lp/keto',"
                    "'code-cap','published','2020-01-01 08:00:00',1)")
                pid = c.execute("SELECT last_insert_rowid() id").fetchone()["id"]
                c.commit()
                c.close()
            with server._lock:
                c = server._db()
                c.execute("INSERT INTO clicks (slug, source, ip, referrer, asin, content) "
                          "VALUES ('keto-snacks','social','t','','','code-cap')")
                c.commit()
                c.close()
            now = _dt.datetime(2020, 1, 3, 12, 0, 0)
            res = server._auto_amplify_winners(now=now)
            self.assertEqual(res["requeued"], 0)
            with server._lock:
                c = server._db()
                row = c.execute("SELECT status,amplify_count FROM social_posts WHERE id=?",
                                (pid,)).fetchone()
                c.close()
            self.assertEqual(row["status"], "published")
            self.assertEqual(row["amplify_count"], 1)
        finally:
            server._set_setting("social.amplify.max_runs", "2")
            server._set_setting("social.amplify.min_age_hours", "24")


if __name__ == "__main__":
    unittest.main()
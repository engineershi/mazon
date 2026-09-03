# -*- coding: utf-8 -*-
"""Offline tests for the aggressive social-marketing stream: post-performance
ranking, long-tail topic recycling, peak-slot scheduling and per-kit share image."""

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

import mailer
import indexnow
import publish
import server
import social


class TestSocialAggressive(unittest.TestCase):
    """Boots a real server against a copied DB (stubbed SMTP / indexnow)."""

    @classmethod
    def setUpClass(cls):
        cls.db = "/tmp/pstore_social_%s.db" % uuid.uuid4().hex[:8]
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "pstore.db"), cls.db)
        os.environ["PSTORE_DB"] = cls.db
        os.environ["PSTORE_ADMIN_EMAIL"] = "o@test.example"
        os.environ["PSTORE_ADMIN_PASSWORD"] = "pw"
        os.environ.pop("PSTORE_URL", None)
        import importlib
        importlib.reload(server)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.PORT = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.cookie = cls._login()

    @classmethod
    def _login(cls):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=8)
        c.request("POST", "/admin/login",
                  body=b"email=o@test.example&password=pw",
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse()
        sc = r.getheader("Set-Cookie")
        r.read()
        c.close()
        return sc.split(";")[0]

    @classmethod
    def _raw(cls, path, method="GET", body=None, cookie=None, headers=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=8)
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = cookie
        if body is not None:
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        out = (r.status, r.getheader("Location"), r.getheader("Content-Type"),
               r.read())
        c.close()
        return out

    def setUp(self):
        with server._lock:
            conn = server._db()
            conn.execute("DELETE FROM social_posts")
            conn.execute("DELETE FROM clicks")
            conn.commit()
            conn.close()
        self._h = server.Handler.__new__(server.Handler)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        cls.httpd.server_close()
        if os.path.exists(cls.db):
            os.unlink(cls.db)

    # ------------------------------------------------------------ ranking loop

    def test_ranked_posts_tags_winners_and_cold(self):
        published = [
            {"utm_content": "AAA", "platform": "X", "name": "a", "slug": "s"},
            {"utm_content": "BBB", "platform": "FB", "name": "b", "slug": "s"},
            {"utm_content": "CCC", "platform": "P", "name": "c", "slug": "s"},
        ]
        stats = {"AAA": 40, "BBB": 0, "CCC": 25}
        ranked = self._h._ranked_posts(published, stats)
        self.assertEqual([(r[0], r[1], r[2]["utm_content"]) for r in ranked],
                         [(40, "winner", "AAA"), (25, "warm", "CCC"), (0, "cold", "BBB")])

    def test_ranked_posts_sort_and_zeros(self):
        published = [
            {"utm_content": "A", "platform": "X", "slug": "s"},
            {"utm_content": "B", "platform": "IG", "slug": "s"},
        ]
        ranked = self._h._ranked_posts(published, {"A": 0, "B": 3})
        self.assertEqual(ranked[0][0], 3)
        self.assertIn(ranked[0][2]["utm_content"], ("B",))
        unchanged = self._h._ranked_posts(published, {})
        self.assertEqual(len(unchanged), 2)

    # -------------------------------------------------- long-tail topic recycling

    def test_social_topics_requires_auth(self):
        st, _, _, _ = self._raw("/api/social/topics", "POST", body=b"{}")
        self.assertEqual(st, 401)

    def test_topic_kits_are_umt_to_longtail_page(self):
        kits = social.topic_post_kits(
            "best keto chips", "keto snacks",
            [{"asin": "B0KETO1", "title": "Keto Chips", "reviews": 10,
              "stars": 4.6, "price": 8.99, "currency": "USD"}],
            "https://p.example", parent_slug="keto-snacks", slug="best-keto-chips")
        self.assertEqual(len(kits), len(social.PLATFORMS))
        kit = kits[0]
        self.assertTrue(kit["target"] == "topic")
        self.assertIn("best-keto-chips", kit["link"])
        self.assertIn("utm_source=", kit["link"])
        self.assertTrue(kit["image"].endswith("/og/keto-snacks"))
        self.assertIn("best keto chips", kit.get("body", "").lower())
        self.assertTrue(kit.get("body"))

    def test_social_topics_recycle_schedules(self):
        st, _, _, data = self._raw("/api/social/topics", "POST",
                                   body=json.dumps({"schedule": True, "hours": 48}),
                                   cookie=self.cookie)
        self.assertEqual(st, 200)
        payload = json.loads(data)
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["kits_built"], 0)
        self.assertEqual(set(payload.keys()),
                         {"ok", "kits_built", "scheduled", "niches"})

    # ---------------------------------------------------- peak-slot scheduling

    def test_schedule_times_snap_to_peak_slots(self):
        import datetime
        fixed = datetime.datetime(2026, 9, 3, 9, 0, 0)  # 09:00 UTC: peaks ahead
        times = self._h._schedule_times(4, hours=24, now=fixed)
        self.assertEqual(len(times), 4)
        for t in times:
            hour = int(t.split(" ")[1].split(":")[0])
            self.assertIn(hour, server.SOCIAL_PEAK_SLOTS, t)

    def test_social_topic_kits_distinct_codes(self):
        items = [{"asin": "B0KETO2", "title": "Keto Bars", "reviews": 5,
                  "stars": 4.2, "price": 12.0, "currency": "USD"}]
        kits = social.topic_post_kits("keto bars", "keto snacks", items,
                                      "https://p.example", parent_slug="keto-snacks",
                                      slug="keto-bars")
        codes = [k["utm_content"] for k in kits]
        self.assertEqual(len(codes), len(set(codes)), "each kit needs its own UTM code")

    # -------------------------------------------------------------- og image

    def test_og_image_url(self):
        self.assertEqual(social.og_image_url("https://p.example", "keto-snacks"),
                         "https://p.example/og/keto-snacks")


if __name__ == "__main__":
    unittest.main()
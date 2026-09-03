# -*- coding: utf-8 -*-
"""Offline tests for the aggressive search-engine stream: SERP snippet preview,
long-tail topic auditing and the /api/seo/topics endpoint."""

import json
import os
import shutil
import sys
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import seo
import server


class TestSeoAggressive(unittest.TestCase):
    """Boots a real server against a copied DB to exercise the SEO endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.db = "/tmp/pstore_seo_%s.db" % uuid.uuid4().hex[:8]
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
    def _raw(cls, path, method="GET", body=None, cookie=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=8)
        h = {}
        if cookie:
            h["Cookie"] = cookie
        if body is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        out = (r.status, r.getheader("Location"), r.getheader("Content-Type"),
               r.read())
        c.close()
        return out

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        cls.httpd.server_close()
        if os.path.exists(cls.db):
            os.unlink(cls.db)

    # -------------------------------------------------------------- snippet

    def test_render_snippet_truncates_meta(self):
        svg = seo.render_snippet("a very long keyword that goes on beyond sixty"
                                 " characters to force a truncation point here",
                                 desc="x" * 250)
        self.assertTrue(svg.startswith(b"<svg"))
        self.assertIn(b"\xe2\x80\xa6", svg)  # ellipsis => truncation ran
        self.assertIn(b"sample", svg)

    def test_render_snippet_escapes_html(self):
        svg = seo.render_snippet('"><img src=x onerror=alert(1)>')
        self.assertNotIn(b"<img", svg)

    def test_seo_snippet_route_serves_svg(self):
        st, _, ctype, data = self._raw("/seo/snippet/keto-snacks", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("image/svg+xml"))
        self.assertTrue(data.startswith(b"<svg"))

    def test_seo_snippet_404_unknown(self):
        st, _, _, _ = self._raw("/seo/snippet/not-a-niche")
        self.assertEqual(st, 404)

    # ------------------------------------------------------- topic audit

    def test_audit_topic_mirrors_niche_audit(self):
        row = seo.audit_topic("best keto chips", "keto snacks",
                              [{"asin": "B0X", "title": "t", "reviews": 1}])
        self.assertEqual(row["url"], "/n/keto-snacks/best-keto-chips")
        self.assertIn("title_ok", row["checks"])
        self.assertIn("indexable", row)
        self.assertTrue(row["checks"]["has_products"])

    def test_seo_topics_api_requires_auth(self):
        st, _, _, _ = self._raw("/api/seo/topics")
        self.assertEqual(st, 401)

    def test_seo_topics_api_payload_shape(self):
        st, _, _, data = self._raw("/api/seo/topics", cookie=self.cookie)
        self.assertEqual(st, 200)
        payload = json.loads(data)
        self.assertEqual(set(payload.keys()),
                         {"topics", "count", "indexable", "needs_work"})
        self.assertIsInstance(payload["topics"], list)
        self.assertGreaterEqual(payload["count"], 0)

    # ------------------------------------------------- marketing ROI

    def test_marketing_api_requires_auth(self):
        st, _, _, _ = self._raw("/api/marketing")
        self.assertEqual(st, 401)

    def test_marketing_payload_shape(self):
        st, _, _, data = self._raw("/api/marketing", cookie=self.cookie)
        self.assertEqual(st, 200)
        p = json.loads(data)
        self.assertEqual(set(p.keys()),
                         {"email", "social", "traffic", "content",
                          "demography", "recommendations"})
        self.assertEqual(set(p["email"]),
                         {"confirmed", "unsubscribed", "sent", "opens",
                          "open_rate", "clicks", "click_rate",
                          "sequence_done", "sequence_length"})
        self.assertIsInstance(p["recommendations"], list)

    def test_marketing_rates_are_finite(self):
        st, _, _, data = self._raw("/api/marketing", cookie=self.cookie)
        p = json.loads(data)
        self.assertGreaterEqual(p["email"]["open_rate"], 0.0)
        self.assertLessEqual(p["email"]["open_rate"], 100.0)
        self.assertEqual(p["content"]["niches"], p["content"]["niches"])  # exists

    def test_admin_marketing_page_serves(self):
        st, _, ctype, data = self._raw("/admin/marketing", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("text/html"))
        html = data.decode("utf-8", "replace")
        self.assertIn("Digital marketing", html)
        self.assertIn("Next best action", html)
        self.assertIn("Market demography", html)
        self.assertIn("demofrm", html)

    def test_suggest_api_requires_auth(self):
        st, _, _, _ = self._raw("/api/suggest")
        self.assertEqual(st, 401)

    def test_admin_marketing_has_auto_suggestion_card(self):
        st, _, _, data = self._raw("/admin/marketing", cookie=self.cookie)
        html = data.decode("utf-8", "replace")
        self.assertIn("Auto niche suggestions", html)
        self.assertIn("loadSuggest()", html)
        self.assertIn("/api/suggest", html)

    # -------------------------------------------------- real sales funnel

    def test_funnel_api_requires_auth(self):
        st, _, _, _ = self._raw("/api/funnel")
        self.assertEqual(st, 401)

    def test_funnel_payload_shape(self):
        st, _, _, data = self._raw("/api/funnel", cookie=self.cookie)
        self.assertEqual(st, 200)
        p = json.loads(data)
        self.assertEqual(set(p.keys()), {"stages", "leak", "stats",
                                         "recommendations", "by_niche"})
        self.assertEqual(len(p["stages"]), 5)
        self.assertEqual([s["n"] for s in p["stages"]], [1, 2, 3, 4, 5])
        # each stage has a value + conversion to next stage
        for s in p["stages"]:
            self.assertIn("value", s)
            self.assertIn("conv", s)
        # Earn stage draws real + estimated revenue from clicks
        self.assertIn("commission_est", p["stats"])
        self.assertIn("channels", p["stats"])
        self.assertIn("real_earnings", p["stats"])
        # leak lost >= 0 when present
        if p["leak"]:
            self.assertGreaterEqual(p["leak"]["lost"], 0)

    def test_admin_funnel_page_serves(self):
        st, _, ctype, data = self._raw("/admin/funnel", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("text/html"))
        html = data.decode("utf-8", "replace")
        self.assertIn("sales funnel", html.lower())
        self.assertIn("fstage", html)

    def test_admin_nav_is_sectioned(self):
        st, _, _, data = self._raw("/admin/marketing", cookie=self.cookie)
        html = data.decode("utf-8", "replace")
        for label in ("Find", "Build", "Market", "Analyze", "Operate"):
            self.assertIn('class="navgroup titles">%s</div>' % label, html)
        self.assertIn("/admin/funnel", html)

    # ---------------------------------------------------- demography settings

    def test_settings_exposes_demography(self):
        st, _, _, data = self._raw("/api/settings", cookie=self.cookie)
        self.assertEqual(st, 200)
        s = json.loads(data)
        self.assertIn("demography", s)
        self.assertEqual(set(s["demography"]),
                         {"region", "interest", "interests_extra", "behavior",
                          "age", "audience", "income", "tone"})

    def test_save_demography(self):
        payload = json.dumps({"demography": {
            "region": "United States", "interest": "Fashion",
            "age": "18-34", "tone": "upbeat"}}).encode()
        st, _, _, _ = self._raw("/api/settings", method="POST",
                                body=payload, cookie=self.cookie)
        self.assertEqual(st, 200)
        st, _, _, data = self._raw("/api/settings", cookie=self.cookie)
        d = json.loads(data)["demography"]
        self.assertEqual(d["interest"], "Fashion")
        self.assertEqual(d["region"], "United States")
        self.assertEqual(d["tone"], "upbeat")

    def test_marketing_payload_includes_demography(self):
        payload = json.dumps({"demography": {"interest": "Fashion"}}).encode()
        self._raw("/api/settings", method="POST", body=payload,
                  cookie=self.cookie)
        st, _, _, data = self._raw("/api/marketing", cookie=self.cookie)
        self.assertEqual(st, 200)
        p = json.loads(data)
        self.assertEqual(p["demography"]["interest"], "Fashion")
        self.assertTrue(any("Audience is set" in r for r in p["recommendations"]))


if __name__ == "__main__":
    unittest.main()
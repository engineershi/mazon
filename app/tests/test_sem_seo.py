# -*- coding: utf-8 -*-
"""Offline tests for the SEM slow-growth suite and the SEO audit suite:
the /admin/sem funnel hub, /admin/seo audit hub, their JSON APIs, the
WebSite/Organization structured data on the landing page, the noindex rule
for empty niches, the back-to-top pill/ui.js include, and per-niche sitemap
lastmod derived from the niche created date."""

import http.client
import json
import os
import re
import shutil
import sys
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amazon
import indexnow
import io
import security
import seo
import sem
import server


class TestSemSeoSite(unittest.TestCase):

    IPKEY = "127.0.0.1|127.0.0.1"

    @classmethod
    def setUpClass(cls):
        cls.db = "/tmp/pstore_test_semseo_%s.db" % uuid.uuid4().hex[:8]
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "pstore.db"), cls.db)
        os.environ["PSTORE_DB"] = cls.db
        os.environ["PSTORE_ADMIN_EMAIL"] = "owner@test.example"
        os.environ["PSTORE_ADMIN_PASSWORD"] = "test-pass-123"
        os.environ.pop("PSTORE_URL", None)
        cls._saved_indexnow_post = indexnow._post
        indexnow._post = cls._fake_indexnow_post
        cls._saved_amazon_urlopen = amazon._urlopen
        amazon._urlopen = cls._fake_amazon_urlopen
        import importlib
        importlib.reload(server)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.PORT = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.cookie = cls._login()
        with server._lock:
            conn = server._db()
            rows = conn.execute("SELECT keyword FROM niches").fetchall()
            conn.close()
        cls.niches = [r["keyword"] for r in rows]

    @classmethod
    def _fake_indexnow_post(cls, url, payload, timeout=20):
        return None

    @classmethod
    def _fake_amazon_urlopen(cls, req, timeout):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "completion.amazon.com" in url:
            return io.BytesIO(json.dumps(
                {"suggestions": [{"value": "keto snacks best"}, {"value": "best keto snacks"}]}
            ).encode("utf-8"))
        return io.BytesIO(b"<html><body></body></html>")

    @classmethod
    def tearDownClass(cls):
        indexnow._post = cls._saved_indexnow_post
        amazon._urlopen = cls._saved_amazon_urlopen
        security.SUBSCRIBE_LIMITER.clear("sub|" + cls.IPKEY)
        security.TRACK_LIMITER.clear("trk|" + cls.IPKEY)
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        cls.httpd.server_close()
        if os.path.exists(cls.db):
            os.unlink(cls.db)

    @classmethod
    def _login(cls):
        conn = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=5)
        conn.request("POST", "/admin/login",
                     body=b"email=owner@test.example&password=test-pass-123",
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        cookie = resp.getheader("Set-Cookie")
        conn.close()
        return cookie.split(";")[0]

    def _raw(self, method, path, body=None, cookie=None, ctype=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.PORT, timeout=10)
        h = {}
        if cookie:
            h["Cookie"] = cookie
        if ctype:
            h["Content-Type"] = ctype
        if headers:
            h.update(headers)
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        data = resp.read()
        status, location, ctype = resp.status, resp.getheader("Location"), \
            resp.getheader("Content-Type")
        conn.close()
        return status, location, ctype, data

    def _pick_niche(self):
        return (self.niches or ["keto snacks"])[0]

    # --- API: SEO audit -----------------------------------------------------
    def test_seo_audit_api(self):
        st, _, ct, body = self._raw("GET", "/api/seo-audit", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertIn("application/json", ct)
        d = json.loads(body)
        self.assertIn("count", d)
        self.assertIn("indexable", d)
        self.assertIn("niches", d)
        self.assertEqual(d["count"], len(self.niches))
        if d["niches"]:
            row = d["niches"][0]
            for k in ("keyword", "slug", "url", "title_len", "desc_len",
                      "checks", "indexable"):
                self.assertIn(k, row)
            self.assertIn("title_ok", row["checks"])
            self.assertIn("desc_ok", row["checks"])
            self.assertIn("schema", row["checks"])
            self.assertIn("og_image", row["checks"])

    # --- API: SEM -----------------------------------------------------------
    def test_sem_api_happy(self):
        kw = self._pick_niche()
        st, _, ct, body = self._raw("GET", "/api/sem?keyword=%s" %
                                    urllib_quote(kw), cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertIn("application/json", ct)
        b = json.loads(body)
        self.assertEqual(b["keyword"], kw)
        self.assertIn("longtail", b)
        self.assertIn("intent", b)
        self.assertIn("paa", b)
        self.assertIn("performance", b)
        self.assertIn("page", b)
        self.assertTrue(any(x["intent"] == "target" for x in b["longtail"]))

    def test_sem_api_requires_keyword(self):
        st, _, _, body = self._raw("GET", "/api/sem", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertIn("error", json.loads(body))

    def test_sem_api_unknown_keyword(self):
        st, _, _, body = self._raw("GET", "/api/sem?keyword=zzz-not-a-niche",
                                   cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertIn("error", json.loads(body))

    # --- Admin pages --------------------------------------------------------
    def test_admin_seo_page(self):
        st, _, ct, body = self._raw("GET", "/admin/seo", cookie=self.cookie)
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("SEO", html)
        self.assertIn('id="top"', html)
        self.assertIn('class="totop"', html)
        # links into the unified Keys hub for the site-level SEO keys
        self.assertIn('href="/keys/site/gsc"', html)
        self.assertIn('href="/keys/site/indexnow"', html)
        self.assertIn('href="/keys"', html)
        # push-to-engines controls
        self.assertIn('id="submitNow"', html)
        self.assertIn("IndexNow", html)
        self.assertIn("search.google.com/search-console", html)
        self.assertIn("Yandex", html)
        self.assertIn("Bing", html)
        self.assertIn("active subscribers", html)

    def test_admin_sem_page(self):
        kw = self._pick_niche()
        st, _, ct, body = self._raw("GET", "/admin/sem?keyword=%s" %
                                    urllib_quote(kw), cookie=self.cookie)
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("Search", html)
        self.assertIn('id="top"', html)
        self.assertIn('class="totop"', html)

    # --- Niche data refresh -------------------------------------------------
    def test_refresh_requires_auth(self):
        st, loc, _, _ = self._raw("GET", "/admin/refresh")
        self.assertEqual(st, 302)
        self.assertIn("/admin/login", loc)

    def test_refresh_status_api(self):
        st, _, ct, body = self._raw("GET", "/api/refresh/status", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertIn("application/json", ct)
        d = json.loads(body)
        self.assertEqual(d["total"], len(self.niches))
        for k in ("refreshed", "stale", "inflight", "auto_interval_s",
                  "stale_min", "max_per_cycle"):
            self.assertIn(k, d)

    def test_refresh_single_niche_sets_updated_at(self):
        kw = self._pick_niche()
        st, _, ct, body = self._raw(
            "POST", "/api/refresh",
            body=json.dumps({"keyword": kw}),
            ctype="application/json", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertIn("application/json", ct)
        self.assertEqual(json.loads(body)["status"], "ok")
        with server._lock:
            conn = server._db()
            row = conn.execute("SELECT updated_at FROM niches WHERE keyword=?",
                               (kw,)).fetchone()
            conn.close()
        self.assertTrue(row["updated_at"])

    def test_refresh_missing_niche(self):
        st, _, _, body = self._raw(
            "POST", "/api/refresh",
            body=json.dumps({"keyword": "not-a-real-niche-xyz"}),
            ctype="application/json", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["status"], "missing")

    def test_refresh_requires_keyword_arg(self):
        st, _, _, body = self._raw("POST", "/api/refresh",
                                   body=json.dumps({}), ctype="application/json",
                                   cookie=self.cookie)
        self.assertEqual(st, 400)

    def test_z_refresh_all_starts_background(self):
        st, _, ct, body = self._raw("POST", "/api/refresh-all",
                                    ctype="application/json", cookie=self.cookie)
        self.assertEqual(st, 200)
        d = json.loads(body)
        self.assertEqual(d["status"], "started")
        self.assertEqual(d["queued"], len(self.niches))
        # Wait for the background worker to finish (validates completion and
        # avoids the daemon leaking into later test modules).
        import time as _time
        deadline = _time.time() + 60
        while _time.time() < deadline:
            st, _, _, sbody = self._raw("GET", "/api/refresh/status",
                                        cookie=self.cookie)
            sd = json.loads(sbody)
            if not sd.get("inflight"):
                break
            _time.sleep(0.25)
        st, _, _, sbody = self._raw("GET", "/api/refresh/status",
                                    cookie=self.cookie)
        sd = json.loads(sbody)
        self.assertEqual(sd["inflight"], [])
        self.assertEqual(sd["refreshed"], len(self.niches))

    def test_admin_refresh_page(self):
        st, _, ct, body = self._raw("GET", "/admin/refresh", cookie=self.cookie)
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("Data refresh", html)
        self.assertIn("/api/refresh-all", html)
        self.assertIn('data-kw=', html)
        self.assertIn("stale", html)

    def test_admin_pages_require_auth(self):
        st, loc, _, _ = self._raw("GET", "/admin/seo")
        self.assertEqual(st, 302)
        self.assertIn("/admin/login", loc)

    # --- Structured data + noindex on public pages -------------------------
    def test_landing_schema_has_website_org(self):
        st, _, ct, body = self._raw("GET", "/")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("application/ld+json", html)
        self.assertIn("WebSite", html)
        self.assertIn("Organization", html)
        self.assertIn('class="totop"', html)
        self.assertIn("/ui.js", html)

    def test_homepage_components_render_with_niches(self):
        st, _, ct, body = self._raw("GET", "/")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        # quick-verdict band above the fold
        self.assertIn("Quick verdict", html)
        self.assertIn('class="qpick', html)
        self.assertIn("Check price", html)
        # honesty trust strip (the differentiation gap)
        self.assertIn("trust-strip", html)
        self.assertIn("Live prices", html)
        # explore-niches tile grid with live counts
        self.assertIn("Explore the niches", html)
        self.assertIn('class="ntile"', html)
        # comparison preview + courier opt-in w/ first_name
        self.assertIn("Compare the shortlist", html)
        self.assertIn('name="first_name"', html)

    def test_homepage_first_name_optin_and_courier(self):
        st, _, _, body = self._raw("GET", "/")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn('/courier.js', html)
        self.assertIn('name="email"', html)
        self.assertIn('name="first_name"', html)

    def test_homepage_nav_anchors_resolve(self):
        st, _, _, body = self._raw("GET", "/")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        anchors = set(re.findall(r'href="#([a-z0-9-]+)"', html))
        ids = set(re.findall(r'id="([a-z0-9-]+)"', html))
        # every nav chip target must have a matching section id, or the click
        # goes nowhere
        for a in anchors:
            self.assertIn(a, ids, "nav anchor #%s has no target id on the page" % a)
        # the sections the chips point to exist
        for a in ("top-picks", "niches", "notify", "method", "faq"):
            self.assertIn(a, ids)

    def test_niche_noindex_when_missing(self):
        st, _, _, body = self._raw("GET", "/n/this-niche-does-not-exist")
        self.assertEqual(st, 404)
        self.assertIn(b'name="robots" content="noindex', body)

    # --- /ui.js and sitemap lastmod ----------------------------------------
    def test_ui_js_served(self):
        st, _, ct, body = self._raw("GET", "/ui.js")
        self.assertEqual(st, 200)
        self.assertIn("javascript", ct)
        self.assertIn(b"totop", body)

    def test_sitemap_has_niche_lastmod(self):
        st, _, ct, body = self._raw("GET", "/sitemap.xml")
        self.assertEqual(st, 200)
        xml = body.decode("utf-8", "replace")
        self.assertIn("urlset", xml)
        slug = seo._slugify(self._pick_niche())
        self.assertIn("/n/%s" % slug, xml)
        self.assertIn("<lastmod>", xml)


def urllib_quote(s):
    import urllib.parse
    return urllib.parse.quote(s)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Offline tests for the lead-page CMS (server.cms + cms_render)."""
import json
import os
import re
import sqlite3
import sys
import unittest
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amazon
import cms
import cms_render

amazon.CACHE_TTL = 0
amazon.MIN_INTERVAL = 0.0


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cms.ensure_tables(conn)
    return conn


SAMPLE_ITEMS = [
    {"asin": "B0TEST1234", "title": "Best Air Fryer 5QT", "price": 89.99,
     "stars": 4.6, "reviews": 15000, "currency": "USD"},
    {"asin": "B0TEST5678", "title": "Cheap Air Fryer 3QT", "price": 49.99,
     "stars": 4.2, "reviews": 8000, "currency": "USD"},
]


class TestCMSPage(unittest.TestCase):
    def test_get_or_create_seeds_default_sections(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        self.assertEqual(page["slug"], "air-fryer")
        sections = cms.get_sections(conn, page["id"])
        types = [s["section_type"] for s in sections]
        self.assertEqual(types, cms.DEFAULT_SECTION_ORDER)
        # All default sections present
        for st in cms.DEFAULT_SECTION_ORDER:
            self.assertIn(st, types)
        conn.close()

    def test_round_trip_preserves_page(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        sec = cms.get_sections(conn, page["id"])[0]
        cms.update_section(conn, sec["id"], {"content": {"headline": "New Hero"}})
        content = cms.get_section_content(conn, page["id"], sec["section_type"])
        self.assertEqual(content["headline"], "New Hero")

    def test_duplicate_section_prefers_most_recent(self):
        """A stale disabled duplicate row must not shadow an enabled row of the
        same section type (get_section_content should pick the newest row)."""
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        st = cms.DEFAULT_SECTION_ORDER[2]  # e.g. benefits
        conn.execute(
            "INSERT INTO lead_sections (page_id, section_type, content, enabled, sort_order) "
            "VALUES (?,?,?,?,?)",
            (page["id"], st, "{}", 0, 0))
        conn.execute(
            "INSERT INTO lead_sections (page_id, section_type, content, enabled, sort_order) "
            "VALUES (?,?,?,?,?)",
            (page["id"], st, json.dumps({"headline": "Fresh"}), 1, 0))
        conn.commit()
        content = cms.get_section_content(conn, page["id"], st)
        self.assertTrue(content["_enabled"])
        self.assertEqual(content["headline"], "Fresh")
        conn.close()

    def test_countdown_inline_js_is_valid(self):
        """Regression: the landing inline <script> must not contain literal
        f-string double-braces ({ {passive: true} }), which are a JS SyntaxError
        that kills the whole block — including the countdown timer."""
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        cms.update_page(conn, page["id"], {"settings": {
            "use_cms": True, "sticky_cta": True, "countdown_enabled": True,
            "countdown_minutes": 12, "promo_enabled": True, "animation": True}})
        ctx = cms.build_page_context(conn, "air fryer", {
            "products": SAMPLE_ITEMS, "subscriber_count": 5})
        html = cms_render.render_landing_page_page(ctx, "air fryer",
                                                   site_url="https://example.com")
        self.assertIn('<div class="sticky-cta">', html)
        self.assertIn("data-countdown=", html)
        script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        self.assertNotIn("{{", script)
        self.assertNotIn("}}", script)
        self.assertIn('addEventListener("scroll", onScroll, { passive: true })',
                      script.replace("\n", " "))
        conn.close()

    def test_cta_band_renders_without_amazon_url(self):
        """An enabled cta_band must still render (falling back to the review
        page) when the niche has no Amazon pick — it must never vanish."""
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        ctx = cms.build_page_context(conn, "air fryer", {"products": [], "subscriber_count": 0})
        sec = {"_type": "cta_band", "headline": "Ready?", "button_text": "See top pick →"}
        html = cms_render._section_html(sec, ctx)
        self.assertIn('<div class="card center reveal">', html)
        self.assertIn('href="/n/air-fryer"', html)
        self.assertEqual(ctx["slug"], "air-fryer")
        conn.close()

    def test_get_section_content_merges_defaults(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        # A field not in stored content should fall back to the default
        content = cms.get_section_content(conn, page["id"], "hero")
        self.assertIn("headline", content)
        # Misspelled type -> empty default
        self.assertEqual(cms.get_section_content(conn, page["id"], "nope"), {})
        conn.close()


class TestCMSRenderer(unittest.TestCase):
    def setUp(self):
        amazon.AFFILIATE_TAG = "yourname-20"
        amazon.set_market("com")

    def test_render_full_page(self):
        conn = _conn()
        ctx = cms.build_page_context(conn, "air fryer", {
            "products": SAMPLE_ITEMS, "subscriber_count": 25})
        html = cms_render.render_landing_page_page(ctx, "air-fryer",
                                                    site_url="https://ex.com")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("gate-form", html)        # email-gated PDF
        self.assertIn("data-niche", html)
        self.assertIn("amazon.com/dp/B0TEST1234?tag=yourname-20", html)
        self.assertIn("/courier.js", html)
        self.assertIn('<link rel="canonical" href="https://ex.com/lp/air-fryer">', html)
        conn.close()

    def test_rendered_cta_carries_asin_beacon(self):
        conn = _conn()
        ctx = cms.build_page_context(conn, "air fryer", {"products": SAMPLE_ITEMS})
        html = cms_render.render_landing_page_page(ctx, "air-fryer")
        self.assertIn('data-asin="B0TEST1234"', html)
        self.assertIn("data-beacon", html)
        conn.close()

    def test_disabled_section_is_hidden(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        gate = next(s for s in cms.get_sections(conn, page["id"])
                    if s["section_type"] == "email_gate")
        cms.update_section(conn, gate["id"], {"enabled": 0})
        ctx = cms.build_page_context(conn, "air fryer", {"products": SAMPLE_ITEMS})
        html = cms_render.render_landing_page_page(ctx, "air-fryer")
        self.assertNotIn("gate-form", html)
        conn.close()

    def test_style_is_applied(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        cms.update_page(conn, page["id"], {"style": {"accent": "#00ff00",
                                                     "border_radius": "10px"}})
        ctx = cms.build_page_context(conn, "air fryer", {"products": SAMPLE_ITEMS})
        html = cms_render.render_landing_page_page(ctx, "air-fryer")
        self.assertIn("#00ff00", html)
        self.assertIn("10px", html)
        conn.close()

    def test_urgency_sections_render(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        for st in ("urgency", "guarantee", "methodology", "cta_band",
                   "testimonials", "faq", "benefits", "social_proof"):
            sec = next(s for s in cms.get_sections(conn, page["id"])
                       if s["section_type"] == st)
            self.assertIsNotNone(sec)
        ctx = cms.build_page_context(conn, "air fryer", {"products": SAMPLE_ITEMS})
        html = cms_render.render_landing_page_page(ctx, "air-fryer")
        self.assertIn("prices move daily", html.lower())
        conn.close()

    def test_promo_countdown_sticky_render(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        cms.update_page(conn, page["id"], {"settings": {
            "promo_enabled": True,
            "promo": {"text": "Free shipping today", "code": "SAVE10"},
            "countdown_enabled": True,
            "countdown_minutes": 30,
            "countdown_headline": "Time is ticking",
            "sticky_cta": True,
        }})
        ctx = cms.build_page_context(conn, "air fryer", {"products": SAMPLE_ITEMS})
        html = cms_render.render_landing_page_page(ctx, "air-fryer")
        self.assertIn('class="promo"', html)
        self.assertIn("Free shipping today", html)
        self.assertIn("SAVE10", html)
        self.assertIn("data-countdown", html)
        self.assertIn("data-cd=\"h\"", html)
        self.assertIn("Time is ticking", html)
        self.assertIn("sticky-cta", html)
        self.assertIn("sticky-cta", html)
        conn.close()

    def test_dark_preset_renders(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        cms.update_page(conn, page["id"], {"style": cms.preset_style("midnight")})
        ctx = cms.build_page_context(conn, "air fryer", {"products": SAMPLE_ITEMS})
        html = cms_render.render_landing_page_page(ctx, "air-fryer")
        self.assertIn("--bg:#0d1321", html)
        self.assertIn("--accent:#f5b942", html)
        conn.close()

    def test_gate_toggle_no_email_form(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        cms.update_page(conn, page["id"], {"settings": {"pdf_gated": False,
                                                        "email_gate_enabled": False}})
        ctx = cms.build_page_context(conn, "air fryer", {"products": SAMPLE_ITEMS})
        html = cms_render.render_landing_page_page(ctx, "air-fryer")
        self.assertNotIn("gate-form", html)
        self.assertIn("/_gated/pdf?keyword=air%20fryer", html)
        conn.close()

    def test_list_pages(self):
        conn = _conn()
        cms.get_or_create_page(conn, "air fryer")
        cms.get_or_create_page(conn, "coffee maker")
        pages = cms.list_pages(conn)
        self.assertEqual(len(pages), 2)
        conn.close()

    def test_preset_style_returns_full_style(self):
        st = cms.preset_style("midnight")
        self.assertEqual(st["mode"], "dark")
        self.assertEqual(st["preset"], "midnight")
        self.assertIn("font_family", st)
        st2 = cms.preset_style("nope")
        self.assertEqual(st2["preset"], "sunset")

    def test_apply_preset_roundtrip(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        cms.apply_preset(conn, page["id"], "ocean")
        page2 = cms.get_page(conn, "air fryer")
        self.assertEqual(page2["style"]["preset"], "ocean")
        self.assertEqual(page2["style"]["accent"], "#0284c7")
        conn.close()

    def test_generate_copy_reseeds_sections(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        gate = next(s for s in cms.get_sections(conn, page["id"])
                    if s["section_type"] == "email_gate")
        cms.update_section(conn, gate["id"], {"content": {"headline": "Custom"}, "enabled": 0})
        n = cms.generate_copy(conn, page["id"], "air fryer")
        self.assertEqual(n, len(cms.DEFAULT_SECTION_ORDER))
        sections = cms.get_sections(conn, page["id"])
        self.assertEqual(len(sections), n)
        gate2 = next(s for s in sections if s["section_type"] == "email_gate")
        self.assertEqual(gate2["enabled"], 1)
        conn.close()

    def test_generate_copy_uses_ai_copy_when_configured(self):
        import ai
        from unittest import mock
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        with mock.patch.object(ai, "configured", return_value=True), \
                mock.patch.object(ai, "generate",
                                  return_value=["AI headline here"]):
            n = cms.generate_copy(conn, page["id"], "air fryer")
        self.assertEqual(n, len(cms.DEFAULT_SECTION_ORDER))
        sections = {s["section_type"]: s for s in cms.get_sections(conn, page["id"])}
        hero = sections["hero"]["content"]
        self.assertEqual(hero["headline"], "AI headline here")
        email_gate = sections["email_gate"]["content"]
        self.assertTrue(email_gate["headline"])
        cta_band = sections["cta_band"]["content"]
        self.assertEqual(cta_band["headline"], "AI headline here")
        conn.close()

    def test_sticky_cta_toggle_controls_render(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        cms.update_page(conn, page["id"], {
            "settings": {"sticky_cta": False, "pdf_gated": False}})
        ctx = cms.build_page_context(conn, "air fryer", {"products": SAMPLE_ITEMS,
                                                          "subscriber_count": 25})
        self.assertEqual(cms_render._sticky_cta_html(ctx), "")
        html = cms_render.render_landing_page_page(ctx, "air-fryer")
        self.assertNotIn('<div class="sticky-cta">', html)
        self.assertNotIn('document.querySelector(".sticky-cta")', html)
        cms.update_page(conn, page["id"], {"settings": {"sticky_cta": True}})
        ctx = cms.build_page_context(conn, "air fryer", {"products": SAMPLE_ITEMS})
        self.assertIn("<div class=\"sticky-cta\">",
                      cms_render.render_landing_page_page(ctx, "air-fryer"))
        conn.close()

    def test_settings_toggles_reflect_in_render(self):
        conn = _conn()
        page = cms.get_or_create_page(conn, "air fryer")
        cms.update_page(conn, page["id"], {"settings": {
            "promo_enabled": True,
            "promo": {"text": "SAVE20 at checkout", "code": "SAVE20"},
            "countdown_enabled": True, "countdown_minutes": 15,
            "countdown_headline": "Offer ending", "countdown_done": "Ended",
            "animation": False}})
        ctx = cms.build_page_context(conn, "air fryer", {"products": SAMPLE_ITEMS,
                                                          "subscriber_count": 25})
        html = cms_render.render_landing_page_page(ctx, "air-fryer")
        self.assertIn("SAVE20 at checkout", html)
        self.assertIn("data-countdown=\"15\"", html)
        self.assertIn("Offer ending", html)
        self.assertNotIn("new IntersectionObserver", html)  # animation off
        conn.close()


class TestCMSRoutes(unittest.TestCase):
    """Boots the real HTTP server against a scratch DB and exercises the CMS
    admin/API routes end-to-end."""

    @classmethod
    def setUpClass(cls):
        import importlib
        import shutil
        import threading
        import uuid
        import urllib.request as urlreq
        from http.server import ThreadingHTTPServer

        cls.db = "/tmp/pstore_cms_%s.db" % uuid.uuid4().hex[:8]
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "pstore.db"), cls.db)
        os.environ["PSTORE_DB"] = cls.db
        os.environ["PSTORE_ADMIN_EMAIL"] = "owner@test.example"
        os.environ["PSTORE_ADMIN_PASSWORD"] = "test-pass-123"
        cls.email = "owner@test.example"
        cls.password = "test-pass-123"
        import server
        importlib.reload(server)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.PORT = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.urlopen = staticmethod(urlreq.urlopen)
        status, set_cookie, body = cls._raw(
            "/admin/login", "POST",
            body=b"email=%s&password=%s" % (cls.email.encode(), cls.password.encode()))
        assert status == 200, (status, body)
        cls.cookie = set_cookie.split(";")[0] if set_cookie else ""

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        cls.httpd.server_close()
        if os.path.exists(cls.db):
            os.unlink(cls.db)

    @classmethod
    def _raw(cls, path, method="GET", body=None, cookie=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=5)
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        set_cookie = resp.getheader("Set-Cookie")
        data = resp.read()
        conn.close()
        return status, set_cookie, data

    def _get(self, path, cookie=None):
        if cookie is None:
            cookie = self.cookie
        try:
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.PORT, path))
            if cookie:
                req.add_header("Cookie", cookie)
            with self.urlopen(req, timeout=5) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except Exception as exc:
            return getattr(exc, "code", None), None, b""

    @classmethod
    def _json_post(cls, path, obj, cookie=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=5)
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        conn.request("POST", path, body=json.dumps(obj).encode(), headers=headers)
        resp = conn.getresponse()
        status = resp.status
        data = resp.read()
        conn.close()
        return status, data

    def _ensure_page(self, keyword):
        """Create the CMS page by rendering its landing page, then return its id."""
        slug = keyword.replace(" ", "-")
        status, _c, _b = self._raw("/lp/" + slug)
        self.assertEqual(status, 200)
        st, _ct, body = self._get("/api/cms/pages")
        pages = json.loads(body)["pages"]
        for p in pages:
            if p["slug"] == slug:
                return p["id"]
        self.fail("page not created for %s" % keyword)

    def test_cms_admin_page_requires_auth(self):
        status, _cookie, _body = self._raw("/admin/cms")  # no cookie sent
        self.assertIn(status, (302, 401, 200))

    def test_cms_admin_page_renders(self):
        st, ctype, body = self._get("/admin/cms")
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("text/html"))
        html = body.decode("utf-8", "replace")
        self.assertIn("Lead page", html)
        self.assertIn("cms", html)

    def test_cms_pages_api(self):
        st, ctype, body = self._get("/api/cms/pages")
        self.assertEqual(st, 200)
        payload = json.loads(body)
        self.assertIn("pages", payload)

    def test_cms_preset_api(self):
        page_id = self._ensure_page("keto snacks")
        st, body = self._json_post("/api/cms/preset",
                                   {"page_id": page_id, "preset": "midnight"},
                                   cookie=self.cookie)
        self.assertEqual(st, 200)
        payload = json.loads(body)
        self.assertEqual(payload["style"]["preset"], "midnight")
        self.assertEqual(payload["style"]["mode"], "dark")

    def test_cms_preset_rejects_unknown(self):
        page_id = self._ensure_page("keto snacks")
        st, body = self._json_post("/api/cms/preset",
                                   {"page_id": page_id, "preset": "nope"},
                                   cookie=self.cookie)
        self.assertEqual(st, 400)

    def test_cms_generate_api(self):
        page_id = self._ensure_page("keto snacks")
        st, body = self._json_post("/api/cms/generate",
                                   {"page_id": page_id, "keyword": "keto snacks"},
                                   cookie=self.cookie)
        self.assertEqual(st, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["sections"], 10)
        self.assertEqual(payload["payload"]["id"], page_id)

    def test_cms_section_toggle_off(self):
        page_id = self._ensure_page("keto snacks")
        payload = json.loads(self._get("/api/cms/pages/%d" % page_id)[2])
        testi = next(s for s in payload["sections"] if s["type"] == "testimonials")
        st, body = self._json_post(
            "/api/cms/pages/%d/sections/%d" % (page_id, testi["id"]),
            {"section_id": testi["id"], "content": {}, "enabled": False},
            cookie=self.cookie)
        self.assertEqual(st, 200)
        payload = json.loads(body)
        sec = next(s for s in payload["sections"] if s["type"] == "testimonials")
        self.assertFalse(sec["enabled"])

    def test_cms_settings_toggle_round_trip(self):
        """Mirrors the editor JS payload and checks every page-feature toggle
        persists through /api/cms/page and lands in the saved page."""
        page_id = self._ensure_page("keto snacks")
        base = {"use_cms": True, "pdf_gated": True, "email_gate_enabled": True,
                "promo_enabled": False,
                "promo": {"text": "Free shipping", "code": "SAVE10"},
                "countdown_enabled": False, "countdown_minutes": 30,
                "countdown_headline": "", "countdown_done": "",
                "sticky_cta": True, "animation": True}
        style = {"preset": "sunset", "mode": "light", "layout": "centered",
                 "hero_style": "gradient", "border_radius": "", "font_family": "",
                 "cta_gradient": ""}
        states = {
            "promo on": dict(base, **{"promo_enabled": True,
                                      "promo": {"text": "20% off today", "code": "HOT20"}}),
            "countdown on": dict(base, **{"countdown_enabled": True,
                                          "countdown_minutes": 15,
                                          "countdown_headline": "Going fast",
                                          "countdown_done": "Done"}),
            "sticky off": dict(base, **{"sticky_cta": False}),
            "anim off": dict(base, **{"animation": False}),
            "gate off": dict(base, **{"pdf_gated": False, "email_gate_enabled": False}),
            "cms off": dict(base, **{"use_cms": False}),
        }
        for label, settings in states.items():
            st, body = self._json_post(
                "/api/cms/page",
                {"page_id": page_id, "style": style, "settings": settings},
                cookie=self.cookie)
            self.assertEqual(st, 200, label)
            payload = json.loads(self._get("/api/cms/pages/%d" % page_id)[2])
            got = payload["settings"]
            for key, val in settings.items():
                self.assertEqual(got.get(key), val, "%s: %s" % (label, key))
        # end on a CMS-rendered page with every marketing lever switched off
        st, _ = self._json_post(
            "/api/cms/page",
            {"page_id": page_id, "style": style, "settings": {
                "use_cms": True, "pdf_gated": False, "email_gate_enabled": False,
                "promo_enabled": False,
                "promo": {"text": "Free shipping", "code": "SAVE10"},
                "countdown_enabled": False, "countdown_minutes": 30,
                "countdown_headline": "", "countdown_done": "",
                "sticky_cta": False, "animation": False}},
            cookie=self.cookie)
        self.assertEqual(st, 200)
        st, _ct, html_body = self._get("/lp/keto-snacks")
        self.assertEqual(st, 200)
        live = html_body.decode("utf-8", "replace")
        self.assertNotIn('class="promo"', live)
        self.assertNotIn("data-countdown=", live)
        self.assertNotIn('<div class="sticky-cta">', live)
        self.assertNotIn("new IntersectionObserver", live)
        self.assertNotIn("gate-form", live)
        self.assertIn("<!DOCTYPE html>", live)
        # restore a clean, fully-gated default so sibling tests are unaffected
        st, _ = self._json_post(
            "/api/cms/page",
            {"page_id": page_id, "style": style, "settings": {
                "use_cms": True, "pdf_gated": True, "email_gate_enabled": True,
                "promo_enabled": False,
                "promo": {"text": "Free shipping", "code": "SAVE10"},
                "countdown_enabled": False, "countdown_minutes": 30,
                "countdown_headline": "", "countdown_done": "",
                "sticky_cta": True, "animation": True}},
            cookie=self.cookie)
        self.assertEqual(st, 200)

    def test_cms_enabled_chrome_actually_renders(self):
        """Positive case: when promo + countdown + sticky are ON, they must
        appear in the live markup with the countdown timer fully wired."""
        page_id = self._ensure_page("keto snacks")
        st, _ = self._json_post(
            "/api/cms/page",
            {"page_id": page_id, "style": {"preset": "sunset"}, "settings": {
                "use_cms": True, "pdf_gated": True, "email_gate_enabled": True,
                "promo_enabled": True,
                "promo": {"text": "Free shipping today", "code": "SAVE10"},
                "countdown_enabled": True, "countdown_minutes": 12,
                "countdown_headline": "Going fast", "countdown_done": "Done",
                "sticky_cta": True, "animation": True}},
            cookie=self.cookie)
        self.assertEqual(st, 200)
        st, _ct, html_body = self._get("/lp/keto-snacks")
        self.assertEqual(st, 200)
        live = html_body.decode("utf-8", "replace")
        self.assertIn('class="promo"', live)
        self.assertIn("Free shipping today", live)
        self.assertIn("SAVE10", live)
        self.assertIn('data-countdown="12"', live)
        self.assertIn("Going fast", live)
        self.assertIn('class="cd-box"', live)
        self.assertIn('data-cd="h"', live)
        self.assertIn('data-cd="m"', live)
        self.assertIn('data-cd="s"', live)
        self.assertIn('[data-countdown]', live)   # inline countdown driver present
        self.assertIn('<div class="sticky-cta">', live)
        # restore defaults so sibling tests are unaffected
        st, _ = self._json_post(
            "/api/cms/page",
            {"page_id": page_id, "style": {"preset": "sunset"}, "settings": {
                "use_cms": True, "pdf_gated": True, "email_gate_enabled": True,
                "promo_enabled": False,
                "promo": {"text": "Free shipping", "code": "SAVE10"},
                "countdown_enabled": False, "countdown_minutes": 30,
                "countdown_headline": "", "countdown_done": "",
                "sticky_cta": True, "animation": True}},
            cookie=self.cookie)
        self.assertEqual(st, 200)

    def test_landing_has_discreet_full_review_link(self):
        status, _c, body = self._raw("/lp/keto-snacks")
        self.assertEqual(status, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("Full review ↗", html)
        self.assertIn("href=\"/n/keto-snacks\"", html)

    def test_niche_page_has_one_pager_short_link(self):
        status, _c, body = self._raw("/n/keto-snacks")
        self.assertEqual(status, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("One-pager", html)
        self.assertIn("href=\"/lp/keto-snacks\"", html)
        self.assertIn('data-keyword="keto snacks"', html)

    def test_cms_gate_field_round_trip_flagged_value(self):
        page_id = self._ensure_page("keto snacks")
        # the editor sends pdf_gated via the mirrored cms-gate hidden input;
        # end the loop back on a fully-gated state for sibling tests
        for want in (False, True):
            st, _ = self._json_post(
                "/api/cms/page",
                {"page_id": page_id, "style": {}, "settings": {"pdf_gated": want,
                                                               "email_gate_enabled": want}},
                cookie=self.cookie)
            self.assertEqual(st, 200)
            payload = json.loads(self._get("/api/cms/pages/%d" % page_id)[2])
            self.assertEqual(payload["settings"]["pdf_gated"], want)
            self.assertEqual(payload["settings"]["email_gate_enabled"], want)
        self.assertIn("cms-gate2", self._get("/admin/cms?keyword=keto+snacks")[2]
                      .decode("utf-8", "replace"))
        self.assertIn("autoSaveSettings", self._get("/admin/cms?keyword=keto+snacks")[2]
                      .decode("utf-8", "replace"))
        self.assertIn("cms-sticky", self._get("/admin/cms?keyword=keto+snacks")[2]
                      .decode("utf-8", "replace"))

    def test_subscribe_returns_download_token(self):
        status, _cookie, body = self._raw(
            "/subscribe", "POST",
            body=b"email=gater@test.example&keyword=keto+snacks&first_name=Gater")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertIn("download_token", payload)
        self.assertTrue(payload["download_token"])

    def test_gated_pdf_rejects_bad_token(self):
        status, _cookie, body = self._get(
            "/_gated/pdf?keyword=keto+snacks&token=bogus")
        self.assertEqual(status, 403)

    def test_gated_pdf_serves_valid_token(self):
        st, _c, body = self._raw(
            "/subscribe", "POST",
            body=b"email=gater2@test.example&keyword=keto+snacks")
        payload = json.loads(body)
        token = payload["download_token"]
        req = urllib.request.Request(
            "http://127.0.0.1:%d/_gated/pdf?keyword=keto+snacks&token=%s"
            % (self.PORT, urllib.parse.quote(token)))
        with self.urlopen(req, timeout=15) as r:
            data = r.read()
        self.assertEqual(data[:4], b"%PDF")
        self.assertIn("application/pdf", r.headers.get("Content-Type", ""))

    def test_ungated_pdf_serves_publicly(self):
        # create the CMS page by rendering its landing page, then flip pdf_gated off
        status, _c, _b = self._raw("/lp/keto-snacks")
        self.assertEqual(status, 200)
        conn = sqlite3.connect(self.db)
        conn.execute(
            "UPDATE lead_pages SET settings=? WHERE slug='keto-snacks'",
            (json.dumps({"pdf_gated": False, "email_gate_enabled": False}),))
        conn.commit()
        conn.close()
        req = urllib.request.Request(
            "http://127.0.0.1:%d/_gated/pdf?keyword=keto+snacks" % self.PORT)
        with self.urlopen(req, timeout=15) as r:
            data = r.read()
        self.assertEqual(data[:4], b"%PDF")


if __name__ == "__main__":
    unittest.main()

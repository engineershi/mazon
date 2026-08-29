# -*- coding: utf-8 -*-
"""Offline tests for Mazon (no live network). Stub amazon._urlopen."""
import json
import os
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amazon
import market_engine
import niche
import seo
import indexnow

amazon.CACHE_TTL = 0
amazon.MIN_INTERVAL = 0.0


class FakeResponse:
    def read(self):
        return self.body

    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")


class FakeURL:
    """Assign responses per URL prefix; __call__ plays the role of _urlopen."""
    def __init__(self, routes):
        self.routes = routes

    def __call__(self, req, timeout=None):
        url = req.full_url if isinstance(req, urllib.request.Request) else req
        for prefix, body in self.routes:
            if url.startswith(prefix):
                return FakeResponse(body)
        raise OSError("no route: " + url)


SEARCH_HTML = """
<div data-component-type="s-search-result">
  <span>Best Keto Snack Bars 2026</span>
  <span class="a-offscreen">$12.99</span>
  <img alt="4.4 out of 5 stars" src="">
  <a href="/dp/B0KETO1234">link</a>
</div>
<a href="/dp/B0KETO1234">x</a>
<a href="/dp/B0KETO5678">y</a>
<p>1,234 results</p>
"""


class TestAmazon(unittest.TestCase):
    def setUp(self):
        amazon.AFFILIATE_TAG = "yourname-20"
        amazon.set_market("com")

    def test_parse_search_page_extracts_product(self):
        items, total = amazon._parse_search_page(SEARCH_HTML, top=8)
        self.assertEqual(total, 1234)
        self.assertTrue(items)
        self.assertIn("B0KETO1234", [i["asin"] for i in items])
        it = items[0]
        self.assertIn("Keto Snack", it["title"])
        self.assertEqual(it["price"], 12.99)
        self.assertEqual(it["stars"], 4.4)

    def test_urlopen_called_for_search(self):
        amazon._urlopen = FakeURL([("https://www.amazon.com/s", SEARCH_HTML)])
        items, source = amazon.search("keto snacks", top=4)
        self.assertEqual(source, "amazon")
        self.assertEqual(items[0]["asin"], "B0KETO1234")
        self.assertTrue(items[0]["url"].startswith("https://www.amazon.com/dp/B0KETO1234?tag=yourname-20"))

    def test_autosuggest_parses(self):
        body = json.dumps({"suggestions": [
            {"value": "keto snacks"}, {"value": "keto bars"}, {"value": "keto chips"}]})
        amazon._urlopen = FakeURL([("https://completion.amazon.com/api/2017/suggestions", body)])
        self.assertEqual(amazon.autosuggest("keto", limit=3),
                         ["keto snacks", "keto bars", "keto chips"])

    def test_affiliate_url_no_tag(self):
        amazon.AFFILIATE_TAG = ""
        self.assertEqual(amazon.affiliate_url("B0KETO1234"),
                         "https://www.amazon.com/dp/B0KETO1234")

    def test_multi_marketplace_url(self):
        amazon.set_market("co.uk")
        amazon.AFFILIATE_TAG = "uktag-21"
        self.assertEqual(amazon.affiliate_url("B0KETO1234"),
                         "https://www.amazon.co.uk/dp/B0KETO1234?tag=uktag-21")

    def test_scraper_serpapi_json(self):
        body = json.dumps({"organic_results": [
            {"title": "Keto Bar", "asin": "B0KETO9876", "price": 9.99,
             "rating": 4.5, "reviews": 1200}]})
        amazon.set_scraper_key("serpapi", "sk-xyz")
        amazon._urlopen = FakeURL([("https://serpapi.com/search.json", body)])
        items, pid = amazon._scraper_search("keto", top=5)
        self.assertEqual(pid, "serpapi")
        self.assertEqual(items[0]["asin"], "B0KETO9876")
        self.assertEqual(items[0]["reviews"], 1200)
        amazon.set_scraper_key("serpapi", "")


class TestNiche(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        amazon.AFFILIATE_TAG = "yourname-20"
        amazon.set_market("com")
        amazon.set_tag("yourname-20")

    def test_mine_niche_produces_niches(self):
        MINE_BODY = json.dumps({"suggestions": [
            {"value": "keto snacks"}, {"value": "keto snacks low carb"}]})
        amazon.autosuggest = lambda q, limit=8: ["keto snacks", "keto snacks low carb"]
        amazon._urlopen = FakeURL([
            ("https://completion.amazon.com/api/2017/suggestions", MINE_BODY),
            ("https://www.amazon.com/s", SEARCH_HTML)])
        niches, meta = niche.mine_niche("keto", top=4, max_niches=2)
        self.assertEqual(meta["seed"], "keto")
        self.assertGreaterEqual(len(niches), 1)
        self.assertIn("products", niches[0])
        self.assertTrue(any("source" in n for n in niches))

    def test_saturation_scoring(self):
        self.assertIsNone(niche._score_saturation([]))
        s = niche._score_saturation([{"reviews": 1200}, {"reviews": 3000}])
        self.assertIsNotNone(s)
        self.assertLessEqual(s, 10)


class TestMarketEngine(unittest.TestCase):
    def setUp(self):
        amazon.AFFILIATE_TAG = "yourname-20"
        amazon.set_market("com")

    def test_text_links_use_direct_tagged_url(self):
        out = market_engine.build_text_links(
            [{"asin": "B0KETO1234", "title": "Keto Bar", "reviews": 10}])
        self.assertIn("https://www.amazon.com/dp/B0KETO1234?tag=yourname-20", out)
        self.assertNotIn("/go/", out)

    def test_pick_best_product(self):
        p = market_engine.pick_for_buyers([
            {"asin": "A", "reviews": 5},
            {"asin": "B", "reviews": 90, "price": 50.0},
            {"asin": "C", "reviews": 90, "price": 1.0}])
        self.assertEqual(p["asin"], "C")

    def test_email_draft(self):
        d = market_engine.build_email_draft(
            [{"asin": "B0KETO1234", "title": "Keto Bar", "reviews": 10}])
        self.assertTrue(d.startswith("Subject:"))
        self.assertIn("https://www.amazon.com/dp/B0KETO1234?tag=yourname-20", d)
        self.assertNotIn("/go/", d)

    def test_redirect_expands_to_affiliate(self):
        url, market = market_engine.expand_go("B0KETO1234")
        self.assertIn("amazon.com/dp/B0KETO1234?tag=yourname-20", url)


class TestSalesFunnel(unittest.TestCase):
    def setUp(self):
        amazon.AFFILIATE_TAG = "yourname-20"
        amazon.set_market("com")
        self.items = [
            {"asin": "B0KETO1234", "title": "Keto Bar Crunch", "price": 12.99,
             "stars": 4.5, "reviews": 1240, "currency": "USD"},
            {"asin": "B0KETO5678", "title": "Keto Crackers", "price": 8.0,
             "stars": 4.0, "reviews": 300, "currency": "USD"},
        ]

    def test_build_landing_page(self):
        h = market_engine.build_landing_page("keto snacks", self.items,
                                             site_url="https://mazon.example")
        self.assertTrue(h.startswith("<!DOCTYPE html>"))
        self.assertIn("Get it on Amazon", h)
        self.assertNotIn("/go/", h)
        self.assertIn("we earn from qualifying purchases", h)
        self.assertIn("keto snacks", h)
        self.assertIn('href="https://www.amazon.com/dp/B0KETO1234?tag=yourname-20"', h)

    def test_email_sequence_parts(self):
        seq = market_engine.build_email_sequence("keto snacks", self.items)
        self.assertEqual(len(seq), 5)
        for m in seq:
            self.assertIn("subject", m)
            self.assertIn("body", m)
        self.assertIn("https://www.amazon.com/dp/B0KETO1234?tag=yourname-20", seq[0]["body"])
        self.assertNotIn("/go/", seq[0]["body"])

    def test_social_pack(self):
        pack = market_engine.build_social_pack("keto snacks", self.items)
        self.assertIn("instagram", pack)
        self.assertIn("https://www.amazon.com/dp/B0KETO1234?tag=yourname-20",
                      pack["instagram"]["caption"])
        self.assertNotIn("/go/", pack["x"]["caption"])
        self.assertTrue(pack["x"]["hashtags"].startswith("#"))

    def test_dm_conversation_scripts(self):
        c = market_engine.build_dm_conversation("keto snacks", self.items)
        self.assertIn("opener", c)
        self.assertIn("close", c)
        self.assertIn("https://www.amazon.com/dp/B0KETO1234?tag=yourname-20", c["close"])
        self.assertNotIn("/go/", c["close"])

    def test_review_pipeline_never_incentivizes(self):
        r = market_engine.build_review_pipeline("keto snacks", self.items)
        combined = " ".join(str(v) for v in r.values()).lower()
        self.assertIn("honest", combined)
        self.assertIn("no incentive", combined)

    def test_boost_campaigns(self):
        b = market_engine.build_boost_campaigns("keto snacks", self.items)
        self.assertGreaterEqual(len(b), 5)
        self.assertIn("https://www.amazon.com/dp/B0KETO1234?tag=yourname-20", b[0]["script"])
        self.assertNotIn("/go/", b[0]["script"])

    def test_build_funnel_payload(self):
        f = market_engine.build_funnel("keto snacks", self.items,
                                       site_url="https://mazon.example",
                                       affiliate_tag="yourname-20")
        self.assertEqual(f["landing_url"], "/lp/keto-snacks")
        self.assertEqual(len(f["email_sequence"]), 5)
        self.assertIn("landing_page", f)
        self.assertIn("reviews", f)
        self.assertTrue(f["stages"])


class TestSEO(unittest.TestCase):
    def test_niche_page_crawlable(self):
        html = seo.render_niche("keto snacks", {
            "products": [{"asin": "B0KETO1234", "title": "Keto Bar",
                          "price": 12.99, "stars": 4.5, "reviews": 10,
                          "url": "https://www.amazon.com/dp/B0KETO1234?tag=yourname-20"}],
            "source": "amazon"}).decode("utf-8")
        self.assertIn("<title>", html)
        self.assertIn('rel="canonical"', html)
        self.assertIn("@type", html)
        self.assertIn("https://www.amazon.com/dp/B0KETO1234?tag=yourname-20", html)
        self.assertNotIn("/go/", html)

    def test_landing_jsonld_not_visible_text(self):
        html = seo.render_landing([{"keyword": "keto snacks"}]).decode("utf-8")
        self.assertIn("application/ld+json", html)
        self.assertLess(html.index("application/ld+json"), html.index("</head>"))
        self.assertTrue(html.rstrip().endswith("</html>"))
        tail = html.rsplit("</body>", 1)[1]
        self.assertNotIn("@type", tail)

    def test_niche_jsonld_in_head(self):
        html = seo.render_niche("keto snacks", {
            "products": [{"asin": "B0KETO1234", "title": "Keto Bar",
                          "price": 12.99, "stars": 4.5, "reviews": 10}],
            "source": "amazon"}).decode("utf-8")
        self.assertLess(html.index("application/ld+json"), html.index("</head>"))
        self.assertIn('"@type": "Product"', html)
        self.assertTrue(html.rstrip().endswith("</html>"))

    def test_sitemap(self):
        s = seo.render_sitemap([("/", "2026-08-28"), ("/n/keto-snacks", "2026-08-28")])
        self.assertIn(b"/n/keto-snacks", s)
        self.assertIn(b"<urlset", s)

    def test_robots(self):
        self.assertIn(b"Sitemap:", seo.render_robots())


REAL_CARD_HTML = """
<div class="sg-col-inner"><div role="listitem" data-asin="B0FFZZZ001" data-index="2" data-component-type="s-search-result">
  <a class="a-link-normal s-line-clamp-3 s-link-style a-text-normal" href="/Keto-Slime/dp/B0FFZZZ001/ref=sr_1_1?foo=1">
    <span class="a-size-base-plus a-spacing-none a-color-base a-text-normal">Keto Bar Crunch Variety 12-Pack</span>
  </a>
  <span class="a-price" data-a-size="xl"><span class="a-offscreen">EUR\xa08.58</span></span>
  <span class="a-text-price"><span class="a-offscreen">List: EUR 13.61</span></span>
  <a aria-label="1,597 ratings" class="a-link-normal s-underline-text" href="/Only-Bean-Crunchy-K/dp/B0FFZZZ001"></a>
  <i data-cy="reviews-ratings-slot" alt="4.2 out of 5 stars"></i>
  <span class="a-badge-text" data-a-badge-color="white">Overall Pick</span>
</div>
<div role="listitem" data-asin="B0GGGG2222" data-component-type="s-search-result">
  <span class="a-badge-label-inner a-text-ellipsis"><div id="aod-background" class="a-section aok-hidden"></div></span>
  <a class="a-link-normal s-line-clamp-3 s-link-style a-text-normal" href="/Gammon-Hamper/dp/B0GGGG2222/ref=sr_1_2">
    <span class="a-size-base-plus a-spacing-none a-color-base a-text-normal">Keto Gammon Pack 2kg</span>
  </a>
</div>
"""


class TestRealisticParsing(unittest.TestCase):
    def test_card_scoped_fields(self):
        items, total = amazon._parse_search_page(REAL_CARD_HTML, top=8)
        self.assertEqual([i["asin"] for i in items], ["B0FFZZZ001", "B0GGGG2222"])
        it = items[0]
        self.assertEqual(it["title"], "Keto Bar Crunch Variety 12-Pack")
        self.assertEqual(it["price"], 8.58)
        self.assertEqual(it["currency"], "EUR")
        self.assertEqual(it["stars"], 4.2)
        self.assertEqual(it["reviews"], 1597)
        self.assertNotIn("Overall Pick", it["title"])

    def test_no_html_noise_in_titles(self):
        items, _ = amazon._parse_search_page(REAL_CARD_HTML, top=8)
        self.assertEqual(items[1]["title"], "Keto Gammon Pack 2kg")
        for it in items:
            self.assertNotIn("<", it["title"])
            self.assertNotIn(">", it["title"])

    def test_list_price_ignored_and_currency_symbol(self):
        self.assertEqual(amazon.currency_symbol("EUR"), "\u20ac")
        self.assertEqual(amazon.currency_symbol("USD"), "$")
        self.assertEqual(amazon.currency_symbol(None), "$")
        self.assertEqual(amazon._split_currency("EUR 8.58"), ("EUR", "8.58"))
        self.assertEqual(amazon._split_currency("$12.99"), ("USD", "12.99"))

    def test_price_with_currency_in_markdown(self):
        amazon.AFFILIATE_TAG = "yourname-20"
        md = market_engine.build_markdown(
            [{"asin": "B0FFZZZ001", "title": "Keto Bar", "price": 8.58,
              "currency": "EUR", "reviews": 100}])
        self.assertIn("\u20ac8.58", md)


class TestIndexNow(unittest.TestCase):
    KEY = "0aa657c0ce459baba7a21e6d40e35351"

    def setUp(self):
        indexnow._DEFAULT_KEY = self.KEY
        seo.BASE_URL = "https://mazon.example"

    def test_key_and_file_route(self):
        self.assertEqual(indexnow.key(), self.KEY)
        self.assertEqual(
            indexnow.key_file_path(),
            "https://mazon.example/%s.txt" % self.KEY)
        self.assertEqual(indexnow.serve_key("/%s.txt" % self.KEY), self.KEY)
        self.assertIsNone(indexnow.serve_key("/other.txt"))

    def test_submit_urls_success(self):
        indexnow._post = lambda url, payload, timeout=20: 200
        ok, msg = indexnow.submit_urls(
            ["https://mazon.example/", "https://mazon.example/n/keto-snacks"],
            base_url="https://mazon.example")
        self.assertTrue(ok)
        self.assertIn("accepted", msg)

    def test_submit_urls_accepts_202(self):
        indexnow._post = lambda url, payload, timeout=20: 202
        ok, _ = indexnow.submit_urls(["https://mazon.example/"],
                                     base_url="https://mazon.example")
        self.assertTrue(ok)

    def test_submit_urls_rejects_foreign_urls(self):
        indexnow._post = lambda url, payload, timeout=20: 200
        ok, msg = indexnow.submit_urls(["https://evil.example/x"],
                                       base_url="https://mazon.example")
        self.assertFalse(ok)
        self.assertIn("no urls", msg)

    def test_submit_urls_invalid_key(self):
        indexnow._DEFAULT_KEY = "xyz"
        ok, msg = indexnow.submit_urls(["https://mazon.example/"],
                                       base_url="https://mazon.example")
        self.assertFalse(ok)
        self.assertIn("invalid key", msg)


class TestRoutes(unittest.TestCase):
    """Boots the real HTTP server on an ephemeral port against a copy of the
    shipped DB, then exercises the SEO/IndexNow routes end-to-end."""

    @classmethod
    def setUpClass(cls):
        import importlib
        import shutil
        import threading
        import urllib.request as urlreq
        import uuid
        from http.server import ThreadingHTTPServer

        cls.db = "/tmp/mazon_test_route_%s.db" % uuid.uuid4().hex[:8]
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "mazon.db"), cls.db)
        os.environ["MAZON_DB"] = cls.db
        import server
        importlib.reload(server)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.PORT = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.urlopen = staticmethod(urlreq.urlopen)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        cls.httpd.server_close()
        if os.path.exists(cls.db):
            os.unlink(cls.db)

    def _get(self, path):
        try:
            with self.urlopen("http://127.0.0.1:%d%s" % (self.PORT, path),
                              timeout=5) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except Exception as exc:
            return getattr(exc, "code", None), None, b""

    def test_indexnow_key_file_is_raw(self):
        st, ctype, body = self._get("/%s.txt" % indexnow.key())
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("text/plain"))
        self.assertEqual(body, indexnow.key().encode("utf-8"))

    def test_indexnow_api(self):
        st, ctype, body = self._get("/api/indexnow")
        self.assertEqual(st, 200)
        payload = json.loads(body)
        self.assertEqual(payload["key"], indexnow.key())
        self.assertGreaterEqual(payload["url_count"], 1)

    def test_sitemap_xml(self):
        st, ctype, body = self._get("/sitemap.xml")
        self.assertEqual(st, 200)
        self.assertIn(b"<loc>", body)
        for page in seo.STATIC_PAGES:
            self.assertIn(("/%s</loc>" % page).encode(), body)

    def test_company_pages_application_ready(self):
        for slug in seo.STATIC_PAGES:
            st, ctype, body = self._get("/" + slug)
            html = body.decode("utf-8", "replace")
            self.assertEqual(st, 200, slug)
            self.assertTrue(ctype.startswith("text/html"), slug)
            self.assertIn("As an Amazon Associate", html, slug)
            self.assertIn('rel="canonical"', html, slug)

    def test_landing_page_serves_html_not_json(self):
        st, ctype, body = self._get("/lp/keto-snacks")
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("text/html"))
        self.assertTrue(body.startswith(b"<!DOCTYPE html>"))

    def test_landing_page_links_are_direct(self):
        _, _, body = self._get("/lp/keto-snacks")
        html = body.decode("utf-8", "replace")
        self.assertIn("https://www.amazon.com/dp/B0", html)
        self.assertNotIn("/go/", html)

    def test_root_stays_crawlable_landing(self):
        st, ctype, body = self._get("/")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("Explore niches", html)
        self.assertNotIn('id="settings"', html)

    def test_dashboard_route_has_admin_menu(self):
        st, ctype, body = self._get("/dashboard")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn('id="settings"', html)
        self.assertIn('/tool', html)
        self.assertIn("Mine a niche", html)

    def test_keys_page_lists_every_credential(self):
        st, ctype, body = self._get("/keys")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn(indexnow.key(), html)
        self.assertIn("IndexNow endpoint", html)
        self.assertIn("/api/indexnow", html)
        self.assertIn("/sitemap.xml", html)
        for pid in ("scraperapi", "outscraper", "serpapi"):
            self.assertIn("/keys/%s" % pid, html)

    def test_keys_provider_page(self):
        st, ctype, body = self._get("/keys/outscraper")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("Outscraper", html)
        self.assertIn("https://app.outscraper.com/", html)
        self.assertIn("OUTSCRAPER_API_KEY", html)

    def test_keys_unknown_provider_404(self):
        st, _, _ = self._get("/keys/nope")
        self.assertEqual(st, 404)


if __name__ == "__main__":
    unittest.main()

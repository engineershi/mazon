# -*- coding: utf-8 -*-
"""Offline tests for pstore (no live network). Stub amazon._urlopen."""
import json
import os
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amazon
import editorial
import market_engine
import niche
import seo
import indexnow
import security
import server

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
                                             site_url="https://pstore.example")
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
                                       site_url="https://pstore.example",
                                       affiliate_tag="yourname-20")
        self.assertEqual(f["landing_url"], "/lp/keto-snacks")
        self.assertEqual(len(f["email_sequence"]), 5)
        self.assertIn("landing_page", f)
        self.assertIn("reviews", f)
        self.assertTrue(f["stages"])


class TestEditorial(unittest.TestCase):
    PRODUCTS = [
        {"asin": "B0BBB", "title": "Premium Keto Bar 12-pack", "price": 13.29,
         "stars": 4.2, "reviews": 1664,
         "url": "https://www.amazon.com/dp/B0BBB?tag=yourname-20", "currency": "USD"},
        {"asin": "B0CCC", "title": "Keto Crackers Variety", "price": 12.99,
         "stars": 3.9, "reviews": 2761,
         "url": "https://www.amazon.com/dp/B0CCC?tag=yourname-20", "currency": "USD"},
        {"asin": "B0DDD", "title": "Protein Chips Multipack", "price": 29.98,
         "stars": 4.5, "reviews": 12435,
         "url": "https://www.amazon.com/dp/B0DDD?tag=yourname-20", "currency": "USD"},
    ]

    def test_best_pick_is_deterministic_and_strong(self):
        amazon.set_tag("yourname-20")
        best = editorial.best_pick(self.PRODUCTS)
        self.assertIsNotNone(best)
        self.assertEqual(best["asin"], "B0BBB")
        self.assertEqual(editorial.best_pick(self.PRODUCTS)["asin"], best["asin"])

    def test_intro_mentions_keyword_and_is_honest(self):
        amazon.set_tag("yourname-20")
        text = editorial.intro("keto snacks", self.PRODUCTS)
        self.assertIn("keto snacks", text)
        self.assertNotIn("we tested", text.lower())

    def test_pros_cons_are_data_derived(self):
        amazon.set_tag("yourname-20")
        for item in self.PRODUCTS:
            pp, cc = editorial.pros_cons(item, self.PRODUCTS)
            self.assertTrue(pp and cc)
        cheapest = self.PRODUCTS[1]
        priciest = self.PRODUCTS[2]
        p, c = editorial.pros_cons(cheapest, self.PRODUCTS)
        self.assertIn("$12.99", " ".join(p + c))
        p, c = editorial.pros_cons(priciest, self.PRODUCTS)
        self.assertIn("$29.98", " ".join(p + c))

    def test_comparison_rows_cover_inventory(self):
        amazon.set_tag("yourname-20")
        top = editorial.best_pick(self.PRODUCTS)
        rows = editorial.comparison_rows(self.PRODUCTS, top_asin=top["asin"])
        self.assertEqual(len(rows), 3)
        by_asin = {r["asin"]: r for r in rows}
        self.assertEqual(by_asin[top["asin"]]["badge"], "Top pick")
        self.assertEqual(by_asin[top["asin"]]["rank"], "#1")
        if len(rows) > 1:
            self.assertEqual(rows[1]["badge"], "Runner-up")

    def test_faq_and_jsonld_shapes(self):
        amazon.set_tag("yourname-20")
        best = editorial.best_pick(self.PRODUCTS)
        qas = editorial.faq("keto snacks", best)
        self.assertEqual(len(qas), 4)
        q, a = qas[0]
        self.assertIn("keto snacks", q)
        self.assertIn("Premium Keto Bar 12-pack", a)
        j = editorial.faq_jsonld("keto snacks", best)
        self.assertEqual(j["@type"], "FAQPage")
        self.assertEqual(len(j["mainEntity"]), 4)
        b = editorial.breadcrumb_jsonld("keto snacks")
        self.assertEqual(b["@type"], "BreadcrumbList")
        self.assertEqual(b["itemListElement"][1]["name"], "keto snacks picks")

    def test_related_excludes_self_and_caps_at_six(self):
        niches = [{"keyword": "keto snacks"}] + [{"keyword": "niche %d" % i} for i in range(10)]
        rel = editorial.related_niches("keto snacks", niches)
        self.assertNotIn("keto snacks", [n["keyword"] for n in rel])
        self.assertLessEqual(len(rel), 6)

    def test_featured_pick_returns_best_across_niches(self):
        amazon.set_tag("yourname-20")
        niches = [{"keyword": "keto snacks", "products": self.PRODUCTS},
                  {"keyword": "yoga", "products": [
                      {"asin": "B0YYY", "title": "Yoga Mat", "price": 20.0,
                       "stars": 3.0, "reviews": 5,
                       "url": "https://www.amazon.com/dp/B0YYY?tag=yourname-20"}]}]
        top, score, kw = editorial.featured_pick(niches)
        self.assertEqual(kw, "keto snacks")
        self.assertIsNotNone(top)


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
        self.assertIn("data-asin=\"B0KETO1234\"", html)
        self.assertIn("https://www.amazon.com/dp/B0KETO1234?tag=yourname-20", html)
        self.assertNotIn("/go/", html)

    def test_niche_page_has_editorial_trust_machinery(self):
        html = seo.render_niche("keto snacks", {
            "products": [
                {"asin": "B0KETO1234", "title": "Keto Bar", "price": 12.99,
                 "stars": 4.5, "reviews": 3100,
                 "url": "https://www.amazon.com/dp/B0KETO1234?tag=yourname-20"},
                {"asin": "B0CRACK", "title": "Cracker Packs", "price": 9.99,
                 "stars": 4.1, "reviews": 900,
                 "url": "https://www.amazon.com/dp/B0CRACK?tag=yourname-20"},
            ],
            "source": "amazon"},
            saved_niches=[{"keyword": "keto snacks"}, {"keyword": "yoga mats"}],
        ).decode("utf-8")
        for section in ("Best overall", "How we pick", "Why trust pstore",
                        "Compare the shortlist", "details class=\"faq\"",
                        "By the", "Updated", "yoga mats", "yoga-mats",
                        "\"@type\": \"FAQPage\"", "\"@type\": \"BreadcrumbList\"",
                        "Check price on Amazon"):
            self.assertIn(section, html, section)
        self.assertLess(html.index("Best overall"), html.index("Compare the shortlist"))

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
        urls = seo.indexable_urls([{"keyword": "keto snacks"}], "https://pstore.example")
        self.assertIn("https://pstore.example/n/keto-snacks", urls)
        self.assertIn("https://pstore.example/lp/keto-snacks", urls)

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
        seo.BASE_URL = "https://pstore.example"

    def test_key_and_file_route(self):
        self.assertEqual(indexnow.key(), self.KEY)
        self.assertEqual(
            indexnow.key_file_path(),
            "https://pstore.example/%s.txt" % self.KEY)
        self.assertEqual(indexnow.serve_key("/%s.txt" % self.KEY), self.KEY)
        self.assertIsNone(indexnow.serve_key("/other.txt"))

    def test_submit_urls_success(self):
        indexnow._post = lambda url, payload, timeout=20: 200
        ok, msg = indexnow.submit_urls(
            ["https://pstore.example/", "https://pstore.example/n/keto-snacks"],
            base_url="https://pstore.example")
        self.assertTrue(ok)
        self.assertIn("accepted", msg)

    def test_submit_urls_accepts_202(self):
        indexnow._post = lambda url, payload, timeout=20: 202
        ok, _ = indexnow.submit_urls(["https://pstore.example/"],
                                     base_url="https://pstore.example")
        self.assertTrue(ok)

    def test_submit_urls_rejects_foreign_urls(self):
        indexnow._post = lambda url, payload, timeout=20: 200
        ok, msg = indexnow.submit_urls(["https://evil.example/x"],
                                       base_url="https://pstore.example")
        self.assertFalse(ok)
        self.assertIn("no urls", msg)

    def test_submit_urls_invalid_key(self):
        indexnow._DEFAULT_KEY = "xyz"
        ok, msg = indexnow.submit_urls(["https://pstore.example/"],
                                       base_url="https://pstore.example")
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

        cls.db = "/tmp/pstore_test_route_%s.db" % uuid.uuid4().hex[:8]
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
        cls._saved_indexnow_post = indexnow._post
        indexnow._post = lambda url, payload, timeout=20: None
        status, location, set_cookie, body = cls._raw(
            "/admin/login", "POST",
            body=b"email=%s&password=%s" % (cls.email.encode(), cls.password.encode()))
        assert status == 200, (status, body)
        cls.cookie = set_cookie.split(";")[0] if set_cookie else ""

    @classmethod
    def tearDownClass(cls):
        indexnow._post = cls._saved_indexnow_post
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
        location = resp.getheader("Location")
        set_cookie = resp.getheader("Set-Cookie")
        data = resp.read()
        conn.close()
        return status, location, set_cookie, data

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
        self.assertNotIn(b"/admin</loc>", body)
        self.assertIn(b"/lp/keto-snacks</loc>", body)
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
        self.assertIn("Explore the niches", html)
        self.assertNotIn('id="settings"', html)

    def test_dashboard_route_has_admin_menu(self):
        st, ctype, body = self._get("/dashboard")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn('id="settings"', html)
        self.assertIn('/tool', html)
        self.assertIn("Mine a niche", html)
        self.assertIn("Step 3", html)
        self.assertNotIn('href="/keys"', html)

    def test_dashboard_js_wires_per_niche_launch(self):
        st, ctype, body = self._get("/app.js")
        self.assertEqual(st, 200)
        js = body.decode("utf-8", "replace")
        self.assertIn("Launch marketing", js)
        self.assertIn('/tool?keyword=', js)
        self.assertIn("conn-status", js)
        self.assertIn("renderConnections", js)

    def test_workbench_page_has_one_click_launch(self):
        st, ctype, body = self._get("/tool")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        for needle in ("Launch marketing", "launch-btn", "send-kw", "ebook-open",
                       "api/tools/launch", "?keyword=", "stat-clicks"):
            self.assertIn(needle, html, needle)

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

    def test_tools_workbench_payload(self):
        import json as _json
        from urllib.parse import quote
        st, _, body = self._get("/api/tools?keyword=keto+snacks")
        self.assertEqual(st, 200)
        p = _json.loads(body)
        self.assertEqual(p["keyword"], "keto snacks")
        self.assertEqual(p["slug"], "keto-snacks")
        self.assertIn("/n/keto-snacks", p["niche_url"])
        self.assertEqual(p["landing_url"], "/lp/keto-snacks")
        self.assertIn("/admin/ebooks/pdf?keyword=" + quote("keto snacks"), p["ebook_url"])
        self.assertIn("funnel", p)
        self.assertIn("text_links", p)
        self.assertEqual(p["stats"]["subscribers_active"], 0)
        self.assertEqual(p["stats"]["subscribers_ready"], 0)
        self.assertEqual(p["indexnow"]["key"], indexnow.key())

    def test_tools_workbench_unknown_keyword_still_shapes_payload(self):
        import json as _json
        st, _, body = self._get("/api/tools?keyword=NOPE")
        self.assertEqual(st, 200)
        p = _json.loads(body)
        self.assertEqual(p["count"], 0)
        self.assertEqual(p["landing_url"], "/lp/nope")

    def test_tools_launch_warms_ebook_and_indexnow(self):
        import json as _json
        from urllib.parse import quote
        st, _, _, body = self._raw(
            "/api/tools/launch", "POST", body=b"keyword=keto snacks",
            cookie=self.cookie)
        self.assertEqual(st, 200)
        p = _json.loads(body)
        self.assertEqual(p["keyword"], "keto snacks")
        self.assertTrue(p["launched"]["landing"])
        self.assertTrue(p["launched"]["ebook_cached"])
        self.assertTrue(p["launched"]["indexnow_queued"])
        self.assertTrue(p["ebook_ready"])
        # ebook warmed by the launch -> its PDF now serves instantly
        st, ctype, pdf = self._get("/admin/ebooks/pdf?keyword=" + quote("keto snacks"))
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("application/pdf"), ctype)
        self.assertGreater(len(pdf), 100)

    def test_tools_launch_unknown_keyword_404(self):
        import json as _json
        st, _, _, body = self._raw("/api/tools/launch", "POST",
                                   body=b"keyword=no-such-niche", cookie=self.cookie)
        self.assertEqual(st, 404)
        self.assertIn("saved niche", _json.loads(body)["error"])

    def test_tools_launch_requires_auth(self):
        st, _, _, _ = self._raw("/api/tools/launch", "POST", body=b"keyword=x")
        self.assertEqual(st, 401)

    def test_admin_pages_redirect_to_login_unauth(self):
        for route in ("/dashboard", "/tool", "/keys", "/keys/scraperapi", "/admin"):
            st, location, _, _ = self._raw(route)
            self.assertEqual(st, 302, route)
            self.assertTrue(location.startswith("/admin/login"), (route, location))

    def test_admin_apis_reject_unauth(self):
        for route in ("/api/settings", "/api/niches", "/api/indexnow", "/api/tools"):
            st, _, _, body = self._raw(route)
            self.assertEqual(st, 401, route)
            self.assertIn(b"unauthorized", body)

    def test_login_wrong_password(self):
        st, _, set_cookie, body = self._raw(
            "/admin/login", "POST", body=b"email=owner@test.example&password=nope")
        self.assertEqual(st, 200)
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertIsNone(set_cookie)

    def test_login_wrong_email(self):
        st, _, set_cookie, body = self._raw(
            "/admin/login", "POST", body=b"email=bad@test.example&password=test-pass-123")
        self.assertEqual(st, 200)
        self.assertFalse(json.loads(body)["ok"])
        self.assertIsNone(set_cookie)

    def test_login_success_sets_cookie(self):
        st, _, set_cookie, body = self._raw(
            "/admin/login", "POST",
            body=b"email=owner@test.example&password=test-pass-123")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertTrue(set_cookie.startswith("pstore_admin="))
        cookie = set_cookie.split(";")[0]
        st, _, _ = self._get("/dashboard", cookie=cookie)
        self.assertEqual(st, 200)

    def test_login_supports_json_next_route(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/admin/login" % self.PORT,
            data=json.dumps({"email": "owner@test.example",
                             "password": "test-pass-123", "next": "/tool"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with self.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertTrue(json.loads(r.read())["ok"])
            self.assertTrue(r.headers.get("Set-Cookie", "").startswith("pstore_admin="))

    def test_admin_page_lists_every_page(self):
        st, ctype, body = self._get("/admin")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn('name="robots" content="noindex,nofollow"', html)
        for needle in ("/dashboard", "/tool", "/keys", "/keys/scraperapi",
                       "/n/keto-snacks", "/lp/keto-snacks", "/about", "/contact",
                       "/sitemap.xml", "/robots.txt", "/%s.txt" % indexnow.key(),
                       "/api/settings", "/api/tools", "All pages"):
            self.assertIn(needle, html, needle)

    def test_logout_invalidates_session(self):
        st, _, set_cookie, body = self._raw(
            "/admin/login", "POST",
            body=b"email=owner@test.example&password=test-pass-123")
        cookie = set_cookie.split(";")[0]
        st_dash, _, _, _ = self._raw("/dashboard", cookie=cookie)
        self.assertEqual(st_dash, 200)
        st_logout, location, _, _ = self._raw("/admin/logout", cookie=cookie)
        self.assertEqual(st_logout, 302)
        self.assertEqual(location, "/admin/login")
        st_after, _, _, _ = self._raw("/dashboard", cookie=cookie)
        self.assertEqual(st_after, 302)  # session gone -> back to login

    def test_public_pages_need_no_cookie(self):
        for route in ("/", "/n/keto-snacks", "/about", "/privacy",
                      "/sitemap.xml", "/robots.txt", "/%s.txt" % indexnow.key(),
                      "/lp/keto-snacks"):
            st, _, _ = self._get(route, cookie="")
            self.assertEqual(st, 200, route)


class TestSecurityAndOAuth(unittest.TestCase):
    """Hardening: rate limits, 413, CSRF origin check, 405s, security headers,
    SQLi literals stored verbatim, and HMAC-signed OAuth state round-trips."""

    IPKEY = "login|127.0.0.1|127.0.0.1"
    APIKEY = "api|127.0.0.1|127.0.0.1"
    HTTPKEY = "127.0.0.1|127.0.0.1"

    @classmethod
    def setUpClass(cls):
        import importlib
        import shutil
        import threading
        import uuid
        from http.server import ThreadingHTTPServer

        import security
        security.LOGIN_LIMITER.clear(cls.IPKEY)
        security.API_LIMITER.clear(cls.APIKEY)
        security.HTTP_LIMITER.clear(cls.HTTPKEY)

        cls.db = "/tmp/pstore_test_sec_%s.db" % uuid.uuid4().hex[:8]
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
        cls._raw("/admin/login", "POST",
                 body=b"email=owner@test.example&password=test-pass-123")
        cls.cookie = None  # per-test login, to keep the brute-force limiter fresh

    @classmethod
    def tearDownClass(cls):
        import security
        security.LOGIN_LIMITER.clear(cls.IPKEY)
        security.API_LIMITER.clear(cls.APIKEY)
        security.HTTP_LIMITER.clear(cls.HTTPKEY)
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        cls.httpd.server_close()
        if os.path.exists(cls.db):
            os.unlink(cls.db)

    @classmethod
    def _raw(cls, path, method="GET", body=None, cookie=None, headers=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=5)
        hdrs = dict(headers or {})
        if cookie:
            hdrs["Cookie"] = cookie
        if body is not None:
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        out = (resp.status, resp.getheader("Location"), resp.getheader("Set-Cookie"),
               resp.getheader("Content-Type"), resp.read())
        conn.close()
        return out

    def _login(self):
        st, loc, sc, _, body = self._raw(
            "/admin/login", "POST",
            body=b"email=owner@test.example&password=test-pass-123")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["ok"])
        return sc.split(";")[0]

    # ------------------------------------------------------------------ core

    def test_security_headers_present_everywhere(self):
        st, _, _, ctype, body = self._raw("/")
        self.assertEqual(st, 200)
        self.assertIn("html", ctype)
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.PORT, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.getheader("X-Frame-Options"), "DENY")
        self.assertIn("default-src 'self'", resp.getheader("Content-Security-Policy"))
        self.assertEqual(resp.getheader("Referrer-Policy"), "strict-origin-when-cross-origin")
        resp.read()
        conn.close()

    def test_state_token_roundtrip_and_tamper(self):
        import security
        good = security.make_token("oauth:google", 600)
        self.assertEqual(security.verify_token(good), "oauth:google")
        self.assertIsNone(security.verify_token(good + "x"))
        self.assertIsNone(security.verify_token("forged"))
        expired = security.make_token("oauth:google", -1)
        self.assertIsNone(security.verify_token(expired))

    # ------------------------------------------------------------------ rate / size

    def test_uri_too_long(self):
        st, _, _, _, _ = self._raw("/" + "a" * (security.MAX_URL + 10))
        self.assertEqual(st, 414)

    def test_oversized_body_rejected(self):
        st, _, _, _, _ = self._raw("/admin/login", "POST",
                                   body=b"x" * (security.MAX_BODY + 10))
        self.assertEqual(st, 413)

    def test_login_brute_force_locked_out(self):
        import security
        security.LOGIN_LIMITER.clear(self.IPKEY)
        for _ in range(security.LOGIN_LIMITER.limit):
            st, _, _, _, body = self._raw(
                "/admin/login", "POST", body=b"email=x@test.example&password=wrong")
            self.assertEqual(st, 200)
            self.assertFalse(json.loads(body)["ok"])
        st, _, _, _, _ = self._raw(
            "/admin/login", "POST", body=b"email=x@test.example&password=wrong")
        self.assertEqual(st, 429)
        security.LOGIN_LIMITER.clear(self.IPKEY)

    def test_cross_origin_post_blocked(self):
        st, _, _, _, _ = self._raw("/admin/login", "POST",
                                   body=b"email=owner@test.example&password=test-pass-123",
                                   headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403)

    def test_unsupported_methods_rejected(self):
        st, _, _, _, _ = self._raw("/dashboard", "PUT")
        self.assertEqual(st, 405)
        st, _, _, _, _ = self._raw("/dashboard", "DELETE")
        self.assertEqual(st, 405)

    # ------------------------------------------------------------------ SQL hardening

    def test_sql_injection_literal_stored_verbatim(self):
        cookie = self._login()
        evil = "drop table niches; -- ' OR '1'='1"
        st, _, _, _, _ = self._raw(
            "/api/niches", "POST", cookie=cookie,
            body=("keyword=%s&score=5&saturation=1&products=[]"
                  % urllib.request.quote(evil)).encode("utf-8"))
        self.assertEqual(st, 200)
        st, _, _, _, body = self._raw("/api/niches", cookie=cookie)
        self.assertEqual(st, 200)
        self.assertIn(evil, body.decode("utf-8", "replace"))

    # ------------------------------------------------------------------ OAuth

    def test_oauth_buttons_hidden_when_unconfigured(self):
        st, _, _, _, body = self._raw("/admin/login")
        html = body.decode("utf-8", "replace")
        self.assertEqual(st, 200)
        self.assertNotIn("Continue with Google", html)
        self.assertNotIn("/admin/oauth/", html)

    def test_oauth_authorize_redirect_and_callback(self):
        import unittest.mock as mock
        import oauth
        with mock.patch.object(oauth, "providers_configured",
                               return_value=[("google", "Google", "Continue with Google")]):
            st, loc, sc, _, _ = self._raw("/admin/oauth/google")
        self.assertEqual(st, 302)
        self.assertTrue(loc.startswith("https://accounts.google.com/o/oauth2/v2/auth"))
        self.assertIn("redirect_uri", loc)
        self.assertIn("state=", loc)
        state = urllib.request.unquote(loc.split("state=")[1].split("&")[0])
        self.assertEqual(security.verify_token(state), "oauth:google")
        self.assertTrue(sc.startswith("pstore_oauth="))

        def fake(req, timeout=None):
            url = req.full_url if isinstance(req, urllib.request.Request) else req
            if url.startswith("https://oauth2.googleapis.com/token"):
                return FakeResponse(b'{"access_token":"tok-1"}')
            return FakeResponse(b'{"email":"owner@test.example","name":"Owner"}')

        with mock.patch.object(oauth, "providers_configured",
                               return_value=[("google", "Google", "Continue with Google")]), \
             mock.patch.object(oauth, "_urlopen", fake):
            st, loc, sc, _, _ = self._raw(
                "/admin/oauth/google/callback?code=abc&state=%s" % urllib.request.quote(state),
                cookie=sc.split(";")[0])
        self.assertEqual(st, 302)
        self.assertEqual(loc, "/dashboard")
        self.assertTrue(sc.startswith("pstore_admin="))

    def test_oauth_callback_rejects_foreign_email(self):
        import unittest.mock as mock
        import oauth
        with mock.patch.object(oauth, "providers_configured",
                               return_value=[("google", "Google", "Continue with Google")]):
            st, loc, sc, _, _ = self._raw("/admin/oauth/google")
        self.assertEqual(st, 302)
        state = urllib.request.unquote(loc.split("state=")[1].split("&")[0])
        cookie = sc.split(";")[0]

        def fake(req, timeout=None):
            url = req.full_url if isinstance(req, urllib.request.Request) else req
            if url.startswith("https://oauth2.googleapis.com/token"):
                return FakeResponse(b'{"access_token":"tok-1"}')
            return FakeResponse(b'{"email":"someone-else@example.com","name":"X"}')

        with mock.patch.object(oauth, "providers_configured",
                               return_value=[("google", "Google", "Continue with Google")]), \
             mock.patch.object(oauth, "_urlopen", fake):
            st, _, _, _, body = self._raw(
                "/admin/oauth/google/callback?code=abc&state=%s" % urllib.request.quote(state),
                cookie=cookie)
        self.assertEqual(st, 200)
        self.assertIn("not authorized", body.decode("utf-8", "replace"))

    def test_oauth_callback_rejects_forged_state(self):
        import unittest.mock as mock
        import oauth
        with mock.patch.object(oauth, "providers_configured",
                               return_value=[("google", "Google", "Continue with Google")]):
            st, _, _, _, body = self._raw(
                "/admin/oauth/google/callback", cookie="pstore_oauth=forgedstate")
        self.assertEqual(st, 200)
        self.assertIn("stale or tampered", body.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()

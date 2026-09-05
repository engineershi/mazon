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
import earnings

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
        b = market_engine.build_boost_campaigns("keto snacks", self.items,
                                                base_url="https://pstore.example")
        self.assertGreaterEqual(len(b), 5)
        self.assertIn("utm_source=boost", b[0]["script"])
        self.assertIn("/lp/keto-snacks?utm_source=boost", b[0]["script"])
        self.assertIn("utm_content=", b[1]["link"])
        joined = " ".join(x["script"] for x in b)
        self.assertIn("https://www.amazon.com/dp/B0KETO1234?tag=yourname-20", joined)
        self.assertNotIn("/go/", joined)
        b2 = market_engine.build_boost_campaigns("keto snacks", self.items,
                                                 base_url="https://pstore.example")
        self.assertEqual(b[0]["code"], b2[0]["code"])
        self.assertEqual(b[0]["link"], b2[0]["link"])
        self.assertTrue(all(c["target"] == "landing" for c in b))

    def test_boost_campaigns_keyword_hints(self):
        b = market_engine.build_boost_campaigns("keto snacks", self.items,
                                                keywords=("keto bars for women",
                                                          "keto chips review"))
        self.assertIn("keto bars for women", b[0]["script"])
        self.assertIn("keto chips review", b[0]["script"])

    def test_build_funnel_payload(self):
        f = market_engine.build_funnel("keto snacks", self.items,
                                       site_url="https://pstore.example",
                                       affiliate_tag="yourname-20")
        self.assertEqual(f["landing_url"], "/lp/keto-snacks")
        self.assertEqual(len(f["email_sequence"]), 5)
        self.assertIn("landing_page", f)
        self.assertIn("reviews", f)
        self.assertNotIn("stages", f)
        self.assertIn("landing_url", f)

    def test_email_sequence_includes_alternates(self):
        seq = market_engine.build_email_sequence("keto snacks", self.items)
        # email 1 and 5 cross-sell runner-ups without cloaked links
        for body in (seq[0]["body"], seq[4]["body"]):
            self.assertIn("https://www.amazon.com/dp/B0KETO5678?tag=yourname-20", body)
            self.assertNotIn("/go/", body)
        # the primary pick is still pushed on email 1
        self.assertIn("B0KETO1234", seq[0]["body"])

    def test_upsell_block_renders_pick_and_alternates(self):
        html = editorial.upsell_block(self.items, "keto snacks")
        self.assertIn('data-role="upsell"', html)
        self.assertIn("Top pick", html)
        self.assertIn("Also consider", html)
        self.assertIn("keto snacks", html)
        self.assertIn('data-asin="B0KETO1234"', html)
        self.assertIn('data-asin="B0KETO5678"', html)
        self.assertIn('rel="nofollow sponsored noopener"', html)
        self.assertEqual(editorial.upsell_block([]), "")


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
        # Keep TestRoutes fully offline: some read paths (opportunities'
        # per-winner expand, social, etc.) call amazon.autosuggest/_fetch even
        # when CACHE_TTL=0. Without this stub they'd hit the live completion
        # endpoint and hang for the socket timeout under the class's load.
        cls._saved_amazon_urlopen = amazon._urlopen
        amazon._urlopen = lambda req, timeout=None: FakeResponse(b"{}")
        status, location, set_cookie, body = cls._raw(
            "/admin/login", "POST",
            body=b"email=%s&password=%s" % (cls.email.encode(), cls.password.encode()))
        assert status == 200, (status, body)
        cls.cookie = set_cookie.split(";")[0] if set_cookie else ""

    @classmethod
    def tearDownClass(cls):
        indexnow._post = cls._saved_indexnow_post
        amazon._urlopen = cls._saved_amazon_urlopen
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        cls.httpd.server_close()
        if os.path.exists(cls.db):
            os.unlink(cls.db)

    @classmethod
    def _raw(cls, path, method="GET", body=None, cookie=None, timeout=60, extra_headers=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=timeout)
        headers = dict(extra_headers or {})
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

    @classmethod
    def _raw_json(cls, path, payload, cookie=None, timeout=60):
        """POST a JSON body (proper Content-Type) and return (status, body)."""
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", cls.PORT, timeout=timeout)
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        conn.request("POST", path, body=json.dumps(payload).encode("utf-8"), headers=headers)
        resp = conn.getresponse()
        status = resp.status
        data = resp.read()
        conn.close()
        return status, data

    def _get(self, path, cookie=None, timeout=60):
        if cookie is None:
            cookie = self.cookie
        try:
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.PORT, path))
            if cookie:
                req.add_header("Cookie", cookie)
            with self.urlopen(req, timeout=timeout) as r:
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
        self.assertIn(b"/blog</loc>", body)
        for page in seo.STATIC_PAGES:
            self.assertIn(("/%s</loc>" % page).encode(), body)

    def test_blog_landing_lists_niche_articles(self):
        st, ctype, body = self._get("/blog")
        self.assertEqual(st, 200)
        self.assertTrue(ctype.startswith("text/html"), ctype)
        html = body.decode("utf-8", "replace")
        self.assertIn("<main data-niche=\"blog\"", html)
        self.assertIn('href="/n/keto-snacks"', html)
        self.assertIn("data-backed pick", html)
        self.assertIn('rel="canonical"', html)
        self.assertIn('href="/blog"', html)

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
        # every key group shows with its platform link + managed page
        for frag in ("/keys/ai/openai", "/keys/ai/opencode", "/keys/ai/mistral",
                     "/keys/ai/nvidia", "/keys/oauth/google", "/keys/oauth/facebook",
                     "/keys/site/indexnow", "/keys/site/gsc",
                     "/keys/market/affiliate", "/keys/market/market"):
            self.assertIn(frag, html, frag)
        self.assertIn("platform", html)

    def test_keys_provider_page(self):
        st, ctype, body = self._get("/keys/outscraper")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("Outscraper", html)
        self.assertIn("https://app.outscraper.com/", html)
        self.assertIn("OUTSCRAPER_API_KEY", html)
        self.assertIn("Test key", html)
        self.assertIn("/api/keys/test", html)

    def test_keys_provider_test_api(self):
        import json as _json
        saved = amazon._urlopen

        def fake(req, timeout):
            if any(v == "good-key" for k, v in req.headers.items()
                   if k.lower() == "x-api-key"):
                class R(object):
                    def read(self):
                        return b'{"balance": 12.5}'
                return R()
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)

        # /api/keys/test requires auth
        st, _, _, body = self._raw("/api/keys/test", "POST", body=b"pid=outscraper&key=good-key")
        self.assertEqual(st, 401)

        amazon._urlopen = fake
        try:
            st, _, _, body = self._raw("/api/keys/test", "POST",
                                       body=b"pid=outscraper&key=good-key",
                                       cookie=self.cookie)
            self.assertEqual(st, 200)
            d = _json.loads(body)
            self.assertTrue(d["ok"], d)
            self.assertEqual(d["status"], 200)
            self.assertIn("balance", d.get("detail", ""))
            st, _, _, body = self._raw("/api/keys/test", "POST",
                                       body=b"pid=outscraper&key=bad-key",
                                       cookie=self.cookie)
            d = _json.loads(body)
            self.assertFalse(d["ok"])
            self.assertEqual(d["status"], 401)
        finally:
            amazon._urlopen = saved
        # direct unit checks
        self.assertFalse(amazon.test_scraper_key("nope", "x")["ok"])
        self.assertFalse(amazon.test_scraper_key("outscraper", "  ")["ok"])

    def test_keys_unknown_provider_404(self):
        st, _, _ = self._get("/keys/nope")
        self.assertEqual(st, 404)

    def test_managed_key_pages_render_with_platform_link(self):
        import json as _json
        from urllib.parse import urlencode
        for path, needle in (
            ("/keys/ai/openai", "platform.openai.com"),
            ("/keys/ai/opencode", "opencode.ai"),
            ("/keys/site/gsc", "search.google.com/search-console"),
            ("/keys/site/indexnow", "indexnow.org"),
            ("/keys/oauth/google", "console.developers.google.com"),
            ("/keys/oauth/facebook", "developers.facebook.com"),
            ("/keys/market/affiliate", "affiliate-program.amazon.com"),
        ):
            st, ctype, _sc, body = self._raw(path, cookie=self.cookie)
            self.assertEqual(st, 200, path)
            html = body.decode("utf-8", "replace")
            self.assertIn(needle, html, path)
            self.assertIn("/api/keys/test", html, path)
            self.assertIn("/api/keys/save", html, path)
            self.assertIn("console ↗", html, path)
        # unknown managed key 404s
        st, _, _, _ = self._raw("/keys/site/nope", cookie=self.cookie)
        self.assertEqual(st, 404)

    def test_keys_save_indexnow_runtime_override(self):
        import json as _json
        from urllib.parse import urlencode
        saved = indexnow._RUNTIME_KEY
        try:
            body = urlencode({"group": "site", "keyid": "indexnow",
                              "key": "a1b2c3d4e5f60718293a4b5c6d7e8f90"}).encode()
            st, _, _, body = self._raw("/api/keys/save", "POST", body=body,
                                       cookie=self.cookie)
            self.assertEqual(st, 200)
            d = _json.loads(body)
            self.assertTrue(d["ok"], d)
            self.assertEqual(indexnow.key(), "a1b2c3d4e5f60718293a4b5c6d7e8f90")
            # auth gate
            st, _, _, _ = self._raw("/api/keys/save", "POST", body=body)
            self.assertEqual(st, 401)
        finally:
            indexnow._RUNTIME_KEY = saved

    def test_keys_save_gsc_runtime_override(self):
        import json as _json
        from urllib.parse import urlencode
        saved = seo._GOOGLE_SITE_VERIFICATION_RUNTIME
        try:
            body = urlencode({"group": "site", "keyid": "gsc",
                              "key": "google-verification-token-123"}).encode()
            st, _, _, body = self._raw("/api/keys/save", "POST", body=body,
                                       cookie=self.cookie)
            self.assertEqual(st, 200)
            d = _json.loads(body)
            self.assertTrue(d["ok"], d)
            self.assertEqual(seo.google_site_verification(), "google-verification-token-123")
            # now the landing head should emit the meta
            st, _, body = self._get("/")
            self.assertEqual(st, 200)
            self.assertIn("google-site-verification", body.decode("utf-8", "replace"))
        finally:
            seo._GOOGLE_SITE_VERIFICATION_RUNTIME = saved

    def test_keys_test_gsc_format(self):
        import json as _json
        from urllib.parse import urlencode
        good = urlencode({"group": "site", "keyid": "gsc", "key": "tok"}).encode()
        st, _, _, body = self._raw("/api/keys/test", "POST", body=good, cookie=self.cookie)
        self.assertEqual(st, 200)
        d = _json.loads(body)
        self.assertTrue(d["ok"], d)

    def test_ai_key_page_test_dispatches(self):
        import json as _json
        from urllib.parse import urlencode
        st, _, _, body = self._raw(
            "/api/keys/test", "POST",
            body=urlencode({"group": "ai", "keyid": "openai", "key": ""}).encode(),
            cookie=self.cookie)
        self.assertEqual(st, 200)
        d = _json.loads(body)
        self.assertFalse(d["ok"])
        self.assertIn("no api key", d["error"].lower())

    def test_earnings_config_save(self):
        import json as _json
        from urllib.parse import urlencode
        saved = dict(earnings._runtime)
        try:
            st, _, _, body = self._raw(
                "/api/earnings/config", "POST",
                body=urlencode({"commission_pct": "9", "avg_order": "80",
                                "order_rate": "0.04"}).encode(),
                cookie=self.cookie)
            self.assertEqual(st, 200)
            d = _json.loads(body)
            self.assertTrue(d["ok"], d)
            self.assertAlmostEqual(earnings.commission_pct(""), 9.0)
            self.assertAlmostEqual(earnings.avg_order(""), 80.0)
            self.assertAlmostEqual(earnings.order_rate(""), 0.04)
        finally:
            earnings._runtime.clear()
            earnings._runtime.update(saved)
        # auth gate
        st, _, _, _ = self._raw("/api/earnings/config", "POST",
                                body=b"commission_pct=9&avg_order=80&order_rate=0.04")
        self.assertEqual(st, 401)

    def test_earnings_log_month(self):
        import json as _json
        from urllib.parse import urlencode
        st, _, _, body = self._raw(
            "/api/earnings/log", "POST",
            body=urlencode({"month": "2026-08", "orders": "12", "earnings": "45.5"}).encode(),
            cookie=self.cookie)
        self.assertEqual(st, 200)
        d = _json.loads(body)
        self.assertTrue(d["ok"], d)
        # bad month format rejected
        st, _, _, body = self._raw(
            "/api/earnings/log", "POST",
            body=urlencode({"month": "aug", "orders": "1", "earnings": "1"}).encode(),
            cookie=self.cookie)
        d = _json.loads(body)
        self.assertFalse(d["ok"])

    def test_analytics_page_has_earnings(self):
        st, _, _, body = self._raw("/admin/analytics", cookie=self.cookie)
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("Earnings", html)
        self.assertIn("est. commission", html)
        self.assertIn("/api/earnings/config", html)

    def test_earnings_priority_ranks_by_commission(self):
        # seed clicks so keto-snacks outclicks best-fish-oil
        with server._lock:
            conn = server._db()
            conn.execute("DELETE FROM clicks WHERE slug IN ('keto-snacks','best-fish-oil')")
            for _ in range(9):
                conn.execute("INSERT INTO clicks (slug, content) VALUES ('keto-snacks','ab-1')")
            for _ in range(3):
                conn.execute("INSERT INTO clicks (slug, content) VALUES ('best-fish-oil','ab-1')")
            conn.commit()
            conn.close()
        st, _, _, data = self._raw("/api/earnings/priority", cookie=self.cookie)
        self.assertEqual(st, 200)
        d = json.loads(data)
        self.assertTrue(d["ranked"], d)
        rank = {r["niche"]: r for r in d["ranked"]}
        self.assertGreater(rank["keto-snacks"]["commission_est"],
                           rank["best-fish-oil"]["commission_est"])
        self.assertGreater(rank["keto-snacks"]["clicks"], rank["best-fish-oil"]["clicks"])
        self.assertEqual(rank["keto-snacks"]["score"], rank["keto-snacks"]["commission_est"])
        # auth gate
        st2, _, _, _ = self._raw("/api/earnings/priority")
        self.assertEqual(st2, 401)

    def test_admin_priority_page(self):
        st, _, _, data = self._raw("/admin/priority", cookie=self.cookie)
        self.assertEqual(st, 200)
        html = data.decode("utf-8", "replace")
        self.assertIn("Prioritize", html)
        self.assertIn("commission", html)

    def test_variants_page_and_save(self):
        import json as _json
        # save an A/B variant for the keto niche
        st, body = self._raw_json(
            "/api/variants/save",
            {"slug": "keto-snacks", "variants": [
                {"variant": 1, "variant_headline": "Keto snacks in 12 picks, compared",
                 "enabled": 1}]},
            cookie=self.cookie)
        self.assertEqual(st, 200)
        d = _json.loads(body)
        self.assertTrue(d["ok"], d)
        # variants admin page lists it
        st, _, _, body = self._raw("/admin/variants?keyword=keto+snacks", cookie=self.cookie)
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("keto-snacks", html)
        self.assertIn("variant", html.lower())
        # a niche page served with the variant carries data-variant + headline
        st, _, _, body = self._raw("/n/keto-snacks")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn('data-variant="1"', html)
        self.assertIn("Keto snacks in 12 picks", html)
        # auth gate on save
        st, _, _, _ = self._raw("/api/variants/save", "POST",
                                body=b'{"slug":"x","variants":[]}')
        self.assertEqual(st, 401)

    def test_ab_autoclean_disables_losing_variant(self):
        # a niche with two variants where v2 is far behind v1 on Amazon clicks
        server._set_setting("ab.min_clicks", "4")
        slug = "ab-clean-test"
        with server._lock:
            conn = server._db()
            conn.execute("DELETE FROM clicks WHERE slug=?", (slug,))
            conn.execute("DELETE FROM niche_variants WHERE lower(slug)=?", (slug,))
            conn.execute(
                "INSERT INTO niche_variants (slug, variant, headline, enabled) "
                "VALUES (?,1,'Control',1),(?,2,'Loser',1)", (slug, slug))
            for _ in range(5):
                conn.execute("INSERT INTO clicks (slug, source, content) VALUES "
                             "(?,'page','ab-1')", (slug,))
            conn.execute("INSERT INTO clicks (slug, source, content) VALUES "
                         "(?,'page','ab-2')", (slug,))
            conn.commit()
            conn.close()
        try:
            st, body = self._raw_json("/api/variants/autoclean", {}, cookie=self.cookie)
        finally:
            server._set_setting("ab.min_clicks", "")
        self.assertEqual(st, 200)
        d = json.loads(body)
        self.assertTrue(d["ok"], d)
        v2 = next(v for v in d["variants"] if v["slug"] == slug and v["variant"] == 2)
        self.assertFalse(v2["enabled"])
        v1 = next(v for v in d["variants"] if v["slug"] == slug and v["variant"] == 1)
        self.assertTrue(v1["enabled"])

    def test_ab_autoclean_keeps_close_variants(self):
        # v2 at ~half of v1 (>=25%) is NOT disabled
        slug = "ab-clean-keep"
        with server._lock:
            conn = server._db()
            conn.execute("DELETE FROM clicks WHERE slug=?", (slug,))
            conn.execute("DELETE FROM niche_variants WHERE lower(slug)=?", (slug,))
            conn.execute(
                "INSERT INTO niche_variants (slug, variant, headline, enabled) "
                "VALUES (?,1,'A',1),(?,2,'B',1)", (slug, slug))
            for _ in range(50):
                conn.execute("INSERT INTO clicks (slug, content) VALUES (?,'ab-1')", (slug,))
            for _ in range(30):
                conn.execute("INSERT INTO clicks (slug, content) VALUES (?,'ab-2')", (slug,))
            conn.commit()
            conn.close()
        st, body = self._raw_json("/api/variants/autoclean", {}, cookie=self.cookie)
        self.assertEqual(st, 200)
        d = json.loads(body)
        self.assertTrue(d["ok"], d)
        v2 = next(v for v in d["variants"] if v["slug"] == slug and v["variant"] == 2)
        self.assertTrue(v2["enabled"])

    def test_ab_autoclean_requires_auth(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.PORT, timeout=8)
        conn.request("POST", "/api/variants/autoclean", body=b'{}',
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse(); resp.read(); conn.close()
        self.assertEqual(resp.status, 401)

    def test_paapi_settings_config_roundtrip(self):
        import paapi
        paapi.configure("", "", "")
        st, body = self._raw_json(
            "/api/settings", {"paapi": {"access_key": "AKIAX", "secret_key": "SEC",
                                        "partner_tag": "tag-20"}},
            cookie=self.cookie)
        self.assertEqual(st, 200)
        d = json.loads(body)
        self.assertTrue(d["paapi"]["ready"])
        # persisted to DB so it survives a restart (env still wins)
        self.assertEqual(server._get_setting("paapi.access_key"), "AKIAX")
        self.assertEqual(server._get_setting("paapi.partner_tag"), "tag-20")
        # auth gate
        st2, _ = self._raw_json("/api/settings", {"paapi": {}})
        self.assertEqual(st2, 401)

    def test_social_settings_persist_keys_and_webhook(self):
        # clear prior
        server._set_setting("social.key.twitter", "")
        server._set_setting("social.webhook", "")
        payload = {
            "social": {
                "webhook": "https://hook.example/zap",
                "keys": {"twitter": "token-ABC", "pinterest": ""},
            }
        }
        st, body = self._raw_json("/api/settings", payload, cookie=self.cookie)
        self.assertEqual(st, 200)
        d = json.loads(body)
        self.assertTrue(d["social"]["webhook"])
        self.assertTrue(d["social"]["keys"]["twitter"])
        # persisted
        self.assertEqual(server._get_setting("social.webhook"), "https://hook.example/zap")
        self.assertEqual(server._get_setting("social.key.twitter"), "token-ABC")

    def test_admin_apikeys_page(self):
        st, location, _, data = self._raw("/admin/apikeys")
        self.assertEqual(st, 302)
        self.assertTrue(location.startswith("/admin/login"))
        # seed a secret so the masking behaviour is visible
        server._set_setting("paapi.secret_key", "SuperSecretKeyValue")
        st, _, _, data = self._raw("/admin/apikeys", cookie=self.cookie)
        self.assertEqual(st, 200)
        html = data.decode("utf-8", "replace")
        self.assertIn("PA-API", html)
        self.assertIn('name="webhook"', html)
        self.assertIn('name="access_key"', html)
        # PA-API secret is not echoed in plaintext but shown masked
        self.assertNotIn("SuperSecretKeyValue", html)
        self.assertIn("••••", html)
        # the four twitter OAuth sub-fields are rendered
        self.assertIn('data-tw="1"', html)

    def test_ai_key_and_scraper_key_persist_to_db(self):
        # AI key survives via DB: persist, then re-init rehydrates the runtime.
        # Restore the runtime in a finally so an "openai" key never leaks into
        # later test modules (ai is a process-wide singleton; a leftover key
        # would make ai.configured() True and every later AI call hit the real
        # network, hanging offline tests).
        import ai as _ai
        saved_runtime = dict(_ai._RUNTIME)
        saved_openai_key = server._get_setting("ai.key.openai")
        try:
            st, body = self._raw_json("/api/keys/save",
                                      {"group": "ai", "keyid": "openai",
                                       "key": "sk-test-xyz", "model": "gpt-4o"},
                                      cookie=self.cookie)
            self.assertEqual(st, 200)
            d = json.loads(body)
            self.assertTrue(d.get("ok"))
            self.assertEqual(server._get_setting("ai.key.openai"), "sk-test-xyz")
            # simulate a restart: reload runtime from the DB
            _ai._RUNTIME.clear()
            server._init()
            cfg = _ai._runtime("openai")
            self.assertEqual(cfg.get("key"), "sk-test-xyz")
        finally:
            _ai._RUNTIME.clear()
            _ai._RUNTIME.update(saved_runtime)
            server._set_setting("ai.key.openai", saved_openai_key)
            server._init()
        # scraper key persists too
        st2, _ = self._raw_json("/api/keys/save",
                                {"group": "scraper", "keyid": "scraperapi",
                                 "key": "scraper-secret"},
                                cookie=self.cookie)
        self.assertEqual(server._get_setting("scraper.key.scraperapi"), "scraper-secret")

    def test_ai_models_curated_list_and_ebooks_selection_ui(self):
        import ai as _ai
        saved_runtime = dict(_ai._RUNTIME)
        try:
            # every provider exposes a non-empty curated model selection
            provs = _ai.providers()
            by_name = {p["name"]: p for p in provs}
            self.assertIn("models", by_name["mistral"])
            self.assertIn("open-mistral-7b", by_name["mistral"]["models"])
            self.assertIn("kimi-k2.5-free", by_name["opencode"]["models"])
            # models_for() includes the default first when not already present
            self.assertEqual(_ai.models_for("mistral")[0], "open-mistral-7b")
            # the /admin/ebooks page injects the curated map + seeds the datalist
            st, _ct, _sc, body = self._raw("/admin/ebooks", cookie=self.cookie)
            self.assertEqual(st, 200)
            html = body.decode("utf-8", "replace")
            self.assertIn("var MODELS", html)
            self.assertIn("kimi-k2.5-free", html)
            self.assertIn("open-mistral-7b", html)
            self.assertIn("ai-models", html)
            self.assertIn("fillModels", html)
        finally:
            _ai._RUNTIME.clear()
            _ai._RUNTIME.update(saved_runtime)

    def test_twitter_subkeys_map_in_publish_key_getter(self):
        server._set_setting("social.key.twitter", "BASE")
        server._set_setting("social.key.twitter.client_id", "CK")
        server._set_setting("social.key.twitter.client_secret", "CS")
        server._set_setting("social.key.twitter.access_token", "AT")
        server._set_setting("social.key.twitter.access_token_secret", "ATS")
        kv = server._publish_key_getter()
        # single-token platforms resolve any field to the base key
        self.assertEqual(kv("pinterest", "token"), server._get_setting("social.key.pinterest"))
        # twitter resolves each slot to its distinct sub-key
        self.assertEqual(kv("twitter", "client_id"), "CK")
        self.assertEqual(kv("twitter", "client_secret"), "CS")
        self.assertEqual(kv("twitter", "access_token"), "AT")
        self.assertEqual(kv("twitter", "access_token_secret"), "ATS")
        # unknown sub-field falls back to the base value
        self.assertEqual(kv("twitter", "bogus"), "BASE")
        server._set_setting("social.key.twitter", "")
        for f in ("client_id", "client_secret", "access_token", "access_token_secret"):
            server._set_setting("social.key.twitter.%s" % f, "")

    def test_paapi_partial_save_preserves_other_fields(self):
        # save all three, then re-save only partner_tag (as the masked JS does):
        # the untouched access/secret keys must not be blanked
        self._raw_json("/api/settings",
                       {"paapi": {"access_key": "AKIAX", "secret_key": "SEC",
                                  "partner_tag": "tag-20"}},
                       cookie=self.cookie)
        self._raw_json("/api/settings", {"paapi": {"partner_tag": "tag-21"}},
                       cookie=self.cookie)
        self.assertEqual(server._get_setting("paapi.access_key"), "AKIAX")
        self.assertEqual(server._get_setting("paapi.secret_key"), "SEC")
        self.assertEqual(server._get_setting("paapi.partner_tag"), "tag-21")

    def test_opportunities_lists_clicked_winners(self):
        # seed click data pointing at a saved niche
        with server._lock:
            conn = server._db()
            conn.execute("DELETE FROM clicks")
            conn.execute(
                "INSERT INTO clicks (slug, source, asin) VALUES "
                "('keto-snacks','social','B0KETO1234'),"
                "('keto-snacks','social','B0KETO5678')")
            conn.commit()
            conn.close()
        orig = amazon.autosuggest
        amazon.autosuggest = lambda q, limit=12: ["keto snacks bars", "keto chips"]
        try:
            st, _, _, data = self._raw("/api/opportunities", cookie=self.cookie)
        finally:
            amazon.autosuggest = orig
        self.assertEqual(st, 200)
        d = json.loads(data)
        winners = {w["slug"]: w for w in d["winners"]}
        self.assertIn("keto-snacks", winners)
        self.assertEqual(winners["keto-snacks"]["clicks"], 2)
        self.assertIn("unbuilt", winners["keto-snacks"])
        # auth gate
        st2, _, _, _ = self._raw("/api/opportunities")
        self.assertEqual(st2, 401)

    def test_opportunities_expand_creates_pages(self):
        # stub mining to return a related term + product so expand auto-saves it
        import niche as niche_mod
        def fake_mine(seed, top=8, max_niches=5):
            return [
                {"keyword": seed, "products": [], "score": 1, "saturation": 0},
                {"keyword": seed + " bars", "products": [
                    {"asin": "B0KETO1234", "title": "Keto Bar", "reviews": 50}],
                 "score": 1, "saturation": 0},
            ], {"seed": seed}
        orig = niche_mod.mine_niche
        niche_mod.mine_niche = fake_mine
        try:
            st, data = self._raw_json(
                "/api/opportunities/expand", {"slug": "keto-snacks", "count": 2},
                cookie=self.cookie)
        finally:
            niche_mod.mine_niche = orig
        self.assertEqual(st, 200)
        d = json.loads(data)
        self.assertTrue(d["ok"])
        self.assertEqual(d["seed"], "keto snacks")
        self.assertEqual(d["count"], 1)
        self.assertEqual(d["created"][0]["keyword"], "keto snacks bars")
        self.assertEqual(d["created"][0]["products"], 1)

    def test_opportunities_expand_requires_auth(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.PORT, timeout=8)
        conn.request("POST", "/api/opportunities/expand",
                     body=b'{"slug":"keto-snacks"}',
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse(); resp.read(); conn.close()
        self.assertEqual(resp.status, 401)

    def test_admin_opportunities_page(self):
        orig = amazon.autosuggest
        amazon.autosuggest = lambda q, limit=12: ["keto snacks bars"]
        try:
            st, _, _, data = self._raw("/admin/opportunities", cookie=self.cookie)
        finally:
            amazon.autosuggest = orig
        self.assertEqual(st, 200)
        html = data.decode("utf-8", "replace")
        self.assertIn("Build", html)
        st2, location, _, _ = self._raw("/admin/opportunities")
        self.assertEqual(st2, 302)
        self.assertTrue(location.startswith("/admin/login"))

    def test_seo_page_embeds_upsell_block(self):
        st, _, _, body = self._raw("/n/keto-snacks")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn('data-role="upsell"', html)

    def test_niche_render_cache_adds_cache_control(self):
        # in prod cache-awareness (CACHE_TTL>0) the /n/ page ships Cache-Control
        old_ttl, amazon.CACHE_TTL = amazon.CACHE_TTL, 3600
        server._render_cache.clear()
        try:
            import http.client
            conn = http.client.HTTPConnection("127.0.0.1", self.PORT, timeout=5)
            conn.request("GET", "/n/keto-snacks")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            cc = resp.getheader("Cache-Control") or ""
            self.assertIn("public", cc.lower())
            self.assertIn("max-age=", cc.lower())
            resp.read()
            conn.close()
        finally:
            amazon.CACHE_TTL = old_ttl
            server._render_cache.clear()

    def test_render_niche_with_ab_headline(self):
        html = seo.render_niche("keto snacks", {"products": []}, ab_headline="Custom A/B H1",
                            ab_variant=2)
        self.assertIn("Custom A/B H1", html.decode("utf-8", "replace"))
        self.assertIn('data-variant="2"', html.decode("utf-8", "replace"))

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

    def test_boosts_api_requires_auth(self):
        st, _, _, body = self._raw("/api/boosts?keyword=keto+snacks")
        self.assertEqual(st, 401)

    def test_boosts_run_api(self):
        import json as _json
        st, _, _, body = self._raw("/api/boosts/run", "POST",
                                   body=b"keyword=keto+snacks", cookie=self.cookie)
        self.assertEqual(st, 200, body)
        d = _json.loads(body)
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["ran"], 5)
        self.assertEqual(d["runs_total"], 5)
        self.assertEqual(d["sem_keywords"], [])
        self.assertGreaterEqual(len(d["boosts"]), 5)
        for b in d["boosts"]:
            self.assertIn("utm_source=boost", b["link"], b)
            self.assertIn("/lp/keto-snacks?", b["link"], b)
            self.assertIn("utm_content=", b["link"], b)
            self.assertTrue(b["code"], b)
            self.assertEqual(b["target"], "landing")
            self.assertEqual(b["runs"], 1, b)
        st, _, _, body = self._raw("/api/boosts/run", "POST",
                                   body=b"keyword=keto+snacks&names=Bundle+stack",
                                   cookie=self.cookie)
        d = _json.loads(body)
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["ran"], 1)
        self.assertEqual(d["runs_total"], 6)
        bundle = next(b for b in d["boosts"] if b["name"] == "Bundle stack")
        self.assertEqual(bundle["runs"], 2)
        # click the tracked link -> per-campaign attribution on the read API
        st, _, _, body = self._raw(
            "/api/track", "POST",
            body=("slug=keto-snacks&content=%s" % bundle["code"]).encode(),
            cookie=self.cookie)
        self.assertEqual(st, 200)
        st, _, body = self._get("/api/boosts?keyword=keto+snacks")
        d = _json.loads(body)
        bundle = next(b for b in d["boosts"] if b["name"] == "Bundle stack")
        self.assertEqual(bundle["clicks"], 1)

    def test_boosts_run_unknown_keyword_404(self):
        import json as _json
        st, _, _, body = self._raw("/api/boosts/run", "POST",
                                   body=b"keyword=no-such-niche", cookie=self.cookie)
        self.assertEqual(st, 404)
        self.assertIn("saved niche", _json.loads(body)["error"])

    def test_social_publish_defaults_to_all_platforms(self):
        """POST /api/social/publish without a platform must default to "all"
        and build/publish every platform kit, not silently match zero (footgun
        fixed: the handler previously defaulted to an empty platform string)."""
        import json as _json
        st, _, _, body = self._raw("/api/social/publish", "POST",
                                   body=b"keyword=keto+snacks", cookie=self.cookie)
        self.assertEqual(st, 200, body)
        out = _json.loads(body)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["published"], 6, out)
        self.assertEqual(len(out["posts"]), 6, out)
        self.assertEqual({p["platform"] for p in out["posts"]},
                         {"Twitter / X", "Facebook", "LinkedIn",
                          "Instagram", "Pinterest", "Threads"})

    def test_boosts_to_social_publishes_relink(self):
        import json as _json
        from urllib.parse import urlencode
        st, _, _, body = self._raw("/api/boosts/run", "POST",
                                   body=b"keyword=keto+snacks", cookie=self.cookie)
        d = _json.loads(body)
        first = d["boosts"][0]
        st, _, _, body = self._raw(
            "/api/boosts/social", "POST",
            body=urlencode({"keyword": "keto snacks",
                            "campaign": first["name"]}).encode(),
            cookie=self.cookie)
        self.assertEqual(st, 200, body)
        out = _json.loads(body)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["post"]["utm_content"], first["code"])
        self.assertIn("Boost", out["post"]["name"])
        self.assertIn("/lp/keto-snacks?utm_source=boost", out["post"]["link"])
        st, ctype, body = self._get("/admin/social?keyword=keto+snacks")
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        self.assertIn("Boost", html)
        self.assertIn("boost", html.lower())

    def test_public_post_page_renders_actual_post(self):
        """The "View live" / "open <a>" buttons must lead to the actual published
        post (/social/<slug>/<code>), not the landing page, and the post page
        must be publicly reachable without a cookie."""
        import json as _json
        from urllib.parse import urlencode
        st, _, _, body = self._raw("/api/boosts/run", "POST",
                                   body=b"keyword=keto+snacks", cookie=self.cookie)
        d = _json.loads(body)
        first = d["boosts"][0]
        st, _, _, body = self._raw(
            "/api/boosts/social", "POST",
            body=urlencode({"keyword": "keto snacks",
                            "campaign": first["name"]}).encode(),
            cookie=self.cookie)
        self.assertEqual(st, 200, body)
        out = _json.loads(body)
        code = out["post"]["utm_content"]
        # public page, no cookie -> the actual post copy, not a landing redirect
        st, _, _, body = self._raw("/social/keto-snacks/" + code)
        self.assertEqual(st, 200)
        html = body.decode("utf-8", "replace")
        firstline = first["script"].split("\n")[0]
        self.assertIn(firstline, html)
        self.assertIn("noindex", html)
        self.assertIn("keto", html.lower())
        # full social-rich preview meta on the public post page
        self.assertIn('property="og:title"', html)
        self.assertIn('property="og:description"', html)
        self.assertIn('name="twitter:card"', html)
        self.assertIn('rel="canonical"', html)
        # admin social page's live links point at the post page, not /lp/
        st, _, _, body = self._raw("/admin/social?keyword=keto+snacks", cookie=self.cookie)
        self.assertEqual(st, 200)
        ah = body.decode("utf-8", "replace")
        self.assertIn("/social/keto-snacks/" + code, ah)
        self.assertNotIn("href=\"/lp/keto-snacks?", ah)
        # a bogus code 404s
        st, _, _, _ = self._raw("/social/keto-snacks/nope123")
        self.assertEqual(st, 404)

    def test_boosts_to_social_requires_campaign(self):
        import json as _json
        st, _, _, body = self._raw("/api/boosts/social", "POST",
                                   body=b"keyword=keto+snacks", cookie=self.cookie)
        self.assertEqual(st, 400)

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

    def test_cron_send_requires_secret(self):
        st, _, _, body = self._raw("/api/cron/send", "POST",
                                   body=b"limit=2",
                                   extra_headers={"X-Cron-Secret": "nope"})
        self.assertEqual(st, 401)  # EMAIL_CRON_SECRET unset -> auth gate
        self.assertIn(b"unauthorized", body)

    def test_cron_send_secret_grants_access(self):
        import server as srv
        saved = srv._CRON_SECRET
        srv._CRON_SECRET = "cron-token-abc"
        try:
            st, _, _, body = self._raw("/api/cron/send", "POST",
                                       body=b"limit=2",
                                       extra_headers={"X-Cron-Secret": "cron-token-abc"})
            payload = json.loads(body)
            self.assertEqual(st, 200)
            self.assertIn("ok", payload)
        finally:
            srv._CRON_SECRET = saved

    def test_cron_send_secret_wrong_is_rejected(self):
        import server as srv
        saved = srv._CRON_SECRET
        srv._CRON_SECRET = "cron-token-abc"
        try:
            st, _, _, body = self._raw("/api/cron/send", "POST",
                                       body=b"limit=2",
                                       extra_headers={"X-Cron-Secret": "wrong"})
            self.assertEqual(st, 401)
            self.assertIn(b"unauthorized", body)
        finally:
            srv._CRON_SECRET = saved

    def test_autosend_disabled_when_env_unset(self):
        import server as srv
        saved = srv._AUTOSEND_HOURS
        srv._AUTOSEND_HOURS = []
        try:
            self.assertEqual(srv._autosend_tick(), "idle")
        finally:
            srv._AUTOSEND_HOURS = saved

    def test_autosend_tick_runs_slot_and_marks_done(self):
        import datetime as dt
        import server as srv
        saved_hours, saved_marker = srv._AUTOSEND_HOURS, srv._AUTOSEND_LAST_KEY
        srv._AUTOSEND_HOURS = [dt.datetime.utcnow().hour]
        srv._AUTOSEND_LAST_KEY = "autosend.test_last"
        try:
            first = srv._autosend_tick()
            self.assertIn(first, ("sent", "fail"))  # no subscribers -> sent(ok) or smtp-unconfigured
            second = srv._autosend_tick()
            self.assertEqual(second, "done")  # marker persisted, slot won't resend
        finally:
            srv._AUTOSEND_HOURS, srv._AUTOSEND_LAST_KEY = saved_hours, saved_marker

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

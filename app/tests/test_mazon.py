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

    def test_text_links_include_redirect(self):
        out = market_engine.build_text_links(
            [{"asin": "B0KETO1234", "title": "Keto Bar", "reviews": 10}])
        self.assertIn("/go/B0KETO1234", out)

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
        self.assertIn("/go/B0KETO1234", d)

    def test_redirect_expands_to_affiliate(self):
        url, market = market_engine.expand_go("B0KETO1234")
        self.assertIn("amazon.com/dp/B0KETO1234?tag=yourname-20", url)


class TestSEO(unittest.TestCase):
    def test_niche_page_crawlable(self):
        html = seo.render_niche("keto snacks", {
            "products": [{"asin": "B0KETO1234", "title": "Keto Bar",
                          "price": 12.99, "stars": 4.5, "reviews": 10,
                          "url": "/go/B0KETO1234"}],
            "source": "amazon"}).decode("utf-8")
        self.assertIn("<title>", html)
        self.assertIn('rel="canonical"', html)
        self.assertIn("@type", html)
        self.assertIn("/go/B0KETO1234", html)

    def test_sitemap(self):
        s = seo.render_sitemap([("/", "2026-08-28"), ("/n/keto-snacks", "2026-08-28")])
        self.assertIn(b"/n/keto-snacks", s)
        self.assertIn(b"<urlset", s)

    def test_robots(self):
        self.assertIn(b"Sitemap:", seo.render_robots())


if __name__ == "__main__":
    unittest.main()

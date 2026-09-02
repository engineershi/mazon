# -*- coding: utf-8 -*-
import json
import os
import shutil
import sys
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amazon
import editorial
import seo
import server


class _Handler(BaseHTTPRequestHandler):
    handler = None
    received = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = self._read()
        self._write(body)

    def do_POST(self):
        body = self._read()
        self._write(body)

    def _read(self):
        self.received.append(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        self._req = self.path
        self._body = raw
        return raw

    def _write(self, payload):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode("utf-8")
        if self.headers.get("Authorization") == "Bearer natetok":
            # native gateway stub hit
            self.send_response(201)
        else:
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload if isinstance(payload, bytes) else str(payload).encode())


class TestTopics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = "/tmp/pstore_topic_%s.db" % uuid.uuid4().hex[:8]
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "pstore.db"), cls.db)
        os.environ["PSTORE_DB"] = cls.db
        import importlib
        importlib.reload(server)
        with server._lock:
            c = server._db()
            c.execute("DELETE FROM niches")
            c.execute("DELETE FROM topics")
            c.execute("INSERT INTO niches (keyword, market, products) VALUES (?,?,?)",
                      ("keto snacks", "com",
                       json.dumps([{"asin": "B0KETO", "title": "Keto Bar",
                                    "reviews": 50, "stars": 4.5,
                                    "price": 9.99, "currency": "USD",
                                    "url": "https://www.amazon.com/dp/B0KETO"}])))
            c.commit()
            c.close()

    def test_generate_topics_from_autosuggest(self):
        orig = amazon.autosuggest
        amazon.autosuggest = lambda kw, limit=12: [
            "keto snack bars", "keto snacks for weight loss", "keto chips", "a"]
        try:
            created = server.Handler._generate_topics(
                server.Handler.__new__(server.Handler), "keto-snacks", 5)
        finally:
            amazon.autosuggest = orig
        slugs = [c["slug"] for c in created]
        self.assertEqual(len(slugs), 3)  # 'a' is too short, filtered
        self.assertIn("keto-snack-bars", slugs)
        self.assertIn("keto-snacks-for-weight-loss", slugs)

    def test_topic_page_renders_and_is_indexable(self):
        # ensure at least one topic exists
        orig = amazon.autosuggest
        amazon.autosuggest = lambda kw, limit=12: ["keto snack bars", "b", "c", "d"]
        try:
            server.Handler._generate_topics(server.Handler.__new__(server.Handler), "keto-snacks", 2)
        finally:
            amazon.autosuggest = orig
        topics = server.Handler._topics_for(server.Handler.__new__(server.Handler), "keto-snacks")
        self.assertTrue(topics, "expected a generated topic")
        t = topics[0]
        niche = {"products": [{"asin": "B0KETO", "title": "Keto Bar", "reviews": 50,
                               "stars": 4.5, "price": 9.99,
                               "url": "https://www.amazon.com/dp/B0KETO"}]}
        html = seo.render_topic(t["term"], "keto snacks", niche, "keto-snacks").decode("utf-8")
        # unique H1 intent + no noindex + schema + corpus
        self.assertIn("Best %s" % t["term"], html)
        self.assertNotIn('name="robots" content="noindex', html)
        self.assertIn("application/ld+json", html)
        self.assertIn("ItemList", html)
        # canonical points at the nested URL so it's a distinct page
        self.assertIn("/n/keto-snacks/%s" % t["slug"], html)

    def test_item_list_jsonld_ranks_products(self):
        il = editorial.item_list_jsonld(
            [{"title": "A", "url": "https://a"}, {"title": "B", "url": "https://b"}],
            "keto snacks")
        self.assertEqual(il["@type"], "ItemList")
        pos = [e["position"] for e in il["itemListElement"]]
        self.assertEqual(pos, [1, 2])

    def test_sticky_cta_and_urgency_present(self):
        niche = {"products": [{"asin": "B0KETO", "title": "Keto Bar", "reviews": 50,
                               "stars": 4.5, "price": 9.99,
                               "url": "https://www.amazon.com/dp/B0KETO"}]}
        html = seo.render_niche("keto snacks", niche).decode("utf-8")
        self.assertIn("sticky-cta", html)
        self.assertIn('data-ev="sticky"', html)
        self.assertIn("prices pulled live", html.lower())

    def test_sitemap_includes_topic(self):
        sitemap = server.Handler._sitemap(server.Handler.__new__(server.Handler)).decode("utf-8")
        topics = server.Handler._topics_for(server.Handler.__new__(server.Handler), "keto-snacks")
        if topics:
            self.assertIn("/n/keto-snacks/%s" % topics[0]["slug"], sitemap)


if __name__ == "__main__":
    import unittest
    unittest.main()
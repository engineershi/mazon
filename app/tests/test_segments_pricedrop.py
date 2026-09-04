# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

import pricedrop
import segments


class PriceParseTest(unittest.TestCase):
    def test_dollar(self):
        self.assertEqual(pricedrop.parse_price("$12.99"), 12.99)

    def test_euro_comma(self):
        self.assertEqual(pricedrop.parse_price("\u20ac9,99"), 9.99)

    def test_plain(self):
        self.assertEqual(pricedrop.parse_price("12.99"), 12.99)
        self.assertEqual(pricedrop.parse_price("12"), 12.0)

    def test_from_prefix(self):
        self.assertEqual(pricedrop.parse_price("from $10"), 10.0)

    def test_garbage(self):
        self.assertIsNone(pricedrop.parse_price(""))
        self.assertIsNone(pricedrop.parse_price(None))
        self.assertIsNone(pricedrop.parse_price("abc"))

    def test_euro_dot_comma(self):
        self.assertEqual(pricedrop.parse_price("1.234,56"), 1234.56)
        self.assertEqual(pricedrop.parse_price("1,234.56"), 1234.56)


class ComputeDropTest(unittest.TestCase):
    def test_no_change(self):
        r = pricedrop.compute_drop(10.0, 10.0)
        self.assertFalse(r["dropped"])

    def test_small_drop_below_threshold(self):
        r = pricedrop.compute_drop(100.0, 97.0)  # 3% < 5%
        self.assertFalse(r["dropped"])

    def test_real_drop(self):
        r = pricedrop.compute_drop(100.0, 90.0)  # 10% >= 5%, $10 >= $2
        self.assertTrue(r["dropped"])
        self.assertEqual(r["drop"], 10.0)
        self.assertEqual(r["drop_pct"], 10.0)

    def test_dollar_min_floor(self):
        r = pricedrop.compute_drop(100.0, 99.0, min_drop_abs=5.0)
        self.assertFalse(r["dropped"])

    def test_rise_never_drops(self):
        r = pricedrop.compute_drop(10.0, 12.0)
        self.assertFalse(r["dropped"])

    def test_missing(self):
        self.assertFalse(pricedrop.compute_drop(None, 9.0)["dropped"])
        self.assertFalse(pricedrop.compute_drop(10.0, None)["dropped"])


class CheckTest(unittest.TestCase):
    def test_pure_no_store(self):
        rows = [{"asin": "ABC1", "title": "Widget", "price": 100.0}]
        res = pricedrop.check(rows, {"ABC1": 80.0})
        self.assertEqual(res["checked"], 1)
        self.assertEqual(len(res["drops"]), 1)
        self.assertEqual(res["drops"][0]["drop"], 20.0)
        self.assertEqual(res["drops"][0]["asin"], "ABC1")

    def test_no_fresh_price_skips(self):
        rows = [{"asin": "ABC1", "title": "Widget", "price": 100.0}]
        res = pricedrop.check(rows, {})
        self.assertEqual(res["drops"], [])

    def test_no_change(self):
        rows = [{"asin": "ABC1", "title": "Widget", "price": 100.0}]
        res = pricedrop.check(rows, {"ABC1": 100.0})
        self.assertEqual(res["drops"], [])


class PriceStoreTest(unittest.TestCase):
    def test_persist_roundtrip(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "pd.json")
        a = pricedrop.PriceStore(path)
        self.assertIsNone(a.baseline("ABC1"))
        a.set_baseline("ABC1", 99.99)
        b = pricedrop.PriceStore(path)
        self.assertEqual(b.baseline("ABC1"), 99.99)


class DropEmailTest(unittest.TestCase):
    def test_empty(self):
        e = pricedrop.drop_email([])
        self.assertEqual(e["subject"], "")

    def test_body(self):
        e = pricedrop.drop_email([{
            "asin": "ABC1", "title": "Widget", "old": 100.0,
            "new": 80.0, "drop": 20.0, "drop_pct": 20.0}])
        self.assertIn("Price dropped", e["subject"])
        self.assertIn("Widget", e["html"])
        self.assertIn("$80.00", e["text"])
        self.assertIn("unsubscribe", e["html"].lower())


class SegmentsTest(unittest.TestCase):
    def test_cold(self):
        s = segments.score_one("a@b.c", opens=0, clicks=0)
        self.assertEqual(s["segment"], "cold")

    def test_warm(self):
        s = segments.score_one("a@b.c", opens=1, clicks=0)
        self.assertEqual(s["segment"], "warm")

    def test_hot(self):
        s = segments.score_one("a@b.c", opens=1, clicks=2)
        self.assertEqual(s["segment"], "hot")

    def test_converted(self):
        s = segments.score_one("a@b.c", opens=1, clicks=1, clicked_asin=True)
        self.assertEqual(s["segment"], "converted")

    def test_inactive(self):
        s = segments.score_one("a@b.c", opens=0, clicks=0, unsubscribed=True)
        self.assertEqual(s["segment"], "inactive")

    def test_build_report(self):
        rows = [
            {"email": "h@x", "opens": 2, "clicks": 3, "clicked_asin": 0, "confirmed": 1, "unsubscribed": 0},
            {"email": "w@x", "opens": 1, "clicks": 0, "clicked_asin": 0, "confirmed": 1, "unsubscribed": 0},
            {"email": "c@x", "opens": 0, "clicks": 0, "clicked_asin": 0, "confirmed": 1, "unsubscribed": 0},
            {"email": "v@x", "opens": 0, "clicks": 0, "clicked_asin": 0, "confirmed": 1, "unsubscribed": 1},
        ]
        r = segments.build_report(rows)
        self.assertEqual(r["counts"]["hot"], 1)
        self.assertEqual(r["counts"]["warm"], 1)
        self.assertEqual(r["counts"]["cold"], 1)
        self.assertEqual(r["counts"]["inactive"], 1)
        self.assertEqual(r["total"], 4)

    def test_next_action(self):
        self.assertIn("urgency", segments.next_action("hot")["angle"].lower())
        self.assertIn("re-engage", segments.next_action("cold")["angle"].lower())


if __name__ == "__main__":
    unittest.main()

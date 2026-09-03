# -*- coding: utf-8 -*-
"""Offline hermetic tests for the demography-driven AUTO SUGGESTION engine
(suggest.py). amazon.autosuggest + amazon.search both bottom out in
amazon._urlopen, so stubbing that keeps everything offline and fast."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amazon
import suggest


def _fake_urlopen(url, *a, **k):
    """Shared fake: autosuggest returns buyer-ish keywords for any seed; search
    returns a couple of plausible products. Never makes a real network call."""
    if "completion.amazon.com" in str(url):
        data = ('{"suggestions":['
                '{"value":"fashion outfit"},'
                '{"value":"fashion accessories"},'
                '{"value":"fashion for women"},'
                '{"value":"best fashion"},'
                '{"value":"fashion bag"}]}').encode()
        return data
    html = ('<html><div data-asin="B0X1">'
            '<h2>Fashion Top Pick</h2><span class="a-price-whole">20</span>'
            '<span class="a-icon-alt">4.5 out of 5 stars</span>'
            '<span class="a-size-base">120</span></div>'
            '<div data-asin="B0X2"><h2>Fashion Runner Up</h2></div></html>').encode()
    return html


class TestSuggestHermetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        amazon.CACHE_TTL = 0
        amazon.MIN_INTERVAL_MS = 0
        cls._saved_urlopen = amazon._urlopen
        amazon._urlopen = _fake_urlopen

    @classmethod
    def tearDownClass(cls):
        amazon._urlopen = cls._saved_urlopen

    def test_seed_queries_from_interest(self):
        seeds = suggest.seed_queries({"interest": "Fashion"})
        self.assertEqual(seeds[0], "fashion")

    def test_seed_queries_behavior_and_region(self):
        seeds = suggest.seed_queries({"interest": "Fashion",
                                      "behavior": "Budget",
                                      "region": "United States"})
        self.assertIn("fashion", seeds)
        # behavior affordance terms are present
        self.assertTrue(any("under" in s for s in seeds))

    def test_seed_queries_empty_falls_back(self):
        seeds = suggest.seed_queries({})
        self.assertTrue(seeds)

    def test_suggest_returns_ranked_build_ready_rows(self):
        out = suggest.suggest_niches({"interest": "Fashion"})
        self.assertIn("suggestions", out)
        self.assertTrue(out["suggestions"])
        top = out["suggestions"][0]
        self.assertIn("keyword", top)
        self.assertIn("score", top)
        self.assertIn("products", top)
        self.assertIn("reason", top)
        self.assertGreaterEqual(top["count"], 0)
        # ranked descending by score
        scores = [s["score"] for s in out["suggestions"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_persona_match_rewards_profile_echo(self):
        self.assertGreater(suggest._match_persona("fashion for women",
                                                  {"interest": "Fashion",
                                                   "audience": "women"}),
                           suggest._match_persona("random basket",
                                                  {"interest": "Fashion"}))

    def test_build_route_returns_mined_niche(self):
        out = suggest.build_route("fashion")
        self.assertIn("niche", out)
        self.assertEqual(out["niche"]["keyword"], "fashion")
        self.assertIn("meta", out)


if __name__ == "__main__":
    unittest.main()
# -*- coding: utf-8 -*-
"""Offline tests for the earnings/conversion estimator + analytics routes."""
import json
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import earnings


class TestEarningsEstimator(unittest.TestCase):
    def tearDown(self):
        earnings._runtime.clear()

    def test_default_estimate(self):
        est = earnings.estimate(100)
        self.assertEqual(est["clicks"], 100)
        self.assertAlmostEqual(est["orders_est"], 3.0)
        self.assertAlmostEqual(est["commission_est"], 4.8)

    def test_zero_clicks(self):
        est = earnings.estimate(0)
        self.assertEqual(est["clicks"], 0)
        self.assertAlmostEqual(est["commission_est"], 0.0)

    def test_configure_overrides(self):
        earnings.configure(commission_pct=8.0, avg_order=60.0, order_rate=0.05)
        est = earnings.estimate(100)
        self.assertAlmostEqual(est["commission_est"], 24.0)
        self.assertAlmostEqual(est["orders_est"], 5.0)

    def test_negative_clicks_floored(self):
        self.assertEqual(earnings.estimate(-5)["clicks"], 0)

    def test_category_rate(self):
        self.assertEqual(earnings.commission_pct("beauty"), 10.0)
        self.assertAlmostEqual(earnings.commission_pct("unknown"), earnings.DEFAULT_COMMISSION_PCT)

    def test_aggregate_total(self):
        rows = [{"clicks": 10}, {"clicks": 5, "category": "books"}]
        agg = earnings.aggregate(rows, lambda r: r.get("category", ""))
        self.assertEqual(agg["total"]["clicks"], 15)
        self.assertEqual(set(agg["by_category"].keys()), {"default", "books"})

    def test_monthly_summary(self):
        s = earnings.monthly_summary([{"orders": 3, "earnings": 9.0}])
        self.assertEqual(s["total_orders"], 3)
        self.assertAlmostEqual(s["total_earnings"], 9.0)


if __name__ == "__main__":
    unittest.main()
# -*- coding: utf-8 -*-
import json
import unittest
import urllib.request

import amazon
import paapi


def _reset_cfg():
    paapi._configure_clear()


class FakeResponse:
    def __init__(self, code=200, body=b"{}"):
        self.code = code
        self._body = body

    def read(self):
        return self._body


class Base(unittest.TestCase):
    def setUp(self):
        _reset_cfg()
        self._orig_urlopen = amazon._urlopen
        self.captured = {}

    def tearDown(self):
        amazon._urlopen = self._orig_urlopen
        _reset_cfg()

    def _stub(self, payload):
        def fake(req, timeout=None):
            self.captured["url"] = req.full_url
            self.captured["body"] = req.data
            self.captured["method"] = req.get_method()
            self.captured["headers"] = dict(req.headers)
            self.captured["unredirected"] = dict(req.unredirected_hdrs)
            return FakeResponse(200, json.dumps(payload).encode("utf-8"))
        amazon._urlopen = fake


class TestPaapiGate(Base):
    def test_disabled_without_creds(self):
        self.assertEqual(paapi.get_items(["B0KETO1234"]), None)
        self.assertEqual(paapi.lookup("B0KETO1234"), None)
        self.assertFalse(paapi.ready())

    def test_configure_and_status(self):
        paapi.configure("ACC", "SEC", "tag-20")
        self.assertTrue(paapi.ready())
        st = paapi.status()
        self.assertTrue(st["has_access_key"])
        self.assertNotIn("ACC", json.dumps(st))  # masked / not echoed


class TestPaapiRequest(Base):
    def setUp(self):
        super().setUp()
        paapi.configure("AKIAEXAMPLE", "SECRETSECRET", "tag-20")

    def test_get_items_sends_signed_post_to_paapi(self):
        self._stub({"ItemsResult": {"Items": []}})
        paapi.get_items(["b0keto1234"])
        h = {k.lower(): v for k, v in self.captured["headers"].items()}
        self.assertEqual(self.captured["method"], "POST")
        self.assertTrue(self.captured["url"].startswith(
            "https://webservices.amazon.com/paapi5/getitems"))
        auth = h.get("authorization", "")
        self.assertIn("AWS4-HMAC-SHA256", auth)
        self.assertIn("ProductAdvertisingAPI", auth)
        self.assertIn("AKIAEXAMPLE", auth)
        body = json.loads(self.captured["body"])
        self.assertEqual(body["PartnerTag"], "tag-20")
        self.assertEqual(body["ItemIds"], ["B0KETO1234"])
        self.assertEqual(h["content-encoding"], "amz-1.0")

    def test_lookup_normalizes_item(self):
        payload = {
            "ItemsResult": {"Items": [{
                "ASIN": "B0KETO1234",
                "ItemInfo": {"Title": {"DisplayValue": "Keto Bar Crunch"}},
                "Offers": {"Listings": [
                    {"Price": {"Amount": "12.99", "Currency": "USD"}}]},
            }]}
        }
        self._stub(payload)
        it = paapi.lookup("B0KETO1234")
        self.assertEqual(it["asin"], "B0KETO1234")
        self.assertEqual(it["title"], "Keto Bar Crunch")
        self.assertEqual(it["price"], "12.99")
        self.assertEqual(it["currency"], "USD")
        self.assertEqual(it["source"], "paapi")
        self.assertTrue(it["url"].startswith("https://"))

    def test_lookup_returns_none_when_asin_not_in_results(self):
        self._stub({"ItemsResult": {"Items": [{"ASIN": "B0OTHER0"}]}})
        self.assertIsNone(paapi.lookup("B0KETO1234"))


if __name__ == "__main__":
    unittest.main()
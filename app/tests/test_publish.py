# -*- coding: utf-8 -*-
import json
import unittest

import publish


class FakeResp:
    def __init__(self, code, body=b"{}"):
        self.code = code
        self._body = body

    def status(self):
        return self.code

    def read(self):
        return self._body


class Base(unittest.TestCase):
    def setUp(self):
        self._orig_post = publish._post
        self._orig_img = publish.og_image
        publish.og_image = lambda url: "https://example.com/og.png"
        self.captured = []
        self.requests = []

    def tearDown(self):
        publish._post = self._orig_post
        publish.og_image = self._orig_img

    def _ok(self, payload=None):
        def fake(url, payload_, headers, timeout=15):
            self.requests.append((url, payload_, headers))
            return 201, (payload or json.loads('{"id":"r1"}' if url.endswith("/pins") else '{}'))
        publish._post = fake

    def _keys(self, mapping=None):
        mapping = mapping or {}
        def kv(ns, name):
            return mapping.get((ns, name), "")
        return kv

    def _kit(self, platform, body="Hello world"):
        return {"platform": platform, "slug": "keto", "body": body,
                "link": "https://x/lp/keto?utm_content=ab", "name": "post"}


class TestNativeScaffold(Base):
    def test_twitter_requires_keys_then_posts(self):
        self._ok()
        res = publish.post_to("Twitter / X", self._kit("Twitter / X"),
                              self._keys({("twitter", "client_id"): "K",
                                          ("twitter", "client_secret"): "S",
                                          ("twitter", "token"): "T",
                                          ("twitter", "token_secret"): "TS"}))
        self.assertTrue(res["ok"])
        self.assertEqual(res["via"], "native")
        url = self.requests[0][0]
        self.assertIn("api.twitter.com/2/tweets", url)
        self.assertIn("OAuth", self.requests[0][2].get("Authorization", ""))

    def test_platform_skipped_without_keys(self):
        res = publish.post_to("Twitter / X", self._kit("Twitter / X"),
                              self._keys())
        self.assertFalse(res["ok"])
        self.assertEqual(res["via"], "skipped")
        self.assertEqual(self.requests, [])  # no network hit

    def test_pinterest_posts_with_token(self):
        self._ok()
        res = publish.post_to("Pinterest", self._kit("Pinterest"),
                              self._keys({("pinterest", "token"): "PIN"}))
        self.assertTrue(res["ok"])
        self.assertEqual(res["via"], "native")
        self.assertIn("api.pinterest.com/v5/pins", self.requests[0][0])

    def test_facebook_posts_with_token(self):
        self._ok()
        res = publish.post_to("Facebook", self._kit("Facebook"),
                              self._keys({("facebook", "token"): "FBTK"}))
        self.assertTrue(res["ok"])
        self.assertEqual(res["via"], "native")
        self.assertIn("graph.facebook.com", self.requests[0][0])

    def test_linkedin_posts_with_token(self):
        self._ok()
        res = publish.post_to("LinkedIn", self._kit("LinkedIn"),
                              self._keys({("linkedin", "token"): "LITK"}))
        self.assertTrue(res["ok"])
        self.assertEqual(res["via"], "native")
        self.assertIn("api.linkedin.com/v2/ugcPosts", self.requests[0][0])

    def test_unknown_platform_skipped(self):
        res = publish.post_to("Threads", self._kit("Threads"), self._keys())
        self.assertEqual(res["via"], "skipped")
        self.assertFalse(res["ok"])

    def test_publish_batch_never_raises_mixed(self):
        self._ok()
        kits = [self._kit("Twitter / X"), self._kit("Pinterest")]
        out = publish.publish_batch(kits, self._keys({("pinterest", "token"): "P"}))
        # twitter no keys -> skipped, pinterest -> native
        self.assertEqual(len(out), 2)
        by = {r["platform"]: r for r in out}
        self.assertEqual(by["Twitter / X"]["via"], "skipped")
        self.assertEqual(by["Pinterest"]["via"], "native")

    def test_http_error_reports_not_ok_not_raises(self):
        def fake(url, payload_, headers, timeout=15):
            return 429, {"errors": []}
        publish._post = fake
        res = publish.post_to("LinkedIn", self._kit("LinkedIn"),
                              self._keys({("linkedin", "token"): "L"}))
        self.assertFalse(res["ok"])
        self.assertEqual(res["via"], "native")


class TestServerNativeWiring(unittest.TestCase):
    def setUp(self):
        import server as srv
        self.srv = srv
        self._orig_twitter = self.srv._get_setting("social.key.twitter")

    def tearDown(self):
        self.srv._set_setting("social.key.twitter", self._orig_twitter if self._orig_twitter else "")

    def test_publish_native_uses_pasted_keys(self):
        import server
        server._set_setting("social.key.twitter", "CK|CS|AT|ATS")
        hits = []
        def fake(url, payload_, headers, timeout=15):
            hits.append((url, payload_, headers))
            return 201, {"data": {"id": "12345"}}
        publish._post = fake
        kit = {"platform": "Twitter / X", "slug": "keto", "body": "Best keto ranked",
               "link": "https://x/lp/keto?utm_content=ab1", "name": "p"}
        res = server._publish_native([kit])
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0]["ok"])
        self.assertEqual(res[0]["via"], "native")
        self.assertEqual(hits[0][0], "https://api.twitter.com/2/tweets")
        self.assertTrue(publish._post is fake)
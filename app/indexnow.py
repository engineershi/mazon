# -*- coding: utf-8 -*-
"""IndexNow integration: free, instant indexing pushes to search engines
(Bing, Yandex, Naver, Seznam) whenever the site's URLs change.

How it works
  * A site-unique 32-hex *key* lives at /<key>.txt (served by server.py so the
    engines can verify ownership without any dashboard/verification step).
  * submit_urls() POSTs the site's URL list to https://api.indexnow.org/indexnow.
  * HTTP 200 (or 202) means accepted -> participating engines crawl in minutes.
  * Limit is 10,000 URLs/day; a free plan this site size never gets close.

Everything bottoms out in the module-level _post() so offline tests can inject
fake responses (same pattern as amazon._urlopen).
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import seo

# Site-unique 32-hex key. Override via INDEXNOW_KEY env if you change it later.
_DEFAULT_KEY = os.environ.get("INDEXNOW_KEY") or "0aa657c0ce459baba7a21e6d40e35351"

ENDPOINT = "https://api.indexnow.org/indexnow"


def key():
    """The active IndexNow key (lowercase 32-hex), or "" when unset."""
    return (_DEFAULT_KEY or "").strip().lower()


def key_file_path(base_url=None):
    """Absolute URL of the key file search engines check: /<key>.txt."""
    base = (base_url or seo.BASE_URL).rstrip("/")
    return "%s/%s.txt" % (base, key())


def serve_key(path):
    """Return the key-body to serve at /<key>.txt, or None if path mismatch."""
    k = key()
    if k and path == "/" + k + ".txt":
        return k
    return None


def _post(url, payload, timeout=20):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return None


def submit_urls(urls, base_url=None, key_id=None, timeout=20):
    """Submit absolute site URLs to IndexNow. Returns (ok, message).

    urls: list of absolute URLs (should all start with base_url).
    Only URLs under the given base (site) are included in the submission.
    """
    k = (key_id or key()).lower()
    base = (base_url or seo.BASE_URL).rstrip("/")
    if not k or len(k) != 32:
        return False, "invalid key (must be 32 hex chars)"
    host = urllib.parse.urlsplit(base).hostname
    if not host:
        return False, "invalid base url: %r" % base
    urls = [u for u in (urls or []) if str(u).startswith(base)]
    urls = sorted(set(urls))
    if not urls:
        return False, "no urls to submit"
    payload = {
        "host": host,
        "key": k,
        "keyLocation": key_file_path(base),
        "urlList": urls[:1000],
    }
    status = _post(ENDPOINT, payload, timeout=timeout)
    if status in (200, 202):
        return True, "accepted (%d urls, engine pings queued)" % len(payload["urlList"])
    if status is None:
        return False, "network error"
    return False, "engine rejected (http %s)" % status
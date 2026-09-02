# -*- coding: utf-8 -*-
"""Minimal stdlib PA-API (Product Advertising API) client.

PA-API is AWS's official Amazon Associates product-data API. It gives richer,
quoted fields than the public scraper (exact price, offer count, stock state)
and is the sanctioned, TOS-clean way to read product data.

To stay stdlib-only and testable we hand-roll AWS Signature V4 over ``urllib``
and route the request through ``amazon._urlopen`` so tests can stub it.

Everything is gated: if no access key / secret / partner tag are configured the
module reads nothing, posts nothing and ``lookup()`` returns ``None`` — callers
silently fall back to the existing keyless scraper. Configure via env
(PAAPI_ACCESS_KEY, PAAPI_SECRET_KEY, PAAPI_PARTNER_TAG, PAAPI_HOST) or at
runtime via :func:`configure`. Env always wins.
"""
import datetime
import hashlib
import hmac
import json
import os
import threading
import urllib.request

import amazon

PAAPI_HOST = os.environ.get("PAAPI_HOST", "webservices.amazon.com").rstrip("/")
_ENDPOINT_PATH = "/paapi5/getitems"

_PAAPI_CFG = {"access_key": "", "secret_key": "", "partner_tag": ""}
_cfg_lock = threading.Lock()


def _cfg():
    with _cfg_lock:
        stored = dict(_PAAPI_CFG)
    return {
        "access_key": os.environ.get("PAAPI_ACCESS_KEY") or stored["access_key"],
        "secret_key": os.environ.get("PAAPI_SECRET_KEY") or stored["secret_key"],
        "partner_tag": os.environ.get("PAAPI_PARTNER_TAG") or stored["partner_tag"],
    }


def configure(access_key="", secret_key="", partner_tag=""):
    """Set PA-API credentials at runtime (admin UI seam). Returns the store so
    callers can persist it. Empty strings clear the values."""
    with _cfg_lock:
        _PAAPI_CFG["access_key"] = (access_key or "").strip()
        _PAAPI_CFG["secret_key"] = (secret_key or "").strip()
        _PAAPI_CFG["partner_tag"] = (partner_tag or "").strip()
    return dict(_PAAPI_CFG)


def _configure_clear():
    """Test helper: reset all stored creds (env overrides still apply)."""
    configure("", "", "")


def status():
    """Masked key presence + summarized readiness (env overrides stored)."""
    c = _cfg()
    return {
        "has_access_key": bool(c["access_key"]),
        "has_secret_key": bool(c["secret_key"]),
        "has_partner_tag": bool(c["partner_tag"]),
        "ready": bool(c["access_key"] and c["secret_key"] and c["partner_tag"]),
        "host": PAAPI_HOST,
    }


def ready():
    return status()["ready"]


def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sigv4(host, access_key, secret_key, body):
    """Return (canonical_uri, signature) for the GetItems POST using SigV4."""
    now = datetime.datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    region = "us-east-1"
    service = "ProductAdvertisingAPI"
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_uri = "/paapi5/getitems"
    canonical_headers = "content-encoding:amz-1.0\nhost:%s\nx-amz-date:%s\n" % (
        host, amz_date)
    signed_headers = "content-encoding;host;x-amz-date"
    canonical_request = "\n".join([
        "POST", canonical_uri, "", canonical_headers, signed_headers,
        payload_hash,
    ])
    scope = "/".join([date_stamp, region, service, "aws4_request"])
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    k_date = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    return canonical_uri, amz_date, signature, signed_headers


def get_items(asin_list):
    """Call PA-API GetItems for a list of ASINs. Returns the raw JSON dict, or
    ``None`` if the API is not configured or the call fails. Never raises."""
    c = _cfg()
    if not (c["access_key"] and c["secret_key"] and c["partner_tag"]):
        return None
    asins = [str(a).strip().upper() for a in asin_list if a]
    if not asins:
        return None
    body = json.dumps({
        "PartnerType": "Associates",
        "PartnerTag": c["partner_tag"],
        "Marketplace": "www.amazon.com",
        "ItemIds": asins[:10],
        "Resources": [
            "Images.Primary.Large",
            "ItemInfo.Title",
            "OfferSummary.LowestPrice",
            "Offers.Listings.Price",
            "ParentASIN",
            "ItemInfo.ExternalIds",
        ],
    }).encode("utf-8")
    try:
        canonical_uri, amz_date, signature, signed_headers = _sigv4(
            PAAPI_HOST, c["access_key"], c["secret_key"], body)
        url = "https://%s%s" % (PAAPI_HOST, canonical_uri)
        req = urllib.request.Request(
            url, data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Content-Encoding": "amz-1.0",
                "X-Amz-Date": amz_date,
                "X-Amz-Target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems",
                "Authorization": (
                    "AWS4-HMAC-SHA256 Credential=%s/%s/a/us-east-1/"
                    "ProductAdvertisingAPI/aws4_request, "
                    "SignedHeaders=%s, Signature=%s"
                ) % (c["access_key"], amz_date[:8], signed_headers, signature),
            },
            method="POST")
        raw = amazon._urlopen(req, timeout=10)
        if raw is None:
            return None
        data = raw.read()
        return json.loads(data.decode("utf-8", "replace"))
    except Exception:
        return None


def lookup(asin):
    """Fetch one ASIN and normalize it to the same dict shape the scraper uses
    so callers can use either source transparently. Returns ``None`` on any
    missing/disabled/failed path."""
    data = get_items([asin])
    if not data:
        return None
    item = None
    for it in (data.get("ItemsResult", {}) or {}).get("Items", []) or []:
        if (it or {}).get("ASIN") == asin:
            item = it
            break
    if not item:
        return None
    info = item.get("ItemInfo", {}) or {}
    title = ((info.get("Title", {}) or {}).get("DisplayValue")) or ""
    price_obj = ((item.get("Offers", {}) or {}).get("Listings") or [])
    price = ""
    currency = ""
    if price_obj:
        p = price_obj[0].get("Price", {}) or {}
        price = p.get("Amount") or ""
        currency = (p.get("Currency") or "")[:3]
    return {
        "asin": asin,
        "title": title,
        "price": price,
        "stars": None,
        "reviews": None,
        "url": amazon.affiliate_url(asin),
        "currency": currency,
        "source": "paapi",
    }
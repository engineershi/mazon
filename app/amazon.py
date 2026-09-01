# -*- coding: utf-8 -*-
"""pstore: keyless multi-marketplace Amazon product data + affiliate link builder.

Everything here is designed to work with NO Amazon API key, so a beginner can
ship and earn immediately. Two live sources are tried in order for product
search:

  1. A third-party scraper provider (ScraperAPI / Outscraper / SerpAPI) when a
     key is configured (proxies Amazon so datacenter IPs aren't bot-blocked).
  2. A direct, best-effort parse of the public Amazon search-results page.

Plus Amazon autosuggest (completion.amazon.com) as a real, keyless demand
signal for niche mining.

Affiliate links are built natively with ?tag=... so you earn commissions with
no API at all (amazon.com/dp/<ASIN>?tag=YOURTAG-20).

Every network call bottoms out in the single module-level ``_urlopen`` so
tests can inject fake responses, and providers never raise on the happy path.
"""
import copy
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# ------------------------------------------------------------------ marketplace
# One marketplace powers every live lookup (search host, autosuggest mid+locale,
# tag suffix). Add new locales here; the rest of the app adapts automatically.
_MARKETPLACES = {
    "com":  {"name": "Amazon US", "host": "www.amazon.com",  "mid": "ATVPDKIKX0DER",
             "lang": "en_US", "tag_suffix": "20"},
    "co.uk":{"name": "Amazon UK", "host": "www.amazon.co.uk","mid": "A1F83G8C2ARO7P",
             "lang": "en_GB", "tag_suffix": "21"},
    "de":   {"name": "Amazon DE", "host": "www.amazon.de",   "mid": "A1PA6795UKMFR9",
             "lang": "de_DE", "tag_suffix": "21"},
    "ca":   {"name": "Amazon CA", "host": "www.amazon.ca",   "mid": "A2EUQ1WTGCTBG2",
             "lang": "en_CA", "tag_suffix": "20"},
    "co.jp":{"name": "Amazon JP", "host": "www.amazon.co.jp","mid": "A1VC38T7YXB528",
             "lang": "ja_JP", "tag_suffix": "22"},
    "com.au":{"name":"Amazon AU", "host": "www.amazon.com.au","mid":"A39IBJ37TRP1C6",
             "lang": "en_AU", "tag_suffix": "22"},
    "in":   {"name": "Amazon IN", "host": "www.amazon.in",   "mid": "A21TJRUUN4KGV",
             "lang": "en_IN", "tag_suffix": "21"},
}

DEFAULT_MARKET = "com"

# ------------------------------------------------------------------ settings
MARKET = DEFAULT_MARKET
NET_TIMEOUT = 10
MIN_INTERVAL = 0.0          # politeness throttle; server raises it at startup
MAX_ATTEMPTS = 3
TRANSIENT = (429, 500, 502, 503, 504)

_SCRAPER_PROVIDERS = {
    "scraperapi": {
        "name": "ScraperAPI", "env_var": "SCRAPERAPI_API_KEY", "kind": "html",
        "key_url": "https://www.scraperapi.com/dashboard",
    },
    "outscraper": {
        "name": "Outscraper", "env_var": "OUTSCRAPER_API_KEY", "kind": "json",
        "key_url": "https://app.outscraper.com/",
    },
    "serpapi": {
        "name": "SerpAPI", "env_var": "SERPAPI_API_KEY", "kind": "json",
        "key_url": "https://serpapi.com/manage-api-key",
    },
}

_SCRAPER_PREFERRED = "auto"

# ------------------------------------------------------------------ infra
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_last_hit = {}
_throttle_lock = threading.Lock()

CACHE_TTL = 3600
_fetch_cache = {}
_cache_lock = threading.Lock()
_MISS = object()


def clear_cache():
    with _cache_lock:
        _fetch_cache.clear()


def _throttle(host):
    now = time.monotonic()
    with _throttle_lock:
        if MIN_INTERVAL <= 0:
            _last_hit[host] = now
            return 0.0
        last = _last_hit.get(host, 0.0)
        wait = max(0.0, last + MIN_INTERVAL - now)
        _last_hit[host] = now + wait
        return wait


def _cache_hit(cache, key):
    if CACHE_TTL <= 0:
        return _MISS
    with _cache_lock:
        hit = cache.get(key)
        if not hit:
            return _MISS
        ts, value = hit
        if time.monotonic() - ts > CACHE_TTL:
            cache.pop(key, None)
            return _MISS
        return copy.deepcopy(value)


def _cache_store(cache, key, value):
    if CACHE_TTL <= 0:
        return
    with _cache_lock:
        cache[key] = (time.monotonic(), copy.deepcopy(value))


def marketplace_info():
    """Fixed metadata for the active marketplace; unknown falls back to US."""
    return copy.deepcopy(_MARKETPLACES.get(MARKET) or _MARKETPLACES[DEFAULT_MARKET])


def set_market(market):
    """Set the active marketplace id; no-op for unknown ids."""
    global MARKET
    if market in _MARKETPLACES:
        MARKET = market


def set_tag(tag):
    """Set the affiliate tag that gets appended to every product link."""
    global AFFILIATE_TAG
    AFFILIATE_TAG = (tag or "").strip()


AFFILIATE_TAG = ""


def _urlopen(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


def _fetch(url, timeout=None):
    """Fetch a URL with retry-once-on-transient, returning bytes."""
    t = timeout or NET_TIMEOUT
    host = urllib.parse.urlsplit(url).netloc
    wait = _throttle(host)
    if wait:
        time.sleep(wait)
    last_err = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": _UA,
                "Accept-Language": marketplace_info()["lang"].replace("_", "-"),
            })
            return _urlopen(req, t).read()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in TRANSIENT and attempt < MAX_ATTEMPTS - 1:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise
        except OSError:
            raise
    raise last_err


# ------------------------------------------------------------------ autosuggest
def autosuggest(query, limit=8):
    """Real Amazon keyword ideas via the public completion endpoint (keyless).
    Returns a list of str, or [] on any failure. A great demand proxy / seed
    expander for niche mining."""
    cache_key = ("autosuggest", MARKET, query, limit)
    hit = _cache_hit(_fetch_cache, cache_key)
    if hit is not _MISS:
        return hit
    info = marketplace_info()
    params = {
        "mid": info["mid"], "alias": "aps", "prefix": query,
        "event": "onKeyPress", "limit": limit, "site-variant": "desktop",
        "suggestion-type": "KEYWORD", "b2b": "1", "fresh": "1",
        "ks": "68", "prefix-site": info["host"],
    }
    url = "https://completion.amazon.com/api/2017/suggestions?" + urllib.parse.urlencode(params)
    ideas = []
    try:
        raw = _fetch(url).decode("utf-8", "replace")
        data = json.loads(raw)
        for s in data.get("suggestions", []):
            text = str(s.get("value") or "").strip()
            if text:
                ideas.append(text)
    except Exception:
        ideas = []
    _cache_store(_fetch_cache, cache_key, ideas)
    return ideas


# ------------------------------------------------------------------ search
def _search_url():
    return "https://%s/s" % marketplace_info()["host"]


def search(query, top=8, category=""):
    """Search Amazon for products. Tries scraper providers first (when any are
    keyed), else parses the public page. Returns (items, source) where source
    is "scraper-<pid>", "amazon", or None. Never raises."""
    items, pid = _scraper_search(query, top)
    if items:
        return items, ("scraper-" + pid)
    try:
        page = _fetch(_search_url() + "?k=%s&i=%s" % (
            urllib.parse.quote_plus(query), urllib.parse.quote_plus(category))).decode("utf-8", "replace")
        items, _total = _parse_search_page(page, top=top)
        return items, ("amazon" if items else None)
    except Exception:
        return [], None


def _parse_search_page(html, top=8):
    """Parse Amazon search-result HTML from supported layouts into item dicts.
    Returns (items, approx_total). Best-effort and layout-tolerant."""
    items = []
    total = 0
    m = re.search(r'([\d,]+)\s+results(?: for)?', html)
    if m:
        try:
            total = int(m.group(1).replace(",", ""))
        except ValueError:
            total = 0
    seen = set()
    # Match result cards by their link/data attributes.
    urls = re.findall(r'href="(/[^"]*dp/[A-Z0-9]{10}[^"]*)"', html)
    if not urls:
        urls = re.findall(r'href="(/[^"]*?/dp/[A-Z0-9]{10})[^"]*"', html)
    for u in urls:
        asin = re.search(r'/dp/([A-Z0-9]{10})', u)
        if not asin:
            continue
        code = asin.group(1)
        if code in seen:
            continue
        seen.add(code)
        items.append(_build_item(html, code))
        if len(items) >= top:
            break
    return items, total


def _build_item(html, asin):
    """Build one normalized product dict from search-page HTML around an ASIN,
    extracting title/price/rating/reviews/url with best-effort regexes."""
    url = affiliate_url(asin)
    window = _card_window(html, asin)
    title = _extract_title(window, asin)
    price, currency = _extract_price(window)
    stars = _extract_stars(window)
    reviews = _extract_reviews(window)
    return {"asin": asin, "title": title, "price": price, "stars": stars,
            "reviews": reviews, "url": url, "currency": currency}


def _card_window(html, asin):
    """Locate the result-card HTML that contains this ASIN. Modern Amazon uses
    `<div role="listitem" data-asin="...">` cards; fall back to a bounded window
    around the `/dp/<ASIN>` link for classic/simple layouts."""
    i = html.find('role="listitem" data-asin="' + asin + '"')
    if i != -1:
        j = html.find('role="listitem"', i + 1)
        return html[i:j if j != -1 else i + 30000]
    i = html.find('data-asin="' + asin + '"')
    if i != -1:
        j = html.find('data-asin="', i + 1)
        return html[i:j if j != -1 else i + 30000]
    i = html.find("/dp/" + asin)
    if i != -1:
        j = html.find("/dp/", i + 5)
        return html[max(0, i - 1200):j if j != -1 else i + 9000]
    return html


def _clean_text(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


_STALE_TITLES = {"overall pick", "amazon's choice", "sponsored", "best seller"}


def _extract_title(window, asin):
    # 1. Product image alt tag is the cleanest source in the current layout.
    for m in re.finditer(r'<img[^>]+\salt=["\']([^"\']{8,500})["\']', window):
        alt = _clean_text(m.group(1))
        low = alt.lower()
        if (" star" in low or "stars" in low or "thumbnail" in low or "image" in low
                or alt in _STALE_TITLES):
            continue
        if len(alt) >= 8:
            return alt[:200]
    for pat in (
        r'data-cy="title-rendering"[^>]*>\s*<[^>]+>([^<]{5,300})<',
        r'class="[^"]*a-text-normal[^"]*"[^>]*>\s*<[^>]+>([^<]{5,300})<',
        r'class="[^"]*a-text-normal[^"]*"[^>]*>([^<]{5,300})<',
        r'<h2[^>]*>(.*?)</h2>',
    ):
        m = re.search(pat, window, re.S)
        if not m:
            continue
        t = _clean_text(re.sub(r"<[^>]+>", " ", m.group(1)))
        if len(t) >= 5 and t.lower() not in _STALE_TITLES:
            return t[:200]
    # 3. Layout-agnostic fallback: first readable `<span>` text, tag-stripped.
    for m in re.finditer(r"<span[^>]*>([^<]{5,300})<", window):
        t = _clean_text(m.group(1))
        if len(t) >= 5 and t.lower() not in _STALE_TITLES:
            return t[:200]
    return asin


def _extract_price(window):
    """Price + currency from the card. Prefers the primary `a-price` block
    (skips `/count` unit prices and `List:` strikethroughs). Returns
    (float or None, currency-id or None)."""
    m = re.search(r'<span[^>]*class="a-price"[^>]*>\s*<span\s+class="a-offscreen">([^<]{1,60})<', window)
    if m:
        value, cur = _price_value(m.group(1))
        if value is not None:
            return value, cur
    for m in re.finditer(r'class="a-offscreen">([^<]{1,60})<', window):
        value, cur = _price_value(m.group(1))
        if value is not None:
            return value, cur
    return None, None


def _price_value(raw):
    """Parse a single a-offscreen price string -> (float or None, currency)."""
    text = raw.replace("\xa0", " ").strip()
    if not text or text.lower().startswith("list:"):
        return None, None
    cur, num = _split_currency(text)
    try:
        return round(float(num.replace(",", "")), 2), cur
    except ValueError:
        return None, None


_CURRENCY_TOKENS = (
    ("EUR", "\u20ac"), ("USD", "$"), ("GBP", "\u00a3"), ("CAD", "C$"),
    ("AUD", "A$"), ("JPY", "\u00a5"), ("INR", "\u20b9"), ("CNY", "\u00a5"),
    ("BRL", "R$"), ("MXN", "MX$"), ("NZD", "NZ$"), ("SGD", "S$"),
)


def _split_currency(raw):
    """Return (currency-id or None, numeric-part) from a price string like
    'EUR 8.58', '$12.99', '£5.00' or '8.58'."""
    for cur, _sym in _CURRENCY_TOKENS:
        if raw.upper().startswith(cur):
            return cur, raw[len(cur):].strip()
    if raw[0] in "\u00a3\u20ac\u00a5":
        return {"\u00a3": "GBP", "\u20ac": "EUR", "\u00a5": "JPY"}.get(raw[0]), raw[1:].strip()
    if raw[:2].upper() in ("US",):
        return "USD", raw[2:].strip()
    if raw.startswith("$"):
        return "USD", raw[1:].strip()
    return None, raw.strip()


def currency_symbol(code):
    """Human symbol for a currency id ('USD' -> '$'); defaults to '$'."""
    return dict(_CURRENCY_TOKENS).get(code or "USD", "$")


def _extract_stars(window):
    if not window:
        return None
    m = re.search(r'aria-label="([\d.]+) out of 5 stars(?:[,"][^"]*)?"', window) or \
        re.search(r'alt="([\d.]+) out of 5 stars"', window)
    if not m:
        m = re.search(r'([\d.]+) out of 5 stars', window)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _extract_reviews(window):
    for pat in (
        r'aria-label="([\d,]+) ratings?[",]',
        r's-underline-text[^>]*>\(([\d,]+)\)<',
        r's-underline-text[^>]*>([\d,]+)[^<]*<',
        r'out of 5 stars[\s\S]{0,400}?\(([\d,]+)\)<',
    ):
        m = re.search(pat, window)
        if m:
            try:
                return _parse_count(m.group(1))
            except (TypeError, ValueError):
                continue
    return None


def _parse_count(raw):
    raw = raw.replace(",", "").strip()
    mult = 1
    if raw and raw[-1] in ("K", "M"):
        mult = 1000 if raw[-1] == "K" else 1000000
        raw = raw[:-1]
    return int(float(raw) * mult)


# ------------------------------------------------------------------ affiliate
# The money-maker: no API needed. Build a tagged product URL from an ASIN.
_TAG_URL = "/dp/%s?tag=%s"
_pass_through_re = re.compile(r'[;/?&:]')


def affiliate_url(asin):
    if not AFFILIATE_TAG:
        return "https://%s/dp/%s" % (marketplace_info()["host"], asin)
    tag = _pass_through_re.sub("-", AFFILIATE_TAG)
    return "https://%s/dp/%s?tag=%s" % (marketplace_info()["host"], asin, tag)


# ------------------------------------------------------------------ scraper providers
def scraper_status():
    """Per-provider key presence (masked). Env wins over stored config."""
    providers = {}
    for pid, meta in _SCRAPER_PROVIDERS.items():
        providers[pid] = {
            "name": meta["name"], "kind": meta["kind"],
            "has_key": bool(_scraper_key(pid)),
        }
    return {"providers": providers, "preferred": _SCRAPER_PREFERRED}


def _scraper_key(pid):
    meta = _SCRAPER_PROVIDERS.get(pid)
    if not meta:
        return ""
    stored = _stored_scraper_keys().get(pid, "")
    return os.environ.get(meta["env_var"]) or stored


_STORED_SCRAPER = {}
_stored_lock = threading.Lock()


def _stored_scraper_keys():
    with _stored_lock:
        return dict(_STORED_SCRAPER)


def set_scraper_key(pid, key):
    with _stored_lock:
        _STORED_SCRAPER[pid] = (key or "").strip()


def test_scraper_key(pid, key=""):
    """Verify an API key against the provider's live account endpoint.

    Uses the module's ``_urlopen`` seam (and ``_fetch``), so tests can stub it
    while production makes a real, no-quota request per provider:
      * scraperapi -> GET /account  (api_key query param)
      * serpapi    -> GET /account  (api_key query param)
      * outscraper -> GET /account  (X-API-KEY header)
    A 200 response means the key was accepted; 401/403 means it was rejected.
    Returns {"ok", "pid", "provider", "status", "detail", "latency_ms"}.
    """
    meta = _SCRAPER_PROVIDERS.get(pid)
    if not meta:
        return {"ok": False, "pid": pid, "provider": "",
                "status": 0, "error": "unknown provider: %s" % pid}
    name = meta["name"]
    key = (key or "").strip() or _scraper_key(pid)
    if not key:
        return {"ok": False, "pid": pid, "provider": name,
                "status": 0, "error": "no key set"}
    t0 = time.monotonic()
    try:
        if pid == "outscraper":
            req = urllib.request.Request(
                "https://api.app.outscraper.com/account",
                headers={"X-API-KEY": key})
            raw = _urlopen(req, NET_TIMEOUT).read()
        elif pid == "scraperapi":
            raw = _fetch("https://api.scraperapi.com/account?api_key=%s"
                         % urllib.parse.quote_plus(key))
        else:  # serpapi
            raw = _fetch("https://serpapi.com/account?api_key=%s"
                         % urllib.parse.quote_plus(key))
        detail = raw.decode("utf-8", "replace").strip()
        try:
            obj = json.loads(detail)
            if isinstance(obj, dict):
                detail = ", ".join("%s=%s" % (k, str(v)[:80])
                                   for k, v in sorted(obj.items())[:3])
        except Exception:
            detail = detail[:200]
        return {"ok": True, "pid": pid, "provider": name, "status": 200,
                "detail": detail,
                "latency_ms": round((time.monotonic() - t0) * 1000)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "pid": pid, "provider": name, "status": e.code,
                "error": "rejected by %s (HTTP %d)" % (name, e.code),
                "latency_ms": round((time.monotonic() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "pid": pid, "provider": name, "status": 0,
                "error": "test failed: %s" % e,
                "latency_ms": round((time.monotonic() - t0) * 1000)}


def _scraper_preferred_order():
    order = list(_SCRAPER_PROVIDERS.keys())
    pref = _SCRAPER_PREFERRED
    if pref in _SCRAPER_PROVIDERS:
        order = [pref] + [p for p in order if p != pref]
    return [pid for pid in order if _scraper_key(pid)]


def _scraper_search(query, top=8):
    for pid in _scraper_preferred_order():
        try:
            if _SCRAPER_PROVIDERS[pid]["kind"] == "html":
                items = _scraper_search_html(pid, query, top)
            else:
                items = _scraper_search_json(pid, query, top)
            if items:
                return items, pid
        except Exception:
            continue
    return [], None


def _scraper_search_html(pid, query, top):
    key = _scraper_key(pid)
    if not key:
        return []
    target = _search_url() + "?k=%s" % urllib.parse.quote_plus(query)
    url = "https://api.scraperapi.com?api_key=%s&url=%s" % (
        urllib.parse.quote_plus(key), urllib.parse.quote_plus(target))
    page = _fetch(url).decode("utf-8", "replace")
    items, _total = _parse_search_page(page, top=top)
    return items


def _scraper_search_json(pid, query, top):
    key = _scraper_key(pid)
    if not key:
        return []
    if pid == "outscraper":
        url = "https://api.app.outscraper.com/amazon/search?query=%s&async=false" % (
            urllib.parse.quote_plus(query))
        req = urllib.request.Request(url, headers={"X-API-KEY": key})
        data = json.loads(_urlopen(req, NET_TIMEOUT).read().decode("utf-8", "replace"))
        rows = data.get("data") or []
        items = rows[:top]
        return _normalize_json_items(items)
    # serpapi
    params = {
        "api_key": key, "engine": "amazon", "amazon_domain": marketplace_info()["host"],
        "search_query": query, "num": top,
    }
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    data = json.loads(_fetch(url).decode("utf-8", "replace"))
    rows = data.get("organic_results") or data.get("products") or []
    return _normalize_json_items(rows)


def _normalize_json_items(rows):
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or r.get("name") or "").strip()
        code = str(r.get("asin") or "").strip()
        url = str(r.get("link") or r.get("url") or "").strip()
        if not title and not code:
            continue
        if code:
            asin = code
        else:
            m = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', url)
            asin = m.group(1) if m else None
        if not asin:
            continue
        price = r.get("price") or r.get("price_raw")
        cur, _num = (None, None)
        try:
            if isinstance(price, (dict,)):
                price = price.get("value")
            if price is not None:
                cur, _num = _split_currency(str(price))
            price = None if price is None else round(float(_num or str(price).replace("$", "").replace(",", "")), 2)
        except (TypeError, ValueError):
            price = None
        stars = r.get("rating") or r.get("stars")
        try:
            stars = None if stars is None else float(stars)
        except (TypeError, ValueError):
            stars = None
        reviews = r.get("reviews") or r.get("ratings_total") or r.get("rating_count")
        try:
            reviews = None if reviews is None else _parse_count(str(reviews))
        except (TypeError, ValueError):
            reviews = None
        out.append({"asin": asin, "title": title, "price": price, "stars": stars,
                    "reviews": reviews, "url": affiliate_url(asin), "currency": cur})
    return out

# -*- coding: utf-8 -*-
"""pstore publish: native per-platform API posting gateway (stdlib-only).

When the operator pastes real platform keys on /admin/apikeys, scheduled / manual
posts publish natively to that platform instead of only firing the Zapier
webhook. Every platform is optional and gated: if the platform's keys are absent
the gateway posts nothing for it and returns ``skipped``, so the existing
SOCIAL_WEBHOOK / Zapier path stays the safe fallback and posting never blocks.

Credentials are read from a ``key_getter(ns, name)`` callable supplied at build
time (server wires it to its persisted settings KV:
  social.key.twitter, social.key.pinterest, social.key.facebook, social.key.linkedin
The same keys already power the /admin/apikeys UI, so pasting them enables native
posting here with zero extra config.

Networking never raises: every request bottoms out in module-level :func:`_post`,
which tests stub to stay hermetic. On any error a platform reports ``error`` and
the caller (server) decides whether to keep the Zapier webhook path.

Supported native backends today (each posts ``{body, link}``):
  * Twitter / X     — OAuth 1.0a (app + user keys) via api.twitter.com/2/tweets
  * Pinterest       — OAuth 2.0 board app token via api.pinterest.com/v5/pins (POST)
  * Facebook        — Graph API feed POST with a Page access token
  * LinkedIn        — UGC post with an organization access token
Threads falls back to the webhook (its API needs the same image-video media
endpoints used for the visual caption; kept behind the webhook for now).
"""
import datetime
import json
import os
import secrets
import threading
import urllib.parse
import urllib.request

import social

_NET_LOCK = threading.Lock()


def _post(url, payload, headers, timeout=15):
    """POST JSON to ``url``. Test seam (module-level) — callers treat any
    exception/HTTP>399 as failure. Returns (http_status, json_dict)."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=dict(headers, **{"Content-Type": "application/json"}), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _read_json(resp)
    except urllib.error.HTTPError as e:
        return e.code, _read_json(e)
    except Exception:
        return 0, {}


def _read_json(resp):
    try:
        raw = resp.read()
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {}


def _oauth_nonce():
    return secrets.token_urlsafe(16)


def _oauth_param(key, secret, token, token_secret):
    """OAuth 1.0a header params; empty secrets still sign deterministically so a
    token-only config posts as the user with a consumer key present."""
    from urllib.parse import urlparse
    u = urlparse("https://api.twitter.com/2/tweets")
    ts = str(int(datetime.datetime.now().timestamp()))
    p_enc = urllib.parse.quote("oauth_consumer_key=%s&oauth_nonce=%s"
                               "&oauth_signature_method=HMAC-SHA1&oauth_timestamp=%s"
                               "&oauth_token=%s&oauth_version=1.0"
                               % (urllib.parse.quote(key or "", safe=""),
                                  urllib.parse.quote(_oauth_nonce(), safe=""), ts,
                                  urllib.parse.quote(token or "", safe="")), safe="")
    base = "POST&%s&%s" % (urllib.parse.quote(u.scheme + "://" + u.netloc + u.path, safe=""), p_enc)
    params = {
        "oauth_consumer_key": key or "",
        "oauth_nonce": _oauth_nonce(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": ts,
        "oauth_token": token or "",
        "oauth_version": "1.0",
    }
    sig = _oauth_sign(key, secret, token_secret, ts, base)
    params["oauth_signature"] = sig
    header = ", ".join('%s="%s"' % (k, urllib.parse.quote(v, safe=""))
                       for k, v in params.items())
    return {"Authorization": "OAuth " + header}


def _oauth_sign(key, secret, token_secret, ts, base):
    import base64
    import hashlib
    import hmac as _hmac
    ckey = urllib.parse.quote(key or "", safe="")
    csec = urllib.parse.quote(secret or "", safe="")
    tsec = urllib.parse.quote(token_secret or "", safe="")
    signing = "&".join([ckey, csec, tsec])
    message = base
    dig = _hmac.new(signing.encode(), message.encode(), hashlib.sha1).digest()
    return base64.b64encode(dig).decode()


# ------------------------------------------------------------------ twitter

def _twt_cred(kv):
    return (kv("twitter", "client_id") or kv("twitter", "app_key") or kv("twitter", "access_key"),
            kv("twitter", "client_secret") or kv("twitter", "app_secret") or kv("twitter", "secret_key"),
            kv("twitter", "token") or kv("twitter", "access_token"),
            kv("twitter", "token_secret") or kv("twitter", "access_token_secret"))


def _pint_cred(kv):
    return (kv("pinterest", "token") or kv("pinterest", "access_token")
            or kv("pinterest", "api_key"),)


def _fb_cred(kv):
    return (kv("facebook", "token") or kv("facebook", "access_token")
            or kv("facebook", "page_token"),)


def _li_cred(kv):
    return (kv("linkedin", "token") or kv("linkedin", "access_token")
            or kv("linkedin", "user_token"),)


# ------------------------------------------------------------------ post app

def _body_for(platform, kit):
    """Body text + link + any platform-specific extras the composer produced."""
    body = (kit.get("body") or "")
    link = kit.get("link") or ""
    return {"body": body, "link": link, "platform": platform}


def post_to(platform, kit, key_getter):
    """Post one kit to one platform natively. Returns a dict:
       {"ok": bool, "platform": platform, "via": "native"|"skipped",
        "message": str, "ok" if posted, "skipped" when no creds."""
    profile = {
        "Twitter / X": _post_twitter, "Pinterest": _post_pinterest,
        "Facebook": _post_facebook, "LinkedIn": _post_linkedin,
    }.get(platform)
    if profile is None:
        return {"ok": False, "platform": platform, "via": "skipped",
                "message": "No native backend for %s — webhook applies." % platform}
    b = _body_for(platform, kit)
    try:
        return profile(b, key_getter)
    except Exception as e:
        return {"ok": False, "platform": platform, "via": "native",
                "message": str(e)}


def _post_twitter(b, kv):
    key, secret, token, token_secret = _twt_cred(kv)
    if not (key and token):
        return {"ok": False, "platform": "Twitter / X", "via": "skipped",
                "message": "No X consumer + access keys configured."}
    text = (b["body"] or "")
    if b["link"] and b["link"] not in text:
        text = text + "\n" + b["link"]
    headers = _oauth_param(key, secret, token, token_secret)
    st, data = _post("https://api.twitter.com/2/tweets", {"text": text}, headers)
    cid = None
    try:
        cid = (data or {})["data"]["id"]
    except Exception:
        cid = None
    return {"ok": 200 <= st < 300, "platform": "Twitter / X", "via": "native",
            "message": ("posted id=" + str(cid)) if cid else json.dumps(data or st)}


def _post_pinterest(b, kv):
    tok = _pint_cred(kv)[0] or ""
    if not tok:
        return {"ok": False, "platform": "Pinterest", "via": "skipped",
                "message": "No Pinterest board token configured."}
    payload = {
        "title": (b["body"] or "").split("\n", 1)[0][:100],
        "description": b["body"] or "",
        "link": b["link"] or "",
        "media_source": {"source_type": "image_url", "url": og_image(b["link"])},
    }
    st, data = _post("https://api.pinterest.com/v5/pins",
                     payload, {"Authorization": "Bearer " + tok, **pint_ignore()})
    return {"ok": 200 <= st < 300, "platform": "Pinterest", "via": "native",
            "message": ("created " + str((data or {}).get("id") or "")) if data else str(st)}


def _post_facebook(b, kv):
    tok = _fb_cred(kv)[0] or ""
    if not tok:
        return {"ok": False, "platform": "Facebook", "via": "skipped",
                "message": "No Facebook page token configured."}
    text = (b["body"] or "") + ("\n" + b["link"] if b["link"] else "")
    url = ("https://graph.facebook.com/v19.0/me/feed?access_token=%s"
           % urllib.parse.quote(tok, safe=""))
    payload = {"message": text}
    st, data = _post(url, payload, {"User-Agent": "pstore/1.0"})
    return {"ok": 200 <= st < 300, "platform": "Facebook", "via": "native",
            "message": (str(data.get("id") or "") if data else str(st))}


def _post_linkedin(b, kv):
    tok = _li_cred(kv)[0] or ""
    if not tok:
        return {"ok": False, "platform": "LinkedIn", "via": "skipped",
                "message": "No LinkedIn access token configured."}
    subj = (b["body"] or "")[:200]
    text = (b["body"] or "")
    com = (b["body"] or "").replace(subj, "").strip() or subj
    payload = {
        "author": "urn:li:person:" + (kv("linkedin", "urn") or "me"),
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": (com + ("\n" + b["link"] if b["link"] else ""))},
                "shareMediaCategory": "NONE",
            }},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    st, data = _post("https://api.linkedin.com/v2/ugcPosts",
                     payload, {"Authorization": "Bearer " + tok})
    return {"ok": 200 <= st < 300, "platform": "LinkedIn", "via": "native",
            "message": ("created " + str((data or {}).get("id") or "")) if data else str(st)}


def og_image(url):
    """Best-effort og:image for the landing URL, so image-first backends
    (Pinterest) can pin something real. Returns '' on any failure."""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "pstore/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(262144).decode("utf-8", "replace")
        m = __import__("re").search(r'property="og:image"\s+content="([^"]+)"', raw)
        return (m.group(1) or "") if m else ""
    except Exception:
        return ""


def pint_ignore():
    """Extra headers so Pinterest v5 image pins don't require an upload; the API
    accepts a JSON post with an image_url media_source."""
    return {}


# ------------------------------------------------------------------ batch

def publish_batch(kits, key_getter):
    """Post a batch of kits (one per platform) natively. Returns a list of per-
    kit results. Never raises. ``kits`` are the composer kits (dicts)."""
    out = []
    for kit in kits:
        platform = kit.get("platform") or ""
        res = post_to(platform, kit, key_getter)
        res["slug"] = kit.get("slug")
        res["utm_content"] = kit.get("utm_content")
        out.append(res)
    return out
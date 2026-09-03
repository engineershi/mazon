# -*- coding: utf-8 -*-
"""pstore social: platform-ready, trackable post kits for one-click publishing.

Every kit turns the niche's top pick into short-form copy that sends readers to
the niche LANDING page with UTM parameters (utm_source=<platform>,
utm_campaign=<slug>, utm_content=<post-code>). The landing page carries the
courier beacon, so every visit and every Amazon click is attributed to the exact
post — no cloaked shortlinks, and traffic + conversions are counted per platform
and per post in /admin/social and /admin/analytics.

Publishing is kept honest and pluggable:
  * default     — /admin/social generates drafts; one click "publishes" them
                  (status flip) and returns copy-ready posts to paste anywhere.
  * webhook     — set SOCIAL_WEBHOOK and a POST of {"body","link","platform"}
                  fires for each published post, so Zapier/Make/browser tools
                  (or a future native API) can post it for real.
"""
import re
import secrets
import urllib.parse

import market_engine

PLATFORMS = ["Twitter / X", "Facebook", "LinkedIn", "Instagram", "Pinterest", "Threads"]

# 1-marketing-safe short codes (no 0/1/o/l/i) for per-post attribution.
_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"


def short_code():
    n = int.from_bytes(secrets.token_bytes(3), "big")
    out = ""
    while n:
        n, r = divmod(n, len(_ALPHABET))
        out = _ALPHABET[r] + out
    return out or "a"


def _key(name):
    return {"Twitter / X": "twitter", "Threads": "threads"}.get(name) or \
        (name or "social").lower().replace(" ", "-")


def track_link(base_url, slug, platform, content):
    """Public, tracked link back to the niche landing page (UTM-tagged)."""
    base = (base_url or "").rstrip("/")
    q = urllib.parse.urlencode({
        "utm_source": _key(platform), "utm_medium": "social",
        "utm_campaign": slug, "utm_content": content})
    return "%s/lp/%s?%s" % (base, slug, q)


def og_image_url(base_url, slug):
    """Absolute URL to the auto-generated 1200x630 share card for a niche
    (og:image / Pinterest / Instagram-adjacent). Lives at /og/<slug>."""
    base = (base_url or "").rstrip("/")
    return "%s/og/%s" % (base, slug)


def _clip(s, n=80):
    s = str(s or "")
    return s[:n - 1].rstrip() + "…" if len(s) > n else s


def _price(it, s=None):
    try:
        return market_engine._price(it) or s
    except Exception:
        return s


def _proof(it):
    stars = it.get("stars")
    reviews = it.get("reviews")
    if stars:
        rv = f" · {reviews:,} reviews" if isinstance(reviews, (int, float)) else ""
        return f"{stars:.1f}★{rv}"
    if isinstance(reviews, (int, float)):
        return f"{reviews:,} reviews"
    return "top-rated"


def _hashwords(keyword):
    words = re.findall(r"[a-z0-9]+", (keyword or "").lower())
    return "".join(w.capitalize() for w in words) or "AmazonPicks"


def hashtags(keyword):
    tag = _hashwords(keyword)
    return " ".join(["#" + tag, "#BestPicks", "#AmazonFinds", "#BuyersGuide",
                     "#Ranked", "#TopPicks", "#HonestReviews"])


# ------------------------------------------------------------------ composers

def _twitter(keyword, title, proof, price, link, slug):
    head = f"🏆 The best {keyword} is now ranked."
    body = (f"{head} {_clip(title, 60)} comes in #1 on our list — {proof}."
            f"\n\nFull ranked list + live prices: {link}")
    return {"platform": "Twitter / X", "name": "X post (top pick)",
            "body": body[:275], "link": link,
            "hashtags": hashtags(keyword)}


def _facebook(keyword, title, proof, price, link, slug):
    body = (f"Tired of guessing which {keyword} to buy? We ranked them from "
            f"live Amazon data (rating + review volume + price).\n\n"
            f"🥇 Pick: {_clip(title, 90)} — {proof}"
            f"{' · ' + price if price else ''}\n\n"
            f"See the full ranked list and why it won: {link}")
    return {"platform": "Facebook", "name": "Facebook post (hook + list)",
            "body": body, "link": link, "hashtags": hashtags(keyword)}


def _linkedin(keyword, title, proof, price, link, slug):
    body = (f"We just published our review of the best {keyword}, ranked by "
            f"real buyer signals — star rating, review volume and price — not "
            f"opinions.\n\n"
            f"Top pick: {_clip(title, 90)}\n{proof}"
            f"{' · ' + price if price else ''}\n\n"
            f"The ranked guide (updated from live listings): {link}")
    return {"platform": "LinkedIn", "name": "LinkedIn post (professional)",
            "body": body, "link": link, "hashtags": hashtags(keyword)}


def _instagram(keyword, title, proof, price, link, slug):
    body = (f"Which {keyword} actually earn their hype? 📊\n\n"
            f"We scored live Amazon listings on rating, review volume and "
            f"price. One clear winner:\n\n"
            f"🏆 {_clip(title, 70)} — {proof}"
            f"{' (' + price + ')' if price else ''}\n\n"
            f"Full ranked list in our bio link ⬅️ {link}")
    return {"platform": "Instagram", "name": "Instagram caption (visual)",
            "body": body, "link": link, "hashtags": hashtags(keyword)}


def _pinterest(keyword, title, proof, price, link, slug):
    body = (f"Best {keyword} ranked — see which one buyers keep choosing, why, "
            f"and what it costs. Pin for your next {keyword} decision.\n\n"
            f"Top pick: {_clip(title, 90)} ({proof}). Prices are live on "
            f"Amazon: {link}")
    return {"platform": "Pinterest", "name": "Pinterest pin (keyword-rich)",
            "body": body, "link": link, "hashtags": hashtags(keyword)}


def _threads(keyword, title, proof, price, link, slug):
    body = (f"Unpopular opinion: most “best {keyword}” lists are guessing.\n\n"
            f"We ranked them from live Amazon data. Top pick: {_clip(title, 55)} "
            f"— {proof}. Full list: {link}")
    return {"platform": "Threads", "name": "Threads post (hot take)",
            "body": body[:495], "link": link, "hashtags": hashtags(keyword)}


_COMPOSERS = {
    "Twitter / X": _twitter, "Facebook": _facebook, "LinkedIn": _linkedin,
    "Instagram": _instagram, "Pinterest": _pinterest, "Threads": _threads,
}


def post_kits(keyword, items, base_url, slug=None):
    """Return one ready-to-post kit (dict) per platform, UTM-tagged. Empty when
    the niche has no top pick."""
    pick = market_engine.pick_for_buyers(items)
    if not (pick or {}).get("asin"):
        return []
    slug = slug or (re.sub(r"[^a-z0-9]+", "-", (keyword or "").lower()).strip("-") or "niche")
    title = pick.get("title") or ""
    proof = _proof(pick)
    price = _price(pick)
    kits = []
    for platform in PLATFORMS:
        content = short_code()
        link = track_link(base_url, slug, platform, content)
        kit = _COMPOSERS[platform](keyword, title, proof, price, link, slug)
        kit["slug"] = slug
        kit["utm_content"] = content
        kit["proof"] = proof
        kit["target"] = "landing"
        kit["image"] = og_image_url(base_url, slug)
        kits.append(kit)
    return kits


def topic_post_kits(term, parent_keyword, items, base_url, parent_slug=None, slug=None):
    """Long-tail topic recycling: same one pick, but the copy names the specific
    long-tail angle (/n/<parent>/<term>) so every indexed topic page earns social
    traffic too. Uses its own tracked link with content tagged for the topic."""
    pick = market_engine.pick_for_buyers(items)
    if not (pick or {}).get("asin"):
        return []
    parent_slug = parent_slug or (re.sub(r"[^a-z0-9]+", "-", (parent_keyword or "").lower())
                                  .strip("-") or "niche")
    slug = slug or parent_slug
    title = pick.get("title") or ""
    proof = _proof(pick)
    price = _price(pick)
    label = _hashtag(term) or term or parent_slug
    kits = []
    for platform in PLATFORMS:
        content = short_code()
        # point to the long-tail page with the topic in the UTM campaign
        q = urllib.parse.urlencode({
            "utm_source": _key(platform), "utm_medium": "social",
            "utm_campaign": slug + "-t-" + _hashtag(term) if term else slug,
            "utm_content": content})
        link = "%s/n/%s/%s?%s" % ((base_url or "").rstrip("/"), parent_slug, slug, q) \
            if term else track_link(base_url, slug, platform, content)
        kit = _COMPOSERS[platform]("%s %s" % (parent_keyword, term), title, proof,
                                   price, link, slug)
        kit["slug"] = slug
        kit["utm_content"] = content
        kit["proof"] = proof
        kit["target"] = "topic"
        kit["term"] = term
        kit["image"] = og_image_url(base_url, parent_slug)
        kits.append(kit)
    return kits


def _hashtag(s):
    words = re.findall(r"[a-z0-9]+", (s or "").lower())
    return (words[0] if words else "") or (words or [""])[0]


# ------------------------------------------------------------------ og preview
def og_svg(slug, keyword, title, stars, reviews):
    """Small share-preview card (SVG, stdlib-only) used as og:image/twitter:image."""
    kw = html_esc(keyword) or "Niche pick"
    t = html_esc(title or "Best picks, ranked")
    pr = ("%.1f★" % stars) if stars else "Top rated"
    if stars and isinstance(reviews, (int, float)):
        pr += " · %d reviews" % reviews
    tr = t if len(t) <= 34 else t[:33] + "…"
    kwl = kw if len(kw) <= 26 else kw[:25] + "…"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#ff6b2c"/><stop offset="1" stop-color="#7c5cff"/>
</linearGradient></defs>
<rect width="1200" height="630" fill="url(#g)"/>
<text x="64" y="120" font-family="Helvetica,Arial,sans-serif" font-size="44" font-weight="700"
  fill="rgba(255,255,255,.85)">{kwl} · ranked</text>
<text x="64" y="300" font-family="Helvetica,Arial,sans-serif" font-size="72" font-weight="800"
  fill="#ffffff">{tr}</text>
<circle cx="64" cy="430" r="46" fill="#ffffff" opacity="0.18"/>
<path d="M64 446 l16 16 l30 -30" stroke="#ffffff" stroke-width="12" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
<text x="132" y="446" font-family="Helvetica,Arial,sans-serif" font-size="38" font-weight="700" fill="#ffffff">{pr}</text>
<text x="64" y="552" font-family="Helvetica,Arial,sans-serif" font-size="30" font-weight="600" fill="rgba(255,255,255,.85)">pstore → full list + live prices</text>
</svg>""".encode("utf-8")


def html_esc(s):
    import html
    return html.escape(str(s or ""), quote=True)
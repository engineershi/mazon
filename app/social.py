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


def og_image_png_url(base_url, slug):
    """Absolute URL to the raster 1200x630 share card (Pinterest/Twitter/FB
    friendly). Lives at /og/<slug>.png — the SVG card plus a real PNG."""
    base = (base_url or "").rstrip("/")
    return "%s/og/%s.png" % (base, slug)


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
        kit["keyword"] = keyword
        kit["utm_content"] = content
        kit["proof"] = proof
        kit["target"] = "landing"
        kit["image"] = og_image_url(base_url, slug)
        kit["image_png"] = og_image_png_url(base_url, slug)
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
        kit["keyword"] = parent_keyword
        kit["utm_content"] = content
        kit["proof"] = proof
        kit["target"] = "topic"
        kit["term"] = term
        kit["image"] = og_image_url(base_url, parent_slug)
        kit["image_png"] = og_image_png_url(base_url, parent_slug)
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


# ----------------------------------------------------------------------------
# Raster share card (real PNG, stdlib-only). Pinterest/Twitter/FB reject the
# SVG card above, so /og/<slug>.png re-draws the same layout as a genuine
# 1200x630 PNG using a tiny built-in 5x7 font + hand-rolled encoder. No PIL,
# no fonts, no third-party deps — a few hundred lines of pure zlib/struct.
# ----------------------------------------------------------------------------

_CANVAS = (1200, 630)
_COLOR_GRAD = ((255, 107, 44), (124, 92, 255))  # top -> bottom (matches SVG gradient)


def _png_encode(width, height, rgb_rows):
    """Encode truecolor 8-bit RGB rows into PNG bytes (stdlib zlib/struct)."""
    import struct, zlib
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = b"".join(b"\x00" + bytes(rgb) for rgb in rgb_rows)
    return (sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(scanlines, 6))
            + chunk(b"IEND", b""))


# 5x7 pixel font (7 rows of 5 columns, "#" = on). Lowercase maps to uppercase,
# non-ASCII falls back to a safe substitute or space.
_FONT = {
    "A": ("#####", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".####", "#...#", "#....", "#....", "#....", "#...#", ".####"),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".####", "#...#", "#....", "#.###", "#...#", "#...#", ".####"),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".####", "#...#", "#...#", "#...#", "#...#", "#...#", ".####"),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".####", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".####", "....#", "....#", ".####"),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".####"),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": (".####", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".####"),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".####", "#...#", "....#", "..##.", ".#...", "#....", "#####"),
    "3": (".####", "#...#", "....#", "..##.", "....#", "#...#", ".####"),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".####"),
    "6": (".####", "#....", "#....", "####.", "#...#", "#...#", ".####"),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".####", "#...#", "#...#", ".####", "#...#", "#...#", ".####"),
    "9": (".####", "#...#", "#...#", ".####", "....#", "....#", ".####"),
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
    ".": (".....", ".....", ".....", ".....", ".....", ".....", "..#.."),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    "_": (".....", ".....", ".....", ".....", ".....", ".....", "#####"),
    "#": ("#.#..", "#.#..", "#####", "#.#..", "#####", "#.#..", "#.#.."),
    "!": ("..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."),
    "?": (".###.", "#...#", "....#", "..##.", "..#..", ".....", "..#.."),
    "&": (".##..", "#..#.", "#.#..", ".##..", "#.#.#", "#..#.", ".##.#"),
    "%": ("##..#", "##.#.", "...#.", "..#..", ".#...", ".#.##", "#..##"),
    "/": ("....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."),
    ":": (".....", "..#..", ".....", "..#..", ".....", "..#..", "....."),
    "'": ("..#..", "..#..", "..#..", ".....", ".....", ".....", "....."),
    "(": ("...#.", "..#..", ".#...", "#....", "#....", ".#...", "..#.."),
    ")": (".#...", "..#..", "...#.", "....#", "....#", "...#.", "..#.."),
    "+": (".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    ",": (".....", ".....", ".....", ".....", "..#..", "..#..", ".#..."),
    ">": ("#....", ".#...", "..#..", "...#.", "..#..", ".#...", "#...."),
    "<": ("....#", "...#.", "..#..", ".#...", "..#..", "...#.", "....#"),
    "=": (".....", ".....", "#####", ".....", "#####", ".....", "....."),
    "*": (".....", "#.#.#", ".#.#.", "#####", ".#.#.", "#.#.#", "....."),
    '"': (".#.#.", ".#.#.", ".#.#.", ".....", ".....", ".....", "....."),
}

_SUBST = {"\u00b7": ".", "\u2026": "...", "\u2192": ">", "\u2605": "*",
          "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2014": "-",
          "\u2013": "-", "\u00a9": "C", "\u00ae": "R", "\u20ac": "E",
          "\ufffc": " "}  # non-breaking / object replacements stripped below


def _raster_text(s, x, y, scale):
    """Pixel positions (set of (x, y)) for the uppercase text `s` drawn at
    origin (x, y) with the built-in 5x7 font scaled `scale`x. Non-ASCII maps
    through _SUBST, then onto the font (uppercase fallback), else a space."""

    def _rows(ch):
        if ch in _SUBST:
            ch = _SUBST[ch]
        if ch in _FONT:
            return _FONT[ch]
        u = ch.upper()
        if u in _FONT:
            return _FONT[u]
        if ch == "\n":
            return None
        return _FONT[" "]

    advance = 6 * scale + 1
    pts = set()
    xx, yy = x, y
    for raw in s:
        rows = _rows(raw)
        if rows is None:
            yy += 8 * scale
            xx = x
            continue
        for r_i, row in enumerate(rows):
            for c_i, cell in enumerate(row):
                if cell != "#":
                    continue
                for dx in range(scale):
                    for dy in range(scale):
                        pts.add((xx + c_i * scale + dx, yy + r_i * scale + dy))
        xx += advance
    return pts


def og_png(slug, keyword, title, stars, reviews):
    """Raster 1200x630 share card — same story as og_svg but a real PNG so
    Pinterest/Twitter/Facebook render it. Pure stdlib (zlib + struct)."""
    W, H = _CANVAS
    t0, t1 = _COLOR_GRAD
    img = bytearray(W * H * 3)
    for y in range(H):
        t = y / (H - 1)
        r = int(t0[0] + (t1[0] - t0[0]) * t)
        g = int(t0[1] + (t1[1] - t0[1]) * t)
        b = int(t0[2] + (t1[2] - t0[2]) * t)
        img[y * W * 3:(y + 1) * W * 3] = bytes((r, g, b)) * W

    def _stamp(pts, rgb):
        for (px, py) in pts:
            if 0 <= px < W and 0 <= py < H:
                i = (py * W + px) * 3
                img[i] = rgb[0]; img[i + 1] = rgb[1]; img[i + 2] = rgb[2]

    white = (255, 255, 255)
    # keyword pill (uppercased)
    kw = (keyword or slug or "niche").replace("-", " ").upper()
    kw_t = kw if len(kw) <= 26 else kw[:25] + "..."
    _stamp(_raster_text(kw_t, 64, 88, 4), white)

    # title (one or two lines at scale 5 -> 34 chars/line)
    tr = (title or "Best picks, ranked").upper()
    line1 = tr if len(tr) <= 34 else tr[:33] + "..."
    _stamp(_raster_text(line1, 64, 258, 5), white)
    if len(tr) > 34:
        line2 = tr[33:66]
        _stamp(_raster_text(line2 + ("..." if len(tr) > 66 else ""), 64, 302, 5), white)

    # translucent bullet + white checkmark (mirrors the SVG circle + path)
    import math
    cx, cy, rad = 64, 430, 46
    for dy in range(-rad, rad + 1):
        hw = int(math.sqrt(max(0, rad * rad - dy * dy)))
        for x in range(cx - hw, cx + hw + 1):
            if 0 <= x < W and 0 <= cy + dy < H:
                i = ((cy + dy) * W + x) * 3
                img[i] = int(0.18 * 255 + 0.82 * img[i])
                img[i + 1] = int(0.18 * 255 + 0.82 * img[i + 1])
                img[i + 2] = int(0.18 * 255 + 0.82 * img[i + 2])

    def _check_line(x0, y0, x1, y1, rgb, width):
        pts = set()
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(steps + 1):
            px = int(round(x0 + (x1 - x0) * i / steps))
            py = int(round(y0 + (y1 - y0) * i / steps))
            for dx in range(-width, width + 1):
                for dy in range(-width, width + 1):
                    pts.add((px + dx, py + dy))
        _stamp(pts, rgb)

    _check_line(78, 452, 96, 470, white, 5)
    _check_line(96, 470, 126, 440, white, 5)

    # rating + proof
    if stars:
        pr = "%.1f STARS" % stars
        if isinstance(reviews, (int, float)):
            pr += " / %d REVIEWS" % reviews
    else:
        pr = "TOP RATED"
    _stamp(_raster_text(pr, 132, 406, 4), white)

    # footer
    _stamp(_raster_text("PSTORE > FULL LIST + LIVE PRICES", 64, 524, 3), white)

    rows = (bytes(img[i:i + W * 3]) for i in range(0, len(img), W * 3))
    return _png_encode(W, H, rows)
# -*- coding: utf-8 -*-
"""Mazon SEO layer: fully server-rendered, crawlable pages + search-engine
packaging (meta, OpenGraph, Twitter cards, JSON-LD, canonical, sitemap,
robots). Every niche/search page is plain static HTML so Google can index it
without JavaScript.

Injected affiliate links carry the ?tag= built by amazon.affiliate_url, so each
indexed page is a live direct-buyer link surface.
"""
import html
import json
import re
import urllib.parse

import amazon

SITE_NAME = "Mazon Finds"
SITE_DESC = "Hand-picked Amazon product picks by niche."


def _clean(s):
    return html.escape(str(s or ""), quote=True)


def _slugify(text):
    s = (text or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "niche"


def _product_json_ld(items):
    graph = []
    for it in (items or [])[:10]:
        if not it.get("title"):
            continue
        graph.append({
            "@type": "Product",
            "name": it.get("title"),
            "image": it.get("image") or "",
            "sku": it.get("asin") or "",
            "offers": {
                "@type": "Offer",
                "url": it.get("url"),
                "price": it.get("price"),
                "priceCurrency": "USD",
                "availability": "https://schema.org/InStock",
            },
            "aggregateRating": ({"@type": "AggregateRating",
                                 "ratingValue": it.get("stars"),
                                 "reviewCount": it.get("reviews")}
                                if it.get("stars") else None),
        })
    return {"@context": "https://schema.org", "@graph": graph}


def _head(title, desc, canonical, path):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_clean(title)} | {SITE_NAME}</title>
<meta name="description" content="{_clean(desc)}">
<link rel="canonical" href="{_clean('https://example.com' + path)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{_clean(title)}">
<meta property="og:description" content="{_clean(desc)}">
<meta property="og:url" content="{_clean('https://example.com' + path)}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_clean(title)}">
<meta name="twitter:description" content="{_clean(desc)}">
<link rel="stylesheet" href="/style.css">
</head>
<body>
""".encode("utf-8")


def _footer():
    return """<footer style="padding:24px;border-top:1px solid var(--border);color:var(--muted);font-size:13px">
  <p>Mazon Finds — comparison picks. Prices are indicative; check Amazon for the live price. As an Amazon Associate we earn from qualifying purchases.</p>
  <p><a href="/">Home</a> · <a href="/sitemap.xml">Sitemap</a></p>
</footer>
</body>
</html>
""".encode("utf-8")


def render_landing(saved_niches):
    """SEO landing page listing saved niches (internal links)."""
    head = _head(SITE_DESC, SITE_DESC, "/", "/")
    links = "".join(
        f'<div class="product"><h4><a href="/n/{_slugify(n["keyword"])}">{_clean(n["keyword"])}</a></h4></div>'
        for n in (saved_niches or [])[:50])
    body = f"""
<header><h1><a href="/" style="color:var(--accent);text-decoration:none">{SITE_NAME}</a></h1>
<p class="tagline">{_clean(SITE_DESC)}</p></header>
<main>
<div class="card"><h2>Explore niches</h2>
<p>Every page below is fully crawlable and carries live, affiliate-tagged product links.</p>
<div class="grid">{links}</div>
</div>
</main>
""".encode("utf-8")
    jsonld = json.dumps({
        "@context": "https://schema.org", "@type": "CollectionPage",
        "name": SITE_NAME, "description": SITE_DESC,
    }).encode("utf-8")
    return head + body + _footer() + jsonld


def render_niche(keyword, niche):
    """Crawlable niche page: title H1, meta, product cards, FAQ-ish prose."""
    items = niche.get("products") or []
    canonical = "/n/" + _slugify(keyword)
    title = f"Best {keyword} to Buy — Top Picks"
    desc = (f"Compare top {keyword} picks, prices and ratings. "
            f"Hand-curated {keyword} products with affiliate links.")
    head = _head(title, desc, canonical, canonical)
    cards = ""
    for it in items:
        cards += f"""
<div class="product">
  <h4>{_clean(it.get('title'))}</h4>
  <div class="price">{it.get('price') and '$%0.2f' % it.get('price') or '—'}</div>
  <div class="meta">{"★ " + str(it.get("stars")) if it.get("stars") else ""}{" (" + str(it.get("reviews")) + " reviews)" if it.get("reviews") else ""}</div>
  <a href="{_clean(it.get('url'))}" target="_blank" rel="nofollow sponsored noopener">Check price on Amazon</a>
</div>"""
    body = f"""
<header><h1><a href="/" style="color:var(--accent);text-decoration:none">{SITE_NAME}</a></h1></header>
<main>
<div class="card">
  <h1>{_clean(keyword)}</h1>
  <p>{_clean(desc)}</p>
  <p class="hint">Researched via Amazon {_clean(niche.get("source") or "search")} · {len(items)} product picks</p>
  <div class="grid">{cards}</div>
</div>
</main>
""".encode("utf-8")
    return head + body + _footer() + json.dumps(_product_json_ld(items)).encode("utf-8")


def render_sitemap(entries):
    """entries: list of (url_path, lastmod). Returns sitemap.xml bytes."""
    urls = "".join(
        f"<url><loc>https://example.com{_clean(p)}</loc><lastmod>{lm}</lastmod></url>\n"
        for p, lm in entries)
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n"
    return body.encode("utf-8")


def render_robots():
    return "User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml\n".encode("utf-8")

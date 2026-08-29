# -*- coding: utf-8 -*-
"""pstore SEO layer: fully server-rendered, crawlable pages + search-engine
packaging (meta, OpenGraph, Twitter cards, JSON-LD, canonical, sitemap,
robots). Every niche/search page is plain static HTML so Google can index it
without JavaScript.

Injected affiliate links carry the ?tag= built by amazon.affiliate_url, so each
indexed page is a live direct-buyer link surface.
"""
import html
import json
import os
import re
import urllib.parse

import amazon
import editorial

SITE_NAME = "pstore"
SITE_DESC = "Hand-picked Amazon product picks by niche."
BASE_URL = os.environ.get("PSTORE_URL", "https://pstore-gxbv.onrender.com").rstrip("/")


def _clean(s):
    return html.escape(str(s or ""), quote=True)


def _slugify(text):
    s = (text or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "niche"


def _product_graph(items):
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
                "priceCurrency": it.get("currency") or "USD",
                "availability": "https://schema.org/InStock",
            },
            "aggregateRating": ({"@type": "AggregateRating",
                                 "ratingValue": it.get("stars"),
                                 "reviewCount": it.get("reviews")}
                                if it.get("stars") else None),
        })
    return graph


def _head(title, desc, canonical, path, jsonld=None):
    head = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_clean(title)} | {SITE_NAME}</title>
<meta name="description" content="{_clean(desc)}">
<link rel="canonical" href="{_clean(BASE_URL + path)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{_clean(title)}">
<meta property="og:description" content="{_clean(desc)}">
<meta property="og:url" content="{_clean(BASE_URL + path)}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_clean(title)}">
<meta name="twitter:description" content="{_clean(desc)}">
<link rel="stylesheet" href="/style.css">
""".encode("utf-8")
    if jsonld:
        payload = json.dumps(jsonld).replace("</", "<\\/")
        head += ("<script type=\"application/ld+json\">%s</script>\n" % payload).encode("utf-8")
    head += b"</head>\n<body>\n"
    return head


def _footer():
    return f"""<footer style="padding:24px;border-top:1px solid var(--border);color:var(--muted);font-size:13px">
  <p>pstore — comparison picks. Prices are indicative; check Amazon for the live price.</p>
  <p>As an Amazon Associate we earn from qualifying purchases.</p>
  <p>
    <a href="/">Home</a> · <a href="/about">About</a> · <a href="/contact">Contact</a> ·
    <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · <a href="/disclosure">Disclosure</a> ·
    <a href="/sitemap.xml">Sitemap</a>
  </p>
</footer>
</body>
</html>
""".encode("utf-8")


# ------------------------------------------------------------------ info pages
STATIC_PAGES = ["about", "contact", "privacy", "terms", "disclosure"]
CONTACT_EMAIL = os.environ.get("PSTORE_CONTACT", "hello@pstore-gxbv.onrender.com")


def _page_header():
    return f"""<header><h1><a href="/" style="color:var(--accent);text-decoration:none">{SITE_NAME}</a></h1>
<p class="tagline">{_clean(SITE_DESC)}</p>
<nav><a href="/">🏠 Home</a><a href="/about">About</a><a href="/contact">Contact</a></nav></header>
"""


def render_page(slug, title, desc, content_html):
    """Generic crawlable info page (abouts/legal). Returns full HTML bytes."""
    canonical = "/" + slug
    jsonld = {"@context": "https://schema.org", "@type": "WebPage",
              "name": SITE_NAME, "description": desc, "url": BASE_URL + canonical}
    head = _head(title, desc, canonical, canonical, jsonld=jsonld)
    body = ("%s\n<main><div class=\"card\"><h1>%s</h1>%s</div></main>\n"
            % (_page_header(), _clean(title), content_html)).encode("utf-8")
    return head + body + _footer()


def render_about():
    desc = "What pstore is — independent, niche-by-niche Amazon product picks and how we make them."
    content = f"""
<p>pstore is an independent product-discovery site. We research Amazon's own catalog by niche,
then rank products by demand, rating and saturation so shoppers can compare the strongest picks in
one place instead of dredging through pages of results.</p>
<h2>How picks are made</h2>
<ul>
  <li><b>Mine the niche.</b> Each topic starts from a broad seed and is expanded using Amazon's
  autosuggest index to surface the search terms real shoppers use.</li>
  <li><b>Rank by real signals.</b> Products are scored on demand and saturation from live listings —
  price, rating and review volume — never on who pays us.</li>
  <li><b>Curate honestly.</b> If a product doesn't clear the bar, it's not listed. We correct or drop
  picks when data changes.</li>
</ul>
<h2>Editorial independence</h2>
<p>Vendors can't buy a placement here. Some links are affiliate links (see our
<a href="/disclosure">Disclosure</a>), which means we may earn a small commission if you buy after
clicking them — at no extra cost to you. Affiliate relationships never affect which products are chosen
or their ranking.</p>
<p>Questions or corrections? <a href="/contact">Contact us</a>.</p>
"""
    return render_page("about", "About pstore", desc, content)


def render_contact():
    desc = "How to reach pstore — corrections, questions and feedback."
    content = f"""
<p>We read and answer every message. Whether it's a price correction, a niche you'd love to see,
or a general question, drop us a line:</p>
<p><a class="btn" href="mailto:{CONTACT_EMAIL}">✉️ {CONTACT_EMAIL}</a></p>
<h2>What to include</h2>
<ul>
  <li>Which page or product you're writing about (a link helps).</li>
  <li>What you noticed — price, availability, rating, or a missing pick.</li>
  <li>Your name and a way to reply, if you'd like one.</li>
</ul>
<p class="hint">We reply within 1–2 business days. Please don't send unsolicited marketing or
partnership pitches — see <a href="/about">About</a> for how we decide what's listed.</p>
"""
    return render_page("contact", "Contact pstore", desc, content)


def render_privacy():
    desc = "What data pstore collects and how it's used."
    content = f"""
<h2>What we collect</h2>
<ul>
  <li><b>Server logs.</b> Standard web logs (IP, user agent, page requested) used for uptime,
  abuse prevention and analytics. Logs aren't sold or shared.</li>
  <li><b>Affiliate cookies.</b> When you click an affiliate link to Amazon, Amazon may set a short
  cookie that lets them credit the referral. We don't see or store your Amazon account data.</li>
  <li><b>Search-engine tokens.</b> We submit our pages to search engines (e.g. via IndexNow/Bing).
  No personal data is involved.</li>
</ul>
<h2>What we don't</h2>
<p>We have no accounts, no logins, no newsletters and no payment processing. We don't build profiles,
track you across other sites, or use advertising trackers beyond the affiliate relationship above.</p>
<h2>Third parties</h2>
<p>Product data and prices come from Amazon; a QR widget may load images from a third-party API.
Those services have their own privacy terms. Outbound links leave this site.</p>
<h2>Contact</h2>
<p>Privacy questions: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p>
"""
    return render_page("privacy", "Privacy Policy", desc, content)


def render_terms():
    desc = "Terms of service for using pstore."
    content = f"""
<h2>Use of this site</h2>
<p>Content is provided for personal, non-commercial browsing. You may share individual pages and
links, but not scrape, mirror or republish our picks at scale.</p>
<h2>Third-party products and links</h2>
<p>Product listings, prices, availability and offers originate from Amazon, where purchases happen.
We're an independent site and not an Amazon seller. Prices shown are indicative and change frequently —
always confirm price and availability on Amazon before buying.</p>
<h2>Affiliate relationship</h2>
<p>Some outbound links are affiliate links. As an Amazon Associate we earn from qualifying purchases,
at no extra cost to you. See our <a href="/disclosure">Disclosure</a>.</p>
<h2>No warranty / liability</h2>
<p>Picks are informational opinions, not professional advice. We work to keep data accurate but can't
warrant that every price, rating or description is current. To the fullest extent permitted by law,
pstore isn't liable for decisions made based on this content.</p>
<h2>Changes</h2>
<p>These terms may be updated; the dated version on this page governs. Continued use means you accept
any updates.</p>
<h2>Contact</h2>
<p><a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p>
"""
    return render_page("terms", "Terms of Service", desc, content)


def render_disclosure():
    desc = "pstore affiliate disclosure — how recommendations are funded."
    content = f"""
<p>pstore is reader-supported. Some of the links on this site are affiliate links — most notably
through the <b>Amazon Associates</b> program — and we may earn a small commission, at no extra cost to
you, when you click through and complete a qualifying purchase.</p>
<h2>Being upfront</h2>
<ul>
  <li>A commission never changes the price you pay.</li>
  <li>A commission never determines whether (or where) a product appears on this site.</li>
  <li>Picks are chosen for quality, demand and signal strength — not for affiliate payout.</li>
</ul>
<p>"As an Amazon Associate I earn from qualifying purchases."</p>
<p>This disclosure is required by the U.S. Federal Trade Commission and by the Amazon Associates
operating agreement, and we're glad to honor both. If you ever see a placement that feels like it
contradicts this, <a href="/contact">tell us</a>.</p>
"""
    return render_page("disclosure", "Affiliate Disclosure", desc, content)


def render_landing(saved_niches):
    """Storefront-style home: value prop, how-we-pick, niche index, FAQ."""
    jsonld = {
        "@context": "https://schema.org", "@type": "CollectionPage",
        "name": SITE_NAME, "description": SITE_DESC,
    }
    head = _head(SITE_DESC, SITE_DESC, "/", "/", jsonld=jsonld)
    links = "".join(
        f'<div class="product"><h4><a href="/n/{_slugify(n["keyword"])}">{_clean(n["keyword"])}</a></h4></div>'
        for n in (saved_niches or [])[:48])
    body = f"""
<header><h1><a href="/" style="color:var(--accent);text-decoration:none">{SITE_NAME}</a></h1>
<p class="tagline">{_clean(SITE_DESC)}</p></header>
<main>
<section class="card"><h1>Find the best <span style="color:var(--accent)">Amazon picks</span>, by niche.</h1>
<p>Each niche page ranks the strongest products on Amazon for a topic — using live price, rating
and review signals — so you can compare in minutes, not hours. Pick a niche and go.</p>
<p class="hint">Honest picks. Live data. Affiliate-tagged links (see our
<a href="/disclosure">Disclosure</a>).</p></section>

{editorial.featured_html(saved_niches)}

<section class="card"><h2>🌱 How we pick</h2>
<div class="features">
  <div class="feature"><h3>⛏️ Niche mining</h3>
  <p>We expand each topic through Amazon's own autosuggest index to find the terms real shoppers use.</p></div>
  <div class="feature"><h3>📊 Real signals</h3>
  <p>Products are ranked on demand and saturation from live listings — price, rating and review volume.</p></div>
  <div class="feature"><h3>🛒 Shop on Amazon</h3>
  <p>Every pick links straight to the product on Amazon. Purchases may earn us a commission at no cost to you.</p></div>
</div></section>

<section class="card"><h2>🛒 Explore niches</h2>
<p>Every page below is fully crawlable and carries live, affiliate-tagged product links.</p>
<div class="grid">{links}</div>
</section>

<section class="card"><h2>❓ Quick questions</h2>
<div class="sub">
<h3>Do your links cost me anything?</h3>
<p>No. If you buy after clicking a link, the price is the same — we may earn a small commission
(see <a href="/disclosure">Disclosure</a>).</p></div>
<div class="sub">
<h3>Are prices accurate?</h3>
<p>Prices are pulled live from Amazon and are indicative. Always confirm the price on Amazon before
ordering — deals change constantly.</p></div>
<div class="sub">
<h3>Can you pick a niche for me?</h3>
<p>We publish niches continuously. Want one you don't see?
<a href="/contact">Contact us</a> — we read everything.</p></div>
</section>
</main>
""".encode("utf-8")
    return head + body + _footer()


def render_niche(keyword, niche, saved_niches=None):
    """Crawlable niche page in the answer-first review layout: breadcrumbs,
    byline, human intro, ranked picks with honest pros/cons, comparison table,
    methodology + trust, FAQ, related niches."""
    items = niche.get("products") or []
    canonical = "/n/" + _slugify(keyword)
    title = "Best %s to Buy — Ranked Picks From Live Amazon Data" % keyword
    desc = (f"See the best {keyword}, ranked. We score live Amazon listings on "
            f"rating, review volume and price, then show you which to buy and "
            f"why — with honest pros and cons for each.")
    best = editorial.best_pick(items)
    ranked = "".join(
        editorial.pick_html(keyword, it, idx, items)
        for idx, it in enumerate(score_order(items)))
    graph = _product_graph(items)
    if best:
        graph.append(editorial.faq_jsonld(keyword, best))
    graph.append(editorial.breadcrumb_jsonld(keyword))
    jsonld = {"@context": "https://schema.org", "@graph": graph}
    head = _head(title, desc, canonical, canonical, jsonld=jsonld)
    body = f"""
<header><h1><a href="/" style="color:var(--accent);text-decoration:none">{SITE_NAME}</a></h1>
<nav><a href="/">🏠 Home</a><a href="/about">About</a><a href="/disclosure">Disclosure</a></nav></header>
<main>
<div class="card">
  {editorial.breadcrumbs_html(keyword)}
  <h1>Best {_clean(keyword)}: ranked picks</h1>
  {editorial.byline_html(keyword, items, best) if best else ""}
  <p class="lede">{_clean(editorial.intro(keyword, items))}</p>
  {editorial.trust_block_html()}
  {editorial.faq_html(keyword, best) if best else ""}
  <h2>The ranked list</h2>
  {ranked}
  {editorial.comparison_html(items) if items else ""}
  {editorial.methodology_html()}
  {editorial.related_html(keyword, saved_niches) if saved_niches else ""}
</div>
</main>
""".encode("utf-8")
    return head + body + _footer()


def score_order(items):
    return [it for it, _s in editorial.score_items(items)]


def indexable_urls(saved_niches, base_url=None):
    """Absolute URLs that belong in the sitemap + IndexNow submissions.

    Mirrors render_sitemap() but returns ready-to-submit absolute URLs.
    """
    base = (base_url or BASE_URL).rstrip("/")
    urls = [base + "/"]
    for page in STATIC_PAGES:
        urls.append(base + "/" + page)
    for n in (saved_niches or []):
        urls.append(base + "/n/" + _slugify(n["keyword"]))
    return urls


def render_sitemap(entries):
    """entries: list of (url_path, lastmod). Returns sitemap.xml bytes."""
    urls = "".join(
        f"<url><loc>{BASE_URL}{_clean(p)}</loc><lastmod>{lm}</lastmod></url>\n"
        for p, lm in entries)
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n"
    return body.encode("utf-8")


def render_robots():
    return f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n".encode("utf-8")

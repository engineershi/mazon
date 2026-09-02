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

import editorial

SITE_NAME = "pstore"
SITE_DESC = "Hand-picked Amazon product picks by niche."
BASE_URL = os.environ.get("PSTORE_URL", "https://pstore-gxbv.onrender.com").rstrip("/")

# Optional Google Search Console ownership token — emits <meta name="google-site-verification">.
GOOGLE_SITE_VERIFICATION = os.environ.get("PSTORE_GOOGLE_SITE_VERIFICATION", "")
# Organization identity shown in JSON-LD (schema.org Organization / Person).
ORG_NAME = SITE_NAME
ORG_URL = BASE_URL
CONTACT_URL = BASE_URL + "/contact"
AUTHOR_NAME = "pstore Editorial Team"


def _org_jsonld():
    """Small Organization node referenced by other graphs (WebSite/Article)."""
    return {
        "@type": "Organization", "name": SITE_NAME,
        "url": ORG_URL, "logo": ORG_URL + "/og/home",
        "contactPoint": {"@type": "ContactPoint", "url": CONTACT_URL},
    }


def _website_jsonld():
    return {
        "@context": "https://schema.org", "@type": "WebSite",
        "name": SITE_NAME, "url": ORG_URL,
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint",
                       "urlTemplate": ORG_URL + "/n/{search_term_string}"},
            "query-input": "required name=search_term_string",
        },
    }


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


def _head(title, desc, canonical, path, jsonld=None, og_image=None, noindex=False):
    img_html = ""
    if og_image:
        abs_img = og_image if str(og_image).startswith("http") else BASE_URL + og_image
        img_html = (f'<meta property="og:image" content="{_clean(abs_img)}">\n'
                    f'<meta name="twitter:image" content="{_clean(abs_img)}">\n')
    gsc = (f'<meta name="google-site-verification" content="{_clean(GOOGLE_SITE_VERIFICATION)}">\n'
           if GOOGLE_SITE_VERIFICATION else "")
    rob = ('<meta name="robots" content="noindex,nofollow">\n' if noindex else "")
    head = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_clean(title)} | {SITE_NAME}</title>
<meta name="description" content="{_clean(desc)}">
{rob}<link rel="canonical" href="{_clean(BASE_URL + path)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{_clean(title)}">
<meta property="og:description" content="{_clean(desc)}">
<meta property="og:url" content="{_clean(BASE_URL + path)}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_clean(title)}">
<meta name="twitter:description" content="{_clean(desc)}">
{img_html}{gsc}<link rel="stylesheet" href="/style.css">
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
    <a href="/">Home</a> · <a href="/blog">Blog</a> · <a href="/about">About</a> · <a href="/contact">Contact</a> ·
    <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · <a href="/disclosure">Disclosure</a> ·
    <a href="/sitemap.xml">Sitemap</a>
  </p>
</footer>
<div class="totop" aria-hidden="false">
  <a href="#top" aria-label="Back to top">&uarr;</a>
</div>
<script src="/ui.js" defer></script>
</body>
</html>
""".encode("utf-8")


# ------------------------------------------------------------------ info pages
STATIC_PAGES = ["about", "contact", "privacy", "terms", "disclosure"]
CONTACT_EMAIL = os.environ.get("PSTORE_CONTACT", "hello@pstore-gxbv.onrender.com")


def _page_header():
    return f"""<header id="top"><h1><a href="/" style="color:var(--accent);text-decoration:none">{SITE_NAME}</a></h1>
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
  <li><b>Opt-in emails.</b> If you join our niche-update list, we keep the email address and the
  niche you signed up for, so we can send the updates you asked for. You can unsubscribe from any
  email we send, or hit the unsubscribe link on any email, and we stop immediately.</li>
  <li><b>Click analytics.</b> When you click an affiliate link on our pages, we record a hashed
  fingerprint of your visit (never a raw address) to see which picks get clicked. We do not build
  cross-site profiles.</li>
  <li><b>Affiliate cookies.</b> When you click an affiliate link to Amazon, Amazon may set a short
  cookie that lets them credit the referral. We don't see or store your Amazon account data.</li>
  <li><b>Search-engine tokens.</b> We submit our pages to search engines (e.g. via IndexNow/Bing).
  No personal data is involved.</li>
</ul>
<h2>What we don't</h2>
<p>We have no accounts and no payment processing. We don't sell or rent email addresses, we don't
build profiles, track you across other sites, or use advertising trackers beyond the affiliate
relationship above.</p>
<h2>Emails</h2>
<p>Email addresses are only used to send the niche updates you opted into. Every email includes a
working unsubscribe link, and unsubscribing removes you from future sends.</p>
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


def optin_html(keyword, source="niche", anchor=""):
    """Email-capture widget: freq 0->1 niche updates. Hooks rendered by courier.js."""
    kw = _clean(keyword or "picks")
    label = ("Updates when these %s picks change" % keyword) if keyword else "The pstore picks note"
    sub = ("One honest email when this page's picks move — price drops, sold-out swaps, "
           "new top-rated options. Unsubscribe any time.")
    fid = ' id="%s"' % _clean(anchor) if anchor else ""
    return f"""<form class="courier card"{fid} action="/subscribe" method="post">
  <h3>{label}</h3>
  <p class="hint">{sub}</p>
  <div class="courier-row">
    <input type="text" name="first_name" placeholder="First name (optional)" autocomplete="given-name" maxlength="80">
    <input type="email" name="email" placeholder="you@example.com" required autocomplete="email">
    <input type="hidden" name="keyword" value="{kw}">
    <button type="submit" class="warm">Notify me</button>
  </div>
  <p class="courier-msg"></p>
  <noscript><p class="hint">Email us a note at {CONTACT_EMAIL} to be notified when this page updates.</p></noscript>
</form>"""


def courier_script():
    return '<script src="/courier.js" defer></script>'.encode("utf-8")


def render_landing(saved_niches):
    """Storefront-style home: value prop, how-we-pick, niche index, FAQ."""
    jsonld = {
        "@context": "https://schema.org", "@graph": [
            {"@type": "WebSite", "name": SITE_NAME, "url": BASE_URL,
             "potentialAction": {"@type": "SearchAction",
                                 "target": {"@type": "EntryPoint",
                                            "urlTemplate": BASE_URL + "/n/{search_term_string}"},
                                 "query-input": "required name=search_term_string"}},
            {"@type": "Organization", "name": SITE_NAME, "url": BASE_URL},
        ],
    }
    head = _head(SITE_DESC, SITE_DESC, "/", "/", jsonld=jsonld)
    top_pick_niches = saved_niches or []
    # comparison preview of the single most-picked niche (scannable, table-flow pill)
    comp_kw = ""
    comp_preview = ""
    for n in (top_pick_niches or []):
        if n.get("products"):
            comp_kw = n["keyword"]
            comp_preview = editorial.comparison_html(n["products"])
            break
    body = f"""
<header id="top"><h1><a href="/" style="color:var(--accent);text-decoration:none">{SITE_NAME}</a></h1>
<p class="tagline">{_clean(SITE_DESC)}</p>
<nav style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
<a class="chip" href="#top-picks">🏆 Top picks</a>
<a class="chip" href="#niches">🗂 Niches</a>
<a class="chip" href="#method">🔬 How we pick</a>
<a class="chip" href="#notify">✉️ Stay updated</a>
<a class="chip" href="#faq">❓ FAQ</a>
 <a class="chip" href="/blog">📝 Blog</a>
 </nav></header>
<main data-niche="home" data-source="home" data-keyword="best amazon niche picks">
<section class="card hero-home" id="top-picks">
  <h1 style="font-size:30px;line-height:1.15">Find the best <span style="color:var(--accent)">Amazon picks</span>, by niche — before you scroll once.</h1>
  <p style="font-size:16px;color:var(--muted);max-width:720px">Each niche page ranks the strongest products on Amazon for a topic — using live price, rating
  and review signals — so the verdict and the price are above the fold, and the proof is right below it.
  No endless listicles. No guesswork.</p>
  <p class="hint">Honest picks. Live data. Affiliate-tagged links (see our <a href="/disclosure">Disclosure</a>).</p>
</section>
{editorial.quick_picks_band(saved_niches)}
{comp_preview and ("<section class='card'><h2>Compare the shortlist — {0}</h2><p class='hint'>Scannable table of the live picks for {1}. Swipe or scroll sideways if it overflows.</p>{2}</section>".format(_clean(comp_kw.title()), _clean(comp_kw), comp_preview)) or ""}
{editorial.home_trust_strip()}
{optin_html("", "home", anchor="notify")}

{editorial.niche_grid(saved_niches, anchor="niches")}

<section class="card" id="method"><h2>🌱 How we pick</h2>
<div class="features">
  <div class="feature"><h3>⛏️ Niche mining</h3>
  <p>We expand each topic through Amazon's own autosuggest index to find the terms real shoppers use.</p></div>
  <div class="feature"><h3>📊 Real signals</h3>
  <p>Products are ranked on demand and saturation from live listings — price, rating and review volume.</p></div>
  <div class="feature"><h3>🛒 Shop on Amazon</h3>
  <p>Every pick links straight to the product on Amazon. Purchases may earn us a commission at no cost to you.</p></div>
</div>
<div class="trust">
  <h3>The one thing most review sites skip</h3>
  <p>Most “best X to buy” pages make you wade through 2,000 words before showing a price, and never tell you
  when their data went stale. We do the opposite: the pick, the rank and the live price come first, and every
  page says it reflects <b>current Amazon listings</b> — prices move, so we re-pull rather than guess.</p>
</div></section>

<section class="card" id="faq"><h2>❓ Quick questions</h2>
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
<script src="/courier.js" defer></script>
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
    graph.append(_org_jsonld())
    jsonld = {"@context": "https://schema.org", "@graph": graph}
    og = BASE_URL + "/og/" + _slugify(keyword)
    head = _head(title, desc, canonical, canonical, jsonld=jsonld, og_image=og,
                 noindex=not bool(items))
    body = f"""
<header id="top"><h1><a href="/" style="color:var(--accent);text-decoration:none">{SITE_NAME}</a></h1>
<nav><a href="/">🏠 Home</a><a href="/about">About</a><a href="/disclosure">Disclosure</a><a href="/lp/{_clean(_slugify(keyword))}">One-pager →</a></nav></header>
<main data-niche="{_clean(_slugify(keyword))}" data-source="niche" data-keyword="{_clean(keyword)}">
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
{optin_html(keyword, "niche")}
<script src="/courier.js" defer></script>
<script src="/table-flow.js" defer></script>
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
        urls.append(base + "/lp/" + _slugify(n["keyword"]))
    return urls


def render_blog(saved_niches):
    """Public /blog landing: index of editorial articles, one per saved niche.
    Each card links to the full ranked notebook (/n/<slug>) and is SEO-shaped
    (title/desc/canonical + indexable)."""
    niches = [n for n in (saved_niches or []) if n.get("products")]
    head = _head("The blog", "Ranked buying guides, data methodology and honest picks, niche by niche.",
                 "/blog", "/blog", noindex=len(niches) == 0)
    cards = ""
    for n in niches:
        slug = _slugify(n["keyword"])
        best = editorial.best_pick(n["products"])
        title = "The best %s: a ranked, data-backed pick" % n["keyword"]
        synopsis = (best or {}).get("title") or n["keyword"]
        cards += f"""
<article class="card">
  <a class="blog-title" href="/n/{_clean(slug)}"><h2>{_clean(title)}</h2></a>
  <p class="hint">Top pick · {_clean(synopsis[:90])}{"…" if len(synopsis) > 90 else ""}</p>
  <p class="muted">{editorial.reading_minutes(n["keyword"], n["products"], best)} read · {len(n["products"] or 0)} products ranked from live Amazon data</p>
</article>"""
    if not cards:
        cards = '<section class="card"><h2>Fresh guides on the way</h2><p class="hint">We\'re ranking new niches now. Check back soon or <a href="/">browse the picks</a>.</p></section>'
    body = f"""<header id="top"><h1><a href="/" style="color:var(--accent);text-decoration:none">{SITE_NAME}</a></h1>
<p class="tagline">{_clean(SITE_DESC)}</p>
<nav><a href="/">🏠 Home</a><a href="/blog">📝 Blog</a><a href="/disclosure">Disclosure</a></nav></header>
<main data-niche="blog" data-source="blog">
<section class="hero-home card">
  <h1 style="font-size:30px;line-height:1.15">The <span style="color:var(--accent)">blog</span>.</h1>
  <p style="font-size:16px;color:var(--muted);max-width:720px">Every guide is a data-backed ranking of the best Amazon pick for that niche —
  live price, rating and review signals, honest methodology. No filler.</p>
</section>
{cards}
</main>
""".encode("utf-8")
    return head + body + _footer()


def render_sitemap(entries):
    """entries: list of (url_path, lastmod). Returns sitemap.xml bytes."""
    urls = "".join(
        f"<url><loc>{BASE_URL}{_clean(p)}</loc><lastmod>{lm}</lastmod></url>\n"
        for p, lm in entries)
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n"
    return body.encode("utf-8")


def render_robots():
    return f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n".encode("utf-8")


# ------------------------------------------------------------------ audit
# Structural, self-serve SEO audit used by /admin/seo and /api/seo-audit.
# Everything is computed from the actual page a niche would render, so the
# checkboxes reflect the live site rather than aspirational settings.

def _meta_lengths(items):
    """Title + description character counts for a niche page (rule of thumb:
    titles 30–60, descriptions 70–160)."""
    keyword = (items or {}).get("keyword") or ""
    title = "Best %s to Buy — Ranked Picks From Live Amazon Data" % keyword
    desc = (f"See the best {keyword}, ranked. We score live Amazon listings on "
            f"rating, review volume and price, then show you which to buy and "
            f"why — with honest pros and cons for each.")
    return len(title), len(desc)


def _words(items):
    """Rough prose word count for the niche page body (audit only)."""
    prods = (items or {}).get("products") or []
    kw = (items or {}).get("keyword") or ""
    if not prods:
        return 0
    n = len(editorial.intro(kw, prods).split()) + 120
    best = editorial.best_pick(prods)
    n += editorial.reading_minutes(kw, prods, best) * 190
    return n


def _slugify_safe(text):
    try:
        return _slugify(text)
    except Exception:
        return "niche"


def audit_niche(niche):
    """One row of the SEO audit for a saved niche (no network)."""
    kw = niche.get("keyword") or ""
    slug = _slugify_safe(kw)
    prods = niche.get("products") or []
    best = editorial.best_pick(prods) if prods else None
    tl, dl = _meta_lengths(niche)
    wc = _words(niche)
    checks = {
        "has_products": bool(prods),
        "title_ok": 30 <= tl <= 60,
        "desc_ok": 70 <= dl <= 160,
        "og_image": bool(best),
        "schema": bool(prods),
        "word_count": wc >= 300,
    }
    return {
        "keyword": kw, "slug": slug,
        "url": "/n/" + slug,
        "products": len(prods),
        "top_asin": (best or {}).get("asin") or "",
        "title_len": tl, "desc_len": dl,
        "words": wc,
        "checks": checks,
        "indexable": bool(prods) and checks["title_ok"] and checks["desc_ok"],
    }


def audit_sites(niches):
    """Global audit summary for the /admin/seo header strip + config status."""
    rows = [audit_niche(n) for n in (niches or [])]
    passable = sum(1 for r in rows if r["indexable"])
    return {
        "niches": rows,
        "count": len(rows),
        "indexable": passable,
        "needs_work": len(rows) - passable,
        "site_url": BASE_URL,
        "google_verification": bool(GOOGLE_SITE_VERIFICATION),
        "sitemap": "/sitemap.xml",
        "robots": "/robots.txt",
        "org": {"name": ORG_NAME, "url": ORG_URL},
    }

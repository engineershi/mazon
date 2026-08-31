# -*- coding: utf-8 -*-
"""pstore SEM layer: turn a single "best X" review page into a search funnel.

Unlike the social/email/buyer-push tools, this suite is about GROWING the pages
a search engine already sends traffic to. Everything is computed offline from data
we hold (keyword, saved niche, link structure, existing FAQ) plus the same keyless
Amazon autosuggest signal the rest of the app uses — no fabricated clicks, volumes
or rankings.

Per niche it produces:
  * long-tail expansion   — related search phrases (from Amazon autosuggest) a
                            "best X" page should ideally cover, grouped by intent
  * intent brief          — the one question to answer, the H1/meta/FAQ targets
                            and quick-win fixes pulled from the live SEO audit
  * people-also-ask       — copy-ready FAQ prompts in the SERP's own phrasing,
                            seeded from the existing FAQ, with __blank__ markers
                            where no answer exists yet
  * performance checklist — file-size / mobile / alt / speed / internal-link
                            hygiene, honest and non-guaranteeing
  * live-url status       — canonical, sitemap, robots and IndexNow submit info
"""
import re

import amazon
import editorial


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "niche"


def _hashwords(keyword):
    words = re.findall(r"[a-z0-9]+", (keyword or "").lower())
    return "".join(w.capitalize() for w in words) or "BestPicks"


def _dedupe(iterable, key=None):
    seen, out = set(), []
    for it in iterable:
        k = key(it) if key else it
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def longtail(keyword):
    """Related search phrases a "best X" page should ideally cover, seeded from
    Amazon's own autosuggest index. Falls back to honest intent labels."""
    seed = keyword.strip().lower()
    if not seed:
        return []
    ideas = []
    try:
        ideas = amazon.autosuggest(seed, limit=12)
    except Exception:
        ideas = []
    phrases = _dedupe(str(p).strip() for p in ideas if str(p or "").strip())
    # Make sure the literal seed is present and labelled as the target.
    phrases = _dedupe([seed] + phrases)
    grouped = []
    for i, p in enumerate(phrases):
        intent = "target" if i == 0 else _intent(p)
        grouped.append({"phrase": p, "intent": intent, "slug": _slug(p)})
    return grouped


def _intent(phrase):
    """Coarse SERP intent guess from surface keywords; honest, never a promise."""
    p = phrase.lower()
    if any(w in p for w in ("best", "top", "review", "compared", "vs", "versus",
                            "guide", "rated", "worth")):
        return "commercial"
    if any(w in p for w in ("how", "what", "why", "does", "is", "work",
                            "size", "fit", "difference")):
        return "informational"
    return "transactional"


def intent_brief(keyword, niche, audit_row=None):
    """The one job of the page, the H1/meta/FAQ targets, and quick-win fixes."""
    ar = audit_row or {}
    checks = ar.get("checks") or {}
    prods = (niche or {}).get("products") or []
    fixes = []
    if not prods:
        fixes.append({"label": "Add products", "detail": "No listings yet — mine and save the niche so the page is indexable."})
    if not checks.get("title_ok", True):
        fixes.append({"label": "Slim the <title>", "detail": "30–60 chars reads best in the SERP."})
    if not checks.get("desc_ok", True):
        fixes.append({"label": "Tighten the meta description", "detail": "Keep it 70–160 chars with the keyword up front."})
    if prods and not checks.get("schema", True):
        fixes.append({"label": "Structured data", "detail": "Product/FAQ schema helps rich results."})
    if not fixes:
        fixes.append({"label": "On track", "detail": "This page is already indexable and well-formed."})

    faq_q = "What's the best %s right now?" % keyword

    return {
        "keyword": keyword,
        "primary_question": bare_question(keyword),
        "search_intent": "Commercial — shoppers comparing %s before they buy." % keyword,
        "h1_target": "Best %s: ranked picks" % keyword,
        "meta_target": ("See the best %s, ranked. We score live Amazon listings on rating, "
                        "review volume and price, then show which to buy and why." % keyword),
        "faq_target": faq_q,
        "fixes": fixes,
        "word_count": ar.get("words", 0),
    }


def bare_question(keyword):
    return "Which %s actually earn the hype?" % keyword


def people_also_ask(keyword, niche=None):
    """Copy-ready FAQ prompts in SERP phrasing, seeded from the page's own FAQ."""
    prods = (niche or {}).get("products") or []
    best = editorial.best_pick(prods) if prods else None
    qas = []
    if best:
        for q, a in editorial.faq(keyword, best):
            qas.append({"question": q, "answer": a})
    # Suggested prompts the operator can add; leave the copy as a blank to fill.
    suggestions = [
        "%s — how do I choose?" % keyword.title(),
        "Is %s worth the price?" % (keyword or "it"),
        "What do real buyers say about %s?" % keyword,
        "How often do %s prices change?" % keyword,
    ]
    existing = {q.lower() for q, _a in qas}
    for s in suggestions:
        if s.lower() in existing:
            continue
        qas.append({"question": s, "answer": "__blank__"})
    return qas


def performance_checklist(keyword, niche=None):
    """Non-guaranteeing, honest page-performance hygiene items the operator can
    act on. Some are dynamic (internal links, indexable); the rest are static."""
    prods = (niche or {}).get("products") or []
    items = [
        {"label": "Direct, tagged Amazon links only",
         "detail": "Every outbound link uses amazon.affiliate_url — no cloaked /go links."},
        {"label": "Mobile-first layout",
         "detail": "Cards collapse to single-column and tables scroll horizontally on small screens."},
        {"label": "Fast, cacheable share images",
         "detail": "og:image is a server-generated SVG served with a 1-hour cache — no heavy image uploads."},
        {"label": "Clear internal linking",
         "detail": "Related-niche chips + the footer link hub cross-connect every review page."},
    ]
    if prods:
        items.insert(0, {"label": "Indexable with structured data",
                         "detail": "Page carries ItemList/FAQ/Breadcrumb + Organization JSON-LD."})
    else:
        items.insert(0, {"label": "Not yet indexable",
                         "detail": "An empty review page is served noindex until products are saved."})
    items.append({"label": "Fresh data source",
                  "detail": "Products are re-pulled from Amazon on an ongoing basis — no stale hardcoding."})
    return items


def page_status(keyword, base_url, indexnow_key=None, sitemap_entries=None):
    """Live-url status for the niche: canonical + where it's advertised."""
    slug = _slug(keyword)
    base = (base_url or "").rstrip("/")
    in_sitemap = any(slug == _slug(p) for p in (sitemap_entries or []))
    return {
        "keyword": keyword,
        "slug": slug,
        "canonical": "%s/n/%s" % (base, slug),
        "og_image": "%s/og/%s" % (base, slug),
        "landing_url": "%s/lp/%s" % (base, slug),
        "sitemap": in_sitemap,
        "indexnow_key": bool(indexnow_key),
        "sitemap_url": base + "/sitemap.xml",
        "robots_url": base + "/robots.txt",
    }


def brief(keyword, niche, base_url, audit_row=None, indexnow_key=None, sitemap_entries=None):
    """Assemble the full SEM payload for one niche."""
    return {
        "keyword": keyword,
        "slug": _slug(keyword),
        "longtail": longtail(keyword),
        "intent": intent_brief(keyword, niche, audit_row),
        "paa": people_also_ask(keyword, niche),
        "performance": performance_checklist(keyword, niche),
        "page": page_status(keyword, base_url, indexnow_key, sitemap_entries),
    }

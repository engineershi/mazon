# -*- coding: utf-8 -*-
"""pstore editorial layer: the human voice and trust machinery that turns a bare
product grid into an answer-first, E-E-A-T-friendly review page.

Everything here is generated from data we actually hold (live listings: price,
rating, review volume, availability) and stays deliberately honest: we never
claim physical testing, never invent product features, and never let a
commission decide placement. Pros/cons are statements about the real data, not
marketing flourish.
"""
import datetime
import hashlib
import html
import math
import os
import re

import amazon

BASE_URL = os.environ.get("PSTORE_URL", "https://pstore-gxbv.onrender.com").rstrip("/")


def _clean(s):
    return html.escape(str(s or ""), quote=True)


def _h(keyword, salt=""):
    return int(hashlib.sha1(("%s:%s" % (keyword, salt)).encode("utf-8")).hexdigest(), 16)


def _slug(text):
    s = (text or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "niche"


def _price(item):
    p = item.get("price")
    if not isinstance(p, (int, float)):
        return None
    return "%s%0.2f" % (amazon.currency_symbol(item.get("currency")), p)


def _median(values):
    vals = sorted(v for v in values if isinstance(v, (int, float)) and v > 0)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _review_hum(reviews):
    if isinstance(reviews, (int, float)) and reviews >= 1000:
        return "%sk" % round(reviews / 1000.0)
    return "%s" % (int(reviews) if isinstance(reviews, (int, float)) else reviews)


def _display(item):
    return item.get("title") or item.get("asin") or "this pick"


def score_items(items):
    """Rank scoring: rating weight, log-scaled review volume, price closeness
    to the list median. Higher is better."""
    med = _median([it.get("price") for it in items])
    scored = []
    for it in items:
        s = 0.0
        if isinstance(it.get("stars"), (int, float)):
            s += it["stars"] * 2.0
        rev = it.get("reviews") or 0
        if isinstance(rev, (int, float)) and rev > 0:
            s += min(math.log10(rev) / 2.0 * 5.0, 5.0)
        p = it.get("price")
        if isinstance(p, (int, float)) and med:
            s += max(min((med - p) / med, 1.0), -1.0)
        scored.append((s, it.get("reviews") or 0, -(it.get("price") or 0.0), it))
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [(t[3], t[0]) for t in scored]


def best_pick(items):
    scored = score_items(items or [])
    return scored[0][0] if scored else None


_INTROS = (
    "Shopping for {kw}? You're far from alone — it's one of the most-browsed "
    "{kw} searches on Amazon. We pulled the current live listings, weighed "
    "ratings and review volume against price, and trimmed the field to the "
    "picks most shoppers actually settle on.",
    "Choosing a {kw} shouldn't mean an evening of open tabs. We dug through "
    "the live Amazon results for {kw}, ranked them by rating and review "
    "volume, and kept only the products we'd feel comfortable pointing you to.",
    "There's no shortage of {kw} listings on Amazon — the problem is that most "
    "of them sink in the noise. We did the sifting: these are the {kw} "
    "products that keep coming out on top across price, rating and how many "
    "buyers have already weighed in.",
    "If you've typed \"{kw}\" into an Amazon search box, you've seen how "
    "cluttered the results are. This page is the short version: the strongest "
    "live listings for {kw}, ranked with real signals like star rating, review "
    "count and price — not a paid placement.",
    "Picking the right {kw} comes down to a few honest signals: how well it's "
    "rated, how many people have actually bought and reviewed it, and whether "
    "the price makes sense for what you get. Here's what the live Amazon data "
    "says for {kw}.",
)


def intro(keyword, items):
    tpl = _INTROS[_h(keyword, "intro") % len(_INTROS)]
    return tpl.format(kw=keyword)


def rank_badge(item, idx, items):
    badge, reason = "Best overall", "leads this list on rating, reviews and price together"
    scores = score_items(items)
    top = scores[0][0] if scores else None
    if idx == 1:
        if top and item.get("price") and isinstance(top.get("price"), (int, float)) \
                and item["price"] < top["price"]:
            badge, reason = "Great value", "prices below the top pick while still earning a strong score"
        elif top and item.get("reviews") and top.get("reviews") \
                and item["reviews"] > top["reviews"]:
            badge, reason = "Readers' favorite", "more total reviews than the top pick"
        else:
            badge, reason = "Solid runner-up", "a close second across rating, reviews and price"
    elif idx > 1:
        badge, reason = "Worth a look", "made the shortlist on the same live signals"
    return badge, reason


def quick_take(item, items):
    stars = item.get("stars")
    rev = item.get("reviews")
    p = item.get("price")
    pieces = []
    if isinstance(p, (int, float)):
        pieces.append("It sits at %s on this page." % _price(item))
    if isinstance(stars, (int, float)) and isinstance(rev, (int, float)) and rev > 0:
        pieces.append("It averages %s★ across %s Amazon ratings." % (stars, _review_hum(rev)))
    elif isinstance(stars, (int, float)):
        pieces.append("It averages %s★ on Amazon's current data." % stars)
    if not pieces:
        return "One of the more-browsed listings in this niche right now."
    return " ".join(pieces)


def pros_cons(item, items):
    stars = item.get("stars")
    rev = item.get("reviews")
    p = item.get("price")
    prices = [it.get("price") for it in items if isinstance(it.get("price"), (int, float))]
    low = min(prices) if prices else None
    med = _median(prices)
    pros, cons = [], []

    if isinstance(rev, (int, float)) and rev >= 500:
        pros.append("Well-reviewed: %s shoppers have already rated it, a strong demand signal." % _review_hum(rev))
    if isinstance(stars, (int, float)) and stars >= 4.2:
        pros.append("Solid average rating of %s★ — consistently higher than the typical listing." % stars)
    if isinstance(p, (int, float)) and isinstance(med, (int, float)) and p < med:
        pros.append("Priced below the mid-point of this list (%s)." % _price(item))
    if isinstance(p, (int, float)) and low is not None and p == low:
        pros.append("The lowest price in this shortlist — good if budget is the deciding factor.")

    if prices and isinstance(p, (int, float)) and p == max(prices):
        cons.append("At the top of the price range here (%s) — fine if the specs justify it, but it's not the budget pick." % _price(item))
    elif isinstance(p, (int, float)) and isinstance(med, (int, float)) and p > med:
        cons.append("Slightly above the list median in price (%s)." % _price(item))
    if isinstance(rev, (int, float)) and rev < 100:
        cons.append("A smaller review base right now, so the verdict is less battle-tested.")
    if isinstance(stars, (int, float)) and stars < 4.0:
        cons.append("Average rating below 4★ — worth skimming the negative reviews before ordering.")

    if not pros:
        pros.append("Made the shortlist on current demand and listing strength for this niche.")
    if not cons:
        cons.append("Price and availability change often on Amazon — confirm today's details before you order.")
    return pros, cons


def comparison_rows(items, start=1, top_asin=None):
    """Rows for the scannable comparison table."""
    rows = []
    scored = score_items(items)
    for idx, (it, _s) in enumerate(scored):
        asin = it.get("asin") or ""
        rows.append({
            "rank": "#%d" % (start + idx),
            "title": it.get("title") or it.get("asin") or "—",
            "asin": asin,
            "price": _price(it) or "—",
            "stars": ("★ %s" % it["stars"]) if isinstance(it.get("stars"), (int, float)) else "—",
            "reviews": _review_hum(it["reviews"]) if isinstance(it.get("reviews"), (int, float)) and it["reviews"] >= 0 else "—",
            "url": it.get("url") or "",
            "top": bool(top_asin) and asin == top_asin,
            "badge": "Top pick" if asin == top_asin else ("Runner-up" if idx == 1 else "Picked"),
        })
    return rows


_FAQS = (
    ("What's the best {kw} right now?",
     "Based on current Amazon data, our top pick is {pick} at {price}. It "
     "leads the list because rating, review volume and price all point the "
     "same way. If your budget is tighter, the runner-up is worth a look too."),
    ("Are the prices on this page accurate?",
     "Prices are pulled live from Amazon but change constantly, so treat them "
     "as indicative. Always confirm the current price and availability on the "
     "Amazon listing before you order."),
    ("Do these links cost me anything?",
     "No. If you buy after clicking one, the price you pay is exactly the same. "
     "As an Amazon Associate we may earn a small commission on qualifying "
     "purchases — it's how the site stays free, and it never decides what's "
     "listed (see our Disclosure)."),
    ("How often is this {kw} list refreshed?",
     "The underlying product data is re-pulled from Amazon on an ongoing "
     "basis, and the ranking is recalculated from the same live signals each "
     "time — so the page reflects current listings rather than a frozen "
     "snapshot."),
)


def faq(keyword, best):
    out = []
    price = _price(best) or "a live Amazon price"
    pick = _display(best)
    for q, a in _FAQS:
        out.append((q.format(kw=keyword), a.format(kw=keyword, pick=pick, price=price)))
    return out


def related_niches(current, niches):
    """Up to 6 sibling niche pages, self-excluded, deterministically chosen."""
    pool = [n for n in (niches or [])
            if isinstance(n, dict) and n.get("keyword") and n["keyword"] != current]
    if not pool:
        return []
    return sorted(pool, key=lambda n: _h(n["keyword"], "related"))[:6]


def reading_minutes(keyword, items, best):
    words = len(intro(keyword, items).split()) + 90
    for it in (items or [])[:10]:
        p, c = pros_cons(it, items)
        words += len((" ".join(p) + " " + " ".join(c)).split())
    words += 140
    return max(2, round(words / 190 + 0.4))


def byline_html(keyword, items, best):
    today = datetime.date.today().strftime("%b %d, %Y")
    mins = reading_minutes(keyword, items, best)
    return ('<p class="byline">By the <a href="/about">pstore</a> '
            'editorial team · Updated %s · %d min read</p>' % (today, mins))


def breadcrumbs_html(keyword):
    return ('<nav class="crumbs"><a href="/">Home</a>'
            '<span class="sep">›</span><span>%s</span></nav>' % _clean(keyword))


def methodology_html():
    return ('<div class="trust"><h3>How we pick</h3>'
            '<p>Every list starts with live Amazon data: we pull current '
            'listings for the niche, then score each product on demand, '
            'average rating and review volume, with a nudge for sane pricing. '
            'No placement is for sale, no vendor can buy a slot, and anything '
            'that doesn\'t clear the bar is left off. That\'s the whole '
            'method — see our <a href="/about">About</a> page for more.</p></div>')


def trust_block_html():
    return ('<div class="trust"><h3>Why trust pstore</h3>'
            '<p>We\'re an independent picks site — not an Amazon seller and '
            'not paid to place products. Rankings come from the same data you '
            'can see on this page: rating, review volume and live price. Some '
            'links are Amazon Associates affiliate links (as an Amazon '
            'Associate we earn from qualifying purchases), which may earn us '
            'a commission if you buy — the price you pay never changes. '
            'Questions or corrections? We answer everything at '
            '<a href="/contact">Contact</a>.</p></div>')


def pick_html(keyword, item, idx, items):
    badge, why = rank_badge(item, idx, items)
    price = _price(item)
    p, c = pros_cons(item, items)
    pros = "".join("<li>%s</li>" % _clean(x) for x in p)
    cons = "".join("<li>%s</li>" % _clean(x) for x in c)
    stars = ('<span class="stars">★ %s</span>' % item.get("stars")) if isinstance(item.get("stars"), (int, float)) else ""
    reviews = ('<span class="meta-strong">%s ratings</span>' % _review_hum(item.get("reviews"))) \
        if isinstance(item.get("reviews"), (int, float)) and item["reviews"] >= 0 else ""
    cta = ""
    if item.get("url"):
        label = "Check price on Amazon" + (" — %s" % price if price else "")
        cta = '<a class="btn" href="%s" data-asin="%s" target="_blank" rel="nofollow sponsored noopener">%s</a>' \
              % (_clean(item["url"]), _clean(item.get("asin") or ""), label)
    return ('<div class="pick%s">'
            '<div class="pick-head"><span class="rank">#%d</span>'
            '<h3>%s</h3><span class="badge">%s</span></div>'
            '<p class="quick">%s</p>'
            '<p class="why">%s</p>'
            '<div class="pilo"><div><h4>Good to know</h4><ul class="pros">%s</ul></div>'
            '<div><h4>Watch out</h4><ul class="cons">%s</ul></div></div>'
            '<p class="starsline">%s %s</p>%s</div>'
            % (" top" if idx == 0 else "", idx + 1, _clean(_display(item)), badge,
               _clean(quick_take(item, items)), _clean(why), pros, cons,
               stars, reviews, cta))


def comparison_html(items):
    rows = comparison_rows(items, top_asin=(best_pick(items) or {}).get("asin"))
    body = "".join(
        "<tr%s><td>%s</td><td class='ct'><a href='%s' data-asin='%s'>%s</a></td><td>%s</td>"
        "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
            " class='top'" if r["top"] else "", r["rank"],
            _clean(r["url"]), _clean(r["asin"]), _clean(r["title"]),
            r["price"], r["stars"], r["reviews"], r["badge"])
        for r in rows)
    return ('<div class="table-wrap"><table class="cgrid"><caption>Compare the '
            'shortlist at a glance</caption><thead><tr><th>Rank</th>'
            '<th>Product</th><th>Price</th><th>Rating</th><th>Reviews</th>'
            '<th>Verdict</th></tr></thead><tbody>%s</tbody></table></div>' % body)


def faq_html(keyword, best):
    qas = faq(keyword, best)
    body = "".join(
        '<details class="faq"><summary>%s</summary><p>%s</p></details>'
        % (_clean(q), _clean(a)) for q, a in qas)
    return "<h2>Questions shoppers ask about %s</h2>%s" % (_clean(keyword), body)


def faq_jsonld(keyword, best):
    qas = faq(keyword, best)
    return {"@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q,
         "acceptedAnswer": {"@type": "Answer", "text": a}}
        for q, a in qas]}


def breadcrumb_jsonld(keyword):
    return {"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": BASE_URL + "/"},
        {"@type": "ListItem", "position": 2, "name": "%s picks" % keyword},
    ]}


def related_html(keyword, niches):
    rel = related_niches(keyword, niches)
    if not rel:
        return ""
    links = "".join(
        '<a class="chip" href="%s/n/%s">%s</a>'
        % (BASE_URL.rstrip("/"), _slug(kw), _clean(kw))
        for kw in [n["keyword"] for n in rel])
    return ("<h2>Keep exploring</h2><p>Browse more niches we've hand-picked:</p>"
            "<div class=\"chips\">%s</div>" % links)


def featured_pick(saved_niches):
    """Best single product across all niches, for the home-page hero."""
    best = None
    for n in (saved_niches or []):
        prods = n.get("products") or []
        top = best_pick(prods)
        if not top:
            continue
        score = score_items(prods)[0][1]
        if best is None or score > best[1]:
            best = (top, score, n.get("keyword") or n.get("name") or "")
    return best


def featured_html(saved_niches):
    f = featured_pick(saved_niches)
    if not f:
        return ""
    top, _score, kw = f
    url = top.get("url") or ""
    price = _price(top)
    stars = ('<span class="stars">★ %s</span>' % top.get("stars")) if isinstance(top.get("stars"), (int, float)) else ""
    price_txt = price and (" · " + price) or ""
    cta = ""
    if url:
        label = "Check price on Amazon" + (price and (" — " + price) or "")
        cta = '<a class="btn" href="%s" target="_blank" rel="nofollow sponsored noopener">%s</a>' \
              % (_clean(url), label)
    return ('<section class="card"><h2>Today&#39;s standout pick</h2>'
            '<div class="pick top">'
            '<div class="pick-head"><span class="rank">Best overall</span>'
            '<h3>%s</h3></div>'
            '<p class="quick">%s</p>'
            '<p class="starsline">%s%s · %s</p>'
            '<p class="why">Full ranking: <a href="%s">our %s picks</a>.</p>%s</div></section>'
            % (_clean(_display(top)), _clean(quick_take(top, [top])),
               stars, price_txt, kw,
               "/n/" + _slug(kw), _clean(kw), cta))


def quick_picks_band(saved_niches, count=3):
    """A 'Quick Verdict' band for the home hero: the best-scoring product from
    the best-scoring niches, each with its ranked badge, live price/rating, one
    line of reasoning and a primary CTA. Answer-first — a shopper gets a
    decision-ready pick above the fold without scrolling into a long review."""
    pools = []
    for n in (saved_niches or []):
        prods = n.get("products") or []
        top = best_pick(prods)
        if not top:
            continue
        kw = n.get("keyword") or n.get("name") or ""
        pools.append((top, score_items(prods)[0][1], kw))
    pools.sort(key=lambda t: t[1], reverse=True)
    if not pools:
        return ""
    cards = []
    for idx, (top, _score, kw) in enumerate(pools[:count]):
        badge, why = rank_badge(top, idx + 1, [top])
        price = _price(top)
        stars = ('<span class="stars">★ %s</span>' % top.get("stars")) \
            if isinstance(top.get("stars"), (int, float)) else ""
        reviews = ('<span class="r-count">%s ratings</span>' % _review_hum(top.get("reviews"))) \
            if isinstance(top.get("reviews"), (int, float)) and top["reviews"] >= 0 else ""
        cta = ""
        if top.get("url"):
            cta = '<a class="btn" href="%s" data-asin="%s" target="_blank" rel="nofollow sponsored noopener">Check price%s</a>' \
                  % (_clean(top["url"]), _clean(top.get("asin") or ""),
                     ((" — " + price) if price else ""))
        cards.append(
            '<div class="qpick%s">'
            '<div class="pick-head"><span class="rank">%s</span>'
            '<h3>%s</h3><span class="badge">%s</span></div>'
            '<p class="who">Best %s pick</p>'
            '<p class="quick">%s</p>'
            '<p class="why">%s</p>'
            '<p class="starsline">%s %s</p>%s</div>'
            % (" top" if idx == 0 else "", ("#%d" % (idx + 1)), _clean(_display(top)),
               _clean(badge), _clean(kw), _clean(quick_take(top, [top])), _clean(why),
               stars, reviews, cta))
    return '<section class="card qband"><h2>Quick verdict — today’s top picks</h2>' \
           '<p class="hint">Ranked from live Amazon data you can verify before you click.</p>' \
           '<div class="qgrid">%s</div></section>' % "".join(cards)


def home_trust_strip():
    """A transparent, honesty-first strip. Where most review sites hide their
    sourcing, we state it plainly — this is the differentiation we lean on."""
    return ('<section class="card trust-strip">'
            '<div class="trow">'
            '<div class="tcell"><b>Live prices</b><span>Prices pulled from current Amazon listings — not cached guesses.</span></div>'
            '<div class="tcell"><b>Independently ranked</b><span>Ranked by star rating, review volume and price — no paid placement.</span></div>'
            '<div class="tcell"><b>Verdict first</b><span>See the pick and the price before the long read. Justified below.</span></div>'
            '<div class="tcell"><b>Disclosure-first</b><span>Affiliate links, plainly labelled. Buying never changes the price to you.</span></div>'
            '</div></section>')


def niche_grid(saved_niches, limit=36):
    """Explore-niches grid: each tile links to its ranked page and shows how
    many live picks it holds, so the homepage doubles as a topic map."""
    if not saved_niches:
        return ""
    tiles = []
    for n in (saved_niches or [])[:limit]:
        kw = n.get("keyword") or n.get("name") or ""
        count = len(n.get("products") or [])
        slug = _slug(kw)
        tiles.append('<a class="ntile" href="/n/%s"><b>%s</b>'
                     '<span>%d ranked picks · live prices</span></a>'
                     % (_clean(slug), _clean(kw.title()), count))
    return '<section class="card"><h2>Explore the niches</h2>' \
           '<p class="hint">Every page below is fully crawlable and carries live, affiliate-tagged links.</p>' \
           '<div class="ngrid">%s</div></section>' % "".join(tiles)
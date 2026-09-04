# -*- coding: utf-8 -*-
"""pstore marketing engine: tools to push direct buyers to Amazon fast.

Everything is keyless and produces affiliate-tagged, copy-paste-ready output:

  * build_text_links  — clickable short text links for comments, DMs, bios
  * build_markdown     — Markdown link blocks for a niche's top products
  * build_email_draft  — a ready-to-send buyer email with all product links
  * build_qr_url       — a QR-ready URL a poster/scanner leads buyers to
  * expand_go          — legacy /go/<asin> resolver (still served for old links)
  * pick_for_buyers    — heuristic to surface the single best "buy this" pick

All outbound links are DIRECT, affiliate-tagged amazon.com/dp/<ASIN>?tag=...
URLs (no /go cloaking) so the site stays Amazon-Affiliate-compliant.
"""
import hashlib
import html
import re
import urllib.parse

import amazon


def clean_tag():
    return (amazon.AFFILIATE_TAG or "").strip() or "YOURTAG-20"


def _best_items(items, n=3):
    ranked = sorted(
        [it for it in (items or []) if it.get("asin")],
        key=lambda x: (x.get("reviews") or 0), reverse=True)
    return ranked[:n]


def _alternate_lines(items, exclude_asin, n=2):
    """Compact '- <title> - link' lines for the runner-up products, excluding
    the primary pick, to cross-sell on budget/spec grounds."""
    lines = []
    for it in _best_items(items, n + 1):
        if not it.get("asin") or it["asin"] == exclude_asin:
            continue
        if len(lines) >= n:
            break
        price = _price(it)
        lines.append("- %s (%s): %s" % (
            _clip(it.get("title"), 48), price or "see price",
            redirect_url(it["asin"])))
    return "\n".join(lines)


def pick_for_buyers(items):
    """Pick the single strongest product to push: most reviews, price tiebreak."""
    best = None
    for it in (items or []):
        if not it.get("asin"):
            continue
        if best is None:
            best = it
            continue
        if (it.get("reviews") or 0) > (best.get("reviews") or 0):
            best = it
        elif (it.get("reviews") or 0) == (best.get("reviews") or 0) and \
                (it.get("price") or 0) < (best.get("price") or 0):
            best = it
    return best


def build_text_links(items, label="View on Amazon"):
    """Plain text links: `best keto snacks - https://amzn.to/...`. Paste straight
    into a comment/DM/bio. Uses the direct tagged Amazon URL."""
    out = []
    for it in _best_items(items, 5):
        asin = it.get("asin")
        if not asin:
            continue
        out.append(f"- {label} ({it.get('title','')[:40]}): {redirect_url(asin)}")
    return "\n".join(out) if out else "(no products yet)"


def build_markdown(items, heading=None):
    """Markdown block with inline affiliate links — paste into blog/notion/gh."""
    lines = []
    if heading:
        lines.append(f"## {heading}")
    for it in _best_items(items, 5):
        title = (it.get("title") or "").strip()
        if not it.get("asin"):
            continue
        price_txt = ""
        if it.get("price"):
            price_txt = " - %s%0.2f" % (amazon.currency_symbol(it.get("currency")), it.get("price"))
        lines.append(f"- [{title}]({redirect_url(it.get('asin'))}){price_txt}")
    return "\n".join(lines)


def build_email_draft(items, subject="My top picks for you", opener="Hey! Here are my top picks I think you'll love:"):
    pick = pick_for_buyers(items)
    lines = [f"Subject: {subject}", "", opener, ""]
    if pick and pick.get("asin"):
        lines.append(f"🛒 Top pick: {pick.get('title')} — {redirect_url(pick.get('asin'))}")
        lines.append("")
    for it in _best_items(items, 6):
        if not it.get("asin"):
            continue
        lines.append(f"- {it.get('title')}: {redirect_url(it.get('asin'))}")
    lines += ["", "Happy shopping!", ""]
    return "\n".join(lines)


def build_post_template(items, caption="My top picks for this week 👇"):
    """Social post caption with each product on its own line."""
    lines = [caption, ""]
    for it in _best_items(items, 5):
        if not it.get("asin"):
            continue
        lines.append(f"• {it.get('title')} → {redirect_url(it.get('asin'))}")
    return "\n".join(lines)


# ------------------------------------------------------------------ sales funnel
# The "make the sale" layer: landing pages, email automation, DM scripts,
# review pipeline and boost campaigns — all copy-paste ready and disclaimer-
# safe (never fabricate specs or outcomes).
def _slug(kw):
    s = (kw or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "niche"


def _price(it):
    if not it.get("price"):
        return None
    return "%s%0.2f" % (amazon.currency_symbol(it.get("currency") or "USD"), it.get("price"))


def _clip(text, n=70):
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return (t if len(t) <= n else t[: n - 1].rstrip() + "…")


def build_landing_page(keyword, items, site_url=None):
    """Full standalone HTML sales page for the niche's top pick."""
    pick = pick_for_buyers(items)
    rest = [it for it in _best_items(items, 6) if it.get("asin") != (pick or {}).get("asin")]
    if not pick or not pick.get("asin"):
        return "<html><body><p>No products yet.</p></body></html>"
    slug = _slug(keyword)
    title = _clip(pick.get("title"), 90)
    price = _price(pick) or "—"
    stars = pick.get("stars")
    reviews = pick.get("reviews")
    go = amazon.affiliate_url(pick["asin"])
    rating = f"{stars}★ ({reviews:,} ratings)" if (stars and reviews) else "highly rated on Amazon"
    e = html.escape
    base = (site_url or "").rstrip("/")
    og_image = (base + "/og/" + slug) if base else ""
    bullet_lines = []
    for it in rest[:3]:
        b = f"<li>{e(_clip(it.get('title'), 60))} — <strong>{e(_price(it) or 'see Amazon')}</strong>"
        if it.get("stars"):
            b += f" · ⭐ {it.get('stars')}"
        if isinstance(it.get("reviews"), (int, float)):
            b += f" ({it.get('reviews'):,} reviews)"
        bullet_lines.append(b + "</li>")
    bullets = "".join(bullet_lines)
    faq = f"""
    <div class="qa"><b>Is this really the best {e(_slug(keyword).replace('-', ' '))} pick?</b>
      <p>It's the highest-social-proof pick from our live Amazon research — {rating}. Click through, compare, and Amazon's own listing page tells the rest.</p></div>
    <div class="qa"><b>How do I buy?</b>
      <p>Hit the button below. It takes you straight to this exact item on Amazon, ready to check out.</p></div>
    <div class="qa"><b>Shipping?</b>
      <p>Handled entirely by Amazon — just pick the delivery option that suits you at checkout.</p></div>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} — Best Picks</title>
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="The ranked best {e(keyword)} pick from live Amazon data — see why it wins, what it costs, and buy it in one click.">
<meta property="og:type" content="website">
<meta property="og:url" content="{e(base)}/lp/{e(slug)}">
<meta property="og:image" content="{e(og_image)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="The ranked best {e(keyword)} pick from live Amazon data.">
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    color:#2b2233; background:linear-gradient(180deg,#fff7ec,#fdf3ff); line-height:1.55; }}
  .wrap {{ max-width:720px; margin:0 auto; padding:36px 20px 60px; }}
  .card {{ background:#fff; border:1px solid #f3e6d4; border-radius:22px; padding:28px;
    box-shadow:0 14px 36px rgba(255,120,60,.10); }}
  h1 {{ font-size:26px; line-height:1.25; letter-spacing:-.4px; margin:10px 0; }}
  .badge {{ display:inline-block; background:#eee9ff; color:#7c5cff; font-weight:700;
    font-size:12px; padding:6px 14px; border-radius:999px; }}
  .price {{ font-size:30px; font-weight:800; color:#ff6b2c; letter-spacing:-.5px; }}
  .cta {{ display:block; text-align:center; background:linear-gradient(135deg,#ff6b2c,#ff873c);
    color:#fff; text-decoration:none; font-weight:800; font-size:18px; padding:16px;
    border-radius:999px; margin:18px 0 8px; box-shadow:0 10px 24px rgba(255,107,44,.35); }}
  .cta.small {{ background:linear-gradient(135deg,#7c5cff,#9b7bff); font-size:14px; padding:12px; }}
  .muted {{ color:#887b94; font-size:13px; }}
  ul {{ padding-left:18px; }}
  li {{ margin:8px 0; }}
  .qa {{ margin-top:14px; }}
  .qa p {{ margin:4px 0 10px; color:#5c5166; font-size:14px; }}
</style>
</head>
<body>
<main data-niche="{e(slug)}" data-source="landing">
<div class="wrap">
  <div class="card">
    <span class="badge">🏆 Top pick · {rating}</span>
    <h1>{e(title)}</h1>
    <p class="muted">Vetted from live Amazon search data · {e(_slug(keyword).replace('-', ' '))} experts' picks</p>
    <div class="price">{e(price)}</div>
    <a class="cta" href="{e(go)}" rel="nofollow sponsored noopener" data-asin="{e(pick['asin'])}" data-beacon="landing-cta">Get it on Amazon →</a>
    <div class="muted" style="text-align:center">Instant checkout · Amazon is the seller</div>
    <h3>Why it's our pick</h3>
    <ul>
      <li><strong>Proven demand</strong> — {rating} from verified Amazon buyers</li>
      <li><strong>Right price</strong> — {e(price or 'visible on Amazon')}</li>
      <li><strong>One click</strong> — button takes you straight to the listing</li>
    </ul>
    <h3>More from this niche</h3>
    <ul>{bullets}</ul>
    <a class="cta" href="{e(go)}" rel="nofollow sponsored noopener" data-asin="{e(pick['asin'])}" data-beacon="landing-cta">Check price &amp; reviews →</a>
    <h3>FAQs</h3>
    {faq}
    <p class="muted" style="margin-top:20px">As an Amazon Associate we earn from qualifying purchases.</p>
  </div>
</div>
</main>
<script src="/courier.js" defer></script>
</body>
</html>"""


def build_email_sequence(keyword, items):
    """5-email buyer sequence: value → proof → objection → urgency → review ask.
    Emails 1 & 5 also surface the runner-up alternatives so a reader who does
    not click the main pick still has a buying path (raises conversion and
    average order value)."""
    pick = pick_for_buyers(items)
    if not pick or not pick.get("asin"):
        return []
    title = _clip(pick.get("title"), 70)
    price = _price(pick)
    url = redirect_url(pick["asin"])
    stars = pick.get("stars")
    reviews = pick.get("reviews")
    proof = f"⭐ {stars}/5 from {reviews:,} Amazon buyers" if (stars and reviews) else "highly rated on Amazon"
    social_head = f"What {reviews:,} buyers already think" if reviews else "What buyers already think"
    alts = _alternate_lines(items, pick["asin"])
    alt1 = ("\n\nPrefer a different budget or spec? Two matched alternatives:\n" + alts
            ) if alts else ""
    alt5 = ("\n\nStill on the fence? The two we'd pick next were:\n" + alts + "\n"
            ) if alts else ""
    return [
        {"name": "Email 1 · Hook + value", "subject": f"Ignore if this isn't for you, but {title[:48]}…",
         "body": f"""Subject: Ignore if this isn't for you, but {title[:48]}…

Hi {{first_name}},

I found a {keyword} buy that a LOT of other buyers already swear by ({proof}).

My one-line take: it solves the problem, it's priced well{(' at ' + price) if price else ''}, and I'd buy it again without thinking.

👉 Take a peek: {url}
{alt1}
No strings, no code — just the link. If it's not your thing, hit delete.

— {{your_name}}"""},
        {"name": "Email 2 · Social proof", "subject": social_head,
         "body": f"""Subject: {social_head}

Hi {{first_name}},

Still on the fence about {title[:60]}?

Here's what you're buying into:
• {proof}
• Sold & shipped by Amazon (fast, painless returns)
• One clean link, checkout in 30 seconds

The doubters are the ones who never look: {url}

— {{your_name}}"""},
        {"name": "Email 3 · Objections", "subject": "3 questions people ask (answered)",
         "body": f"""Subject: 3 questions people ask (answered)

Hi {{first_name}},

If you're hesitating, it's usually one of these:

1. "Is it actually good?" → {proof}
2. "Is it worth the price?" → It's {price or 'competitively priced'} and it's the most-reviewed option in this niche.
3. "What if I don't like it?" → Amazon's return policy has your back.

Feeling more solid? → {url}

— {{your_name}}"""},
        {"name": "Email 4 · Soft urgency", "subject": "Heads up before this shifts",
         "body": f"""Subject: Heads up before this shifts

Hi {{first_name}},

Prices on Amazon move — sometimes daily. Right now {title[:52]} sits at {price or 'a competitive price'}, and if it fits your budget and your need, today is as good a day as any.

Lock it in before you forget: {url}

— {{your_name}}"""},
        {"name": "Email 5 · Follow-up + review", "subject": "One small favour + last link",
         "body": f"""Subject: One small favour (+ last link)

Hi {{first_name}},

Two things.

1) If you grabbed it — enjoy it! When Amazon asks for a review, 30 seconds of your honest words helps other buyers a ton. (All we ever ask: honest.)

 2) If you didn't — here's the link one more time, it's genuinely the pick: {url}
{alt5}
— {{your_name}}"""},
    ]


def _ai_copy(keyword, items, first_name=""):
    """Best-effort AI rewrite of the next email's subject+body. Returns a dict
    {subject, body} or None when no AI provider is configured (offline tests)
    or on any failure, so the deterministic templates are always the fallback.
    This is the "let AI write the sell copy" layer (Suby: sell like crazy,
    Brunson: story-driven, Cialdini: social proof) when a provider is wired."""
    try:
        import ai
        if not ai.configured():
            return None
        pick = pick_for_buyers(items)
        title = _clip((pick or {}).get("title"), 60) if pick else keyword
        hint = "product: " + title
        if first_name:
            hint += "; first name of the reader: " + first_name
        subj = ai.generate("email_subject", keyword, hint
                           + " | write ONLY one short subject line, <=9 words, no punctuation at the end")
        if not subj:
            return None
        subject = (subj[0] or "").strip()[:90]
        body_lns = [l for l in ai.generate("email_body", keyword,
                                           hint + " | 3 short plain-text paragraphs, no markdown, no Subject line, no greeting")
                    if (l or "").strip()]
        body = "\n\n".join(body_lns) if body_lns else None
        if not subject or not body:
            return None
        return {"subject": subject, "body": body}
    except Exception:
        return None


def build_converted_followup(keyword, items):
    """Deterministic follow-up for a lead who already clicked a product ASIN
    (segment = CONVERTED): the review ask (social proof) PLUS the value-ladder
    upsell / 'next rung up' backend offer (Brunson), so a converted lead is
    moved toward the higher tier instead of getting the same nurture emails
    again. AI copy is layered on top by the caller when a provider is wired."""
    pick = pick_for_buyers(items)
    if not pick or not pick.get("asin"):
        return None
    title = _clip(pick.get("title"), 52)
    url = redirect_url(pick["asin"])
    stars = pick.get("stars")
    reviews = pick.get("reviews")
    proof = (f"⭐ {stars}/5 from {reviews:,} Amazon buyers"
             if (stars and reviews) else "one of the most-reviewed picks in this niche")
    alts = _alternate_lines(items, pick["asin"])
    alt = ("If your needs have grown, the next rung up the value ladder we'd point to:\n"
           + alts) if alts else ""
    return {
        "name": "Follow-up · review + value-ladder upsell",
        "subject": "Enjoy it? One rung up the ladder (and a small favour)",
        "body": f"""Subject: Enjoy it? One rung up the ladder (and a small favour)

Hi {{first_name}},

If you grabbed {title} — enjoy it! When Amazon asks, 30 seconds of your honest review helps the next buyer decide. That's all we'd ever ask: your honest words, because {proof}.

The reason this pick wins is simple: it's the value sweet spot — solves the job, priced well, backed by real buyers.

{alt}
Or one more look at the same pick: {url}

Most people level up on the next tier once the basics are handled — same trust, one step more. No pressure, just the link.

— {{your_name}}""",
    }


def build_social_pack(keyword, items):
    """Per-platform caption + hashtags for one product pick."""
    pick = pick_for_buyers(items)
    if not pick or not pick.get("asin"):
        return {}
    url = redirect_url(pick["asin"])
    title = _clip(pick.get("title"), 60)
    stars = pick.get("stars")
    reviews = pick.get("reviews")
    proof = f"⭐ {stars}/5 · {reviews:,} reviews" if (stars and reviews) else "highly rated"
    tags = ["#" + s for s in _slug(keyword).split("-")] + ["#amazonfinds", "#amazondeals", "#musthave", "#recommended"]
    hook = f"{title} is my current {keyword} pick ({proof}) — link in the post 👇"
    return {
        "instagram": {"caption": f"{hook}\n\n👇 Grab it here:\n{url}\n\n{' '.join(tags[:8])}", "hashtags": " ".join(tags)},
        "tiktok": {"caption": f"POV: you found THE {keyword} buy ({proof}) 👀\n\nGrab it: {url}\n\n{' '.join(tags[:6])}", "hashtags": " ".join(tags[:8])},
        "facebook": {"caption": f"{hook}\n\n💳 {url}", "hashtags": "#shopping #amazonpicks"},
        "x": {"caption": f"Best {keyword} pick right now: {title}\n{proof}\n\n{url}\n\n{' '.join(tags[:5])}", "hashtags": " ".join(tags[:6])},
        "pinterest": {"caption": f"{title} — {keyword} essentials board-worthy 🛍️\n{url}", "hashtags": " ".join(tags)},
    }


def build_dm_conversation(keyword, items):
    """DM scripts: opener → objection handling → close → review ask."""
    pick = pick_for_buyers(items)
    if not pick or not pick.get("asin"):
        return {}
    url = redirect_url(pick["asin"])
    title = _clip(pick.get("title"), 60)
    stars = pick.get("stars")
    reviews = pick.get("reviews")
    proof = f"⭐ {stars}/5 from {reviews:,} buyers" if (stars and reviews) else "really well reviewed"
    return {
        "opener": f"Hey {{name}}! Random, but quick — you ever looked at {title[:55]}…? I wrote it up in 2 lines here {url} — honestly fair price and {proof}. No pressure, just thought of you.",
        "reply_price": f"Totally fair. It's {pick.get('price') and _price(pick) or 'actually reasonably priced'} and it's the most-reviewed option I could find ({proof}). If budget's tight, it's worth watching for a price dip — I'd bookmark {url}. 🙂{{first_name}}",
        "reply_compare": f"Good question! Between the options I tested, this one wins on {proof} + price. If you want, I can grab you the 2 runner-ups in the same list — just say the word.",
        "reply_wait": f"No rush at all. When you do want it, it's one click: {url}. It's the same product page Amazon shows you, so you decide everything. 👌",
        "close": f"Great — here's the link: {url}. Took me 10 seconds, takes you 30. Let me know what you think after it lands! 🙌",
        "review_request": f"Hey {{name}}! Hope the {keyword} pick is working out. If you get a sec, an honest star rating on Amazon helps the next person a ton — no hype, just your truth. Thanks either way! 🙏",
    }


def build_review_pipeline(keyword, items):
    """Ethical review funnel: when to ask + what to say + reply to bad reviews."""
    pick = pick_for_buyers(items)
    title = _clip(pick.get("title"), 60) if pick else keyword
    return {
        "when_to_ask": "3–7 days after delivery lands — buyers have formed an opinion but still remember the purchase.",
        "request_sms": f"Hey {{name}} — quick one: does {title[:50]} meet your expectations? If yes and you have 20 seconds, an honest rating on the Amazon page helps others big time (we never ask for anything but honesty). Link: view your order → 'Write a review'.",
        "request_email": f"""Subject: How's {title[:44]} treating you?

Hi {{first_name}},

Quick check-in — is the {keyword} pick working out?

If you loved it (or even if it didn't), a 15-second honest review on the Amazon listing helps real people decide. That's all we ever ask for — honesty.

Thanks for giving it a shot! 🙏

— {{your_name}}""",
        "reply_good": f"Thank you for the 5-star review of {title[:48]} — genuinely appreciate you taking the time to share your experience! 🙏",
        "reply_neutral": f"Thanks for the honest feedback on {title[:48]} — we hear you, and it's exactly this kind of detail that helps others. If you run into trouble with the product itself, Amazon's support (order page → contact seller) is fast and friendly.",
        "ask_happy": "Happy customer? Politely invite a 1-minute review with no incentive (Amazon forbids paid-for reviews).",
        "ask_unhappy": "Unhappy customer? Route them to Amazon's return/refund flow FIRST — never argue in public; keep replies short, warm, and solution-first.",
    }


def _boost_code(slug, name):
    """Stable 5-char UTM content code per (slug, campaign) — no random churn,
    so re-running a boost reuses the same tracked link and clicks aggregate."""
    h = hashlib.sha1(("%s:%s" % (slug, name)).encode("utf-8")).digest()
    n = int.from_bytes(h[:3], "big")
    core = "23456789abcdefghjkmnpqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, len(core))
        out = core[r] + out
    return out or "a"


def build_boost_campaigns(keyword, items, base_url="", slug="", keywords=()):
    """Ready-to-run promo angle templates for the pick. Each campaign carries a
    stable, UTM-tracked link back to the niche landing page (/lp/<slug>) so a
    "boost" is a real, measurable campaign — not a bare Amazon link. Long-tail
    keyword hints (SEM) get woven into the copy where it fits."""
    pick = pick_for_buyers(items)
    slug = slug or _slug(keyword)
    base = (base_url or "").rstrip("/")
    amazon_url = redirect_url(pick["asin"]) if pick else "#"
    title = _clip(pick.get("title"), 55) if pick else keyword
    disc = keyword.replace("-", " ")
    kit = [str(k).strip() for k in (keywords or ()) if str(k).strip()]
    long = kit[0] if kit else disc
    hints = ("  ·  sweep these search terms: " +
             ", ".join(kit[:3]) + ".") if kit else ""

    def tracked(name):
        code = _boost_code(slug, name)
        q = urllib.parse.urlencode({
            "utm_source": "boost", "utm_medium": "social",
            "utm_campaign": slug, "utm_content": code})
        link = "%s/lp/%s?%s" % (base, slug, q)
        return link, code

    def entry(name, script):
        link, code = tracked(name)
        return {"name": name, "id": _slug(name),
                "script": script, "link": link, "code": code,
                "qr": qr_url(pick["asin"]) if pick else "",
                "target": "landing"}

    pas_link, _ = tracked("Problem Agitate Solution")
    entries = [
        entry("Problem → Agitate → Solution (PAS)", f"""Awareness:
Pain of {disc} done wrong → the fix in one line. People keep searching “{long}”.

Agitate:
How much time/money you waste on a bad pick…

Solution:
This one's the most-reviewed ({'⭐ ' + str(pick['stars']) if pick and pick.get('stars') else ''}).
Full ranked guide (live prices): {pas_link}{hints}"""),
        entry("Social proof drop", f"""Just facts, zero hype:
• Most-reviewed option in the {disc} niche
• Sold & shipped by Amazon
• 30-second checkout
• Full ranked guide: {pas_link}"""),
        entry("Urgency / restock angle", f"""⏳ If you've been eyeing {title}, Amazon prices/stock move all the time.
Best-reviewed pick in the niche → don't lose it to a price change: {pas_link}"""),
        entry("Bundle stack", f"""This {disc} pick + the runner-ups in the same range = the whole starter kit.
Top pick: {amazon_url}
Buyers keep asking “{long}” — the full ranked guide answers it: {pas_link}"""),
        entry("Giveaway / engagement", f""""Fellow {disc} shopper: the top-rated pick is ranked from live data.
Top pick: {amazon_url}
Guides & honest picks: {pas_link}
Drop a comment 🍀 I'll DM the winner the link.""") if True else None,
    ]
    return [e for e in entries if e]


def build_funnel(keyword, items, site_url=None, affiliate_tag=None, boosts_kw=()):
    """The complete sales funnel payload for one niche shortlist."""
    pick = pick_for_buyers(items)
    landing = build_landing_page(keyword, items, site_url=site_url)
    return {
        "keyword": keyword,
        "count": len(items or []),
        "affiliate_tag": affiliate_tag or clean_tag(),
        "pick": pick,
        "landing_page": landing,
        "landing_url": "/lp/" + _slug(keyword),
        "email_sequence": build_email_sequence(keyword, items),
        "social": build_social_pack(keyword, items),
        "conversation": build_dm_conversation(keyword, items),
        "reviews": build_review_pipeline(keyword, items),
        "boosts": build_boost_campaigns(keyword, items,
                                        base_url=site_url or "",
                                        slug=_slug(keyword),
                                        keywords=boosts_kw),
    }


def qr_url(asin):
    """URL to hand to a QR-code tool (or poster) that leads straight to a tagged
    product page — drives in-person/offline buyers."""
    u = redirect_url(asin)
    return f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(u)}"


# ------------------------------------------------------------------ links
# Every outbound link is a DIRECT, affiliate-tagged amazon.com/dp/<ASIN>?tag=...
# URL. Amazon's program terms frown on cloaked/redirected links unless you have
# written permission, so /go/<ASIN> is kept only as a legacy resolver for links
# already published — new output always uses the full tagged URL.
def redirect_url(asin):
    return amazon.affiliate_url(asin)


def expand_go(asin):
    """Legacy resolver for the /go/<ASIN> endpoint (old links only)."""
    return amazon.affiliate_url(asin), amazon.MARKET


def status_blurb(scraper_cfg=None):
    return {
        "affiliate_tag": clean_tag(),
        "marketplace": amazon.MARKET,
        "tools": ["text-links", "markdown", "email-draft", "social-post",
                  "funnel (landing page /lp/<slug>, 5-email sequence, DM scripts, "
                  "review pipeline, boost campaigns)",
                  "direct affiliate links", "qr"],
    }

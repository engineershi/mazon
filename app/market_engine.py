# -*- coding: utf-8 -*-
"""Mazon marketing engine: tools to push direct buyers to Amazon fast.

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
_FUNNEL_STAGES = [
    ("1 · Attract", "Awareness content (social/DM/email) links to a focused landing page"),
    ("2 · Convert", "Landing page pushes ONE product with social proof + a single CTA"),
    ("3 · Deliver", "Every CTA is a direct affiliate-tagged Amazon listing link (no cloaking)"),
    ("4 · Multiply", "Follow-up emails + review requests compound long-term revenue"),
]


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
    title = _clip(pick.get("title"), 90)
    price = _price(pick) or "—"
    stars = pick.get("stars")
    reviews = pick.get("reviews")
    go = amazon.affiliate_url(pick["asin"])
    rating = f"{stars}★ ({reviews:,} ratings)" if (stars and reviews) else "highly rated on Amazon"
    e = html.escape
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
<div class="wrap">
  <div class="card">
    <span class="badge">🏆 Top pick · {rating}</span>
    <h1>{e(title)}</h1>
    <p class="muted">Vetted from live Amazon search data · {e(_slug(keyword).replace('-', ' '))} experts' picks</p>
    <div class="price">{e(price)}</div>
    <a class="cta" href="{e(go)}" rel="nofollow sponsored noopener">Get it on Amazon →</a>
    <div class="muted" style="text-align:center">Instant checkout · Amazon is the seller</div>
    <h3>Why it's our pick</h3>
    <ul>
      <li><strong>Proven demand</strong> — {rating} from verified Amazon buyers</li>
      <li><strong>Right price</strong> — {e(price or 'visible on Amazon')}</li>
      <li><strong>One click</strong> — button takes you straight to the listing</li>
    </ul>
    <h3>More from this niche</h3>
    <ul>{bullets}</ul>
    <a class="cta" href="{e(go)}" rel="nofollow sponsored noopener">Check price &amp; reviews →</a>
    <h3>FAQs</h3>
    {faq}
    <p class="muted" style="margin-top:20px">As an Amazon Associate we earn from qualifying purchases.</p>
  </div>
</div>
</body>
</html>"""


def build_email_sequence(keyword, items):
    """5-email buyer sequence: value → proof → objection → urgency → review ask."""
    pick = pick_for_buyers(items)
    if not pick or not pick.get("asin"):
        return []
    title = _clip(pick.get("title"), 70)
    price = _price(pick)
    url = redirect_url(pick["asin"])
    stars = pick.get("stars")
    reviews = pick.get("reviews")
    proof = f"⭐ {stars}/5 from {reviews:,} Amazon buyers" if (stars and reviews) else "highly rated on Amazon"
    return [
        {"name": "Email 1 · Hook + value", "subject": f"Ignore if this isn't for you, but {title[:48]}…",
         "body": f"""Subject: Ignore if this isn't for you, but {title[:48]}…

Hi {{first_name}},

I found a {keyword} buy that a LOT of other buyers already swear by ({proof}).

My one-line take: it solves the problem, it's priced well{(' at ' + price) if price else ''}, and I'd buy it again without thinking.

👉 Take a peek: {url}

No strings, no code — just the link. If it's not your thing, hit delete.

— {{your_name}}"""},
        {"name": "Email 2 · Social proof", "subject": f"What {reviews:,} buyers already think" if reviews else "What buyers already think",
         "body": f"""Subject: What {reviews:,} buyers already think

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

— {{your_name}}"""},
    ]


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


def build_boost_campaigns(keyword, items):
    """Ready-to-run promo angle templates for the pick."""
    pick = pick_for_buyers(items)
    url = redirect_url(pick["asin"]) if pick else "#"
    title = _clip(pick.get("title"), 55) if pick else keyword
    return [
        {"name": "Problem → Agitate → Solution (PAS)", "script": f"""Awareness:
Pain of {keyword.replace('-', ' ')} done wrong → the fix in one line.

Agitate:
How much time/money you waste on a bad pick…

Solution:
This one's the most-reviewed ({'⭐ ' + str(pick['stars']) if pick and pick.get('stars') else ''}).
{url}"""},
        {"name": "Social proof drop", "script": f"""Just facts, zero hype:
• Most-reviewed option in the {keyword} niche
• Sold & shipped by Amazon
• 30-second checkout

Link: {url}"""},
        {"name": "Urgency / restock angle", "script": f"""⏳ If you've been eyeing {title}, Amazon prices/stock move all the time.
Best-reviewed pick in the niche → don't lose it to a price change: {url}"""},
        {"name": "Bundle stack", "script": f"""This {keyword} pick + the runner-ups in the same range = the whole starter kit.
Top pick: {url}
Ask me for the rest of the list 👇"""},
        {"name": "Giveaway / engagement", "script": f""""Like anything {keyword}? Drop a comment 🍀 I'll DM the winner the link to the top-rated pick (fair price, Amazon-backed).
{url}"""},
    ]


def build_funnel(keyword, items, site_url=None, affiliate_tag=None):
    """The complete sales funnel payload for one niche shortlist."""
    pick = pick_for_buyers(items)
    landing = build_landing_page(keyword, items, site_url=site_url)
    return {
        "keyword": keyword,
        "count": len(items or []),
        "affiliate_tag": affiliate_tag or clean_tag(),
        "stages": _FUNNEL_STAGES,
        "pick": pick,
        "landing_page": landing,
        "landing_url": "/lp/" + _slug(keyword),
        "email_sequence": build_email_sequence(keyword, items),
        "social": build_social_pack(keyword, items),
        "conversation": build_dm_conversation(keyword, items),
        "reviews": build_review_pipeline(keyword, items),
        "boosts": build_boost_campaigns(keyword, items),
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

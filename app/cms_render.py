# -*- coding: utf-8 -*-
"""pstore CMS landing-page renderer.

Turns a CMS context (sections + style + settings) into a fully-styled,
persuasion-engineered HTML page. Follows Suby's "How to Sell Like Crazy"
(dream outcome + PAS copy + dense social proof + urgency + strong offer)
and Cialdini's "Influence" (reciprocity via PDF lead magnet, commitment via
email opt-in, social proof counters, authority via methodology, scarcity).

The renderer is intentionally self-contained and stdlib-only. All section
HTML is generated from stored data, so the admin CMS can re-style and
re-word the page without touching code.
"""
import html
import json
import time
import urllib.parse

import market_engine
import amazon as amazon_mod


def _esc(s):
    return html.escape(str(s or ""), quote=True)


def _style_css(style):
    """Build the <style> block for a landing page from the CMS style dict."""
    s = style or {}
    radius = s.get("border_radius", "22px")
    font = s.get("font_family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")
    accent = s.get("accent", "#ff6b2c")
    accent2 = s.get("accent2", "#7c5cff")
    text = s.get("text", "#2b2233")
    muted = s.get("muted", "#887b94")
    bg = s.get("bg", "#fff7ec")
    card_bg = s.get("card_bg", "#ffffff")
    cta_grad = s.get("cta_gradient", "linear-gradient(135deg, #ff6b2c, #ff873c)")
    cta_grad2 = "linear-gradient(135deg, %s, #9b7bff)" % accent2

    return f"""
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family:{font};
    color:{text}; background:{bg}; line-height:1.55; }}
  .wrap {{ max-width:720px; margin:0 auto; padding:36px 20px 60px; }}
  .card {{ background:{card_bg}; border:1px solid #f3e6d4; border-radius:{radius};
    padding:28px; box-shadow:0 14px 36px rgba(255,120,60,.10); }}
  h1 {{ font-size:28px; line-height:1.22; letter-spacing:-.4px; margin:10px 0; }}
  h2 {{ font-size:20px; margin:22px 0 12px; }}
  h3 {{ font-size:16px; margin:16px 0 8px; }}
  .badge {{ display:inline-block; background:#eee9ff; color:{accent2}; font-weight:700;
    font-size:12px; padding:6px 14px; border-radius:999px; }}
  .price {{ font-size:30px; font-weight:800; color:{accent}; letter-spacing:-.5px; }}
  .cta {{ display:block; text-align:center; background:{cta_grad};
    color:#fff; text-decoration:none; font-weight:800; font-size:18px; padding:16px;
    border-radius:999px; margin:18px 0 8px; box-shadow:0 10px 24px rgba(255,107,44,.35); }}
  .cta.inline {{ background:{cta_grad2}; font-size:15px; padding:14px; }}
  .muted {{ color:{muted}; font-size:13px; }}
  ul {{ padding-left:18px; }}
  li {{ margin:8px 0; }}
  .qa {{ margin-top:14px; }}
  .qa p {{ margin:4px 0 10px; color:{muted}; font-size:14px; }}
  /* social proof */
  .proofbar {{ display:flex; gap:12px; flex-wrap:wrap; justify-content:center;
    background:#fff; border:1px solid #f3e6d4; border-radius:{radius}; padding:18px;
    margin:18px 0; text-align:center; }}
  .proofstat {{ flex:1 1 120px; }}
  .proofstat .num {{ font-size:26px; font-weight:800; color:{accent}; }}
  .proofstat .lbl {{ color:{muted}; font-size:12px; }}
  .proofitems li {{ font-size:14px; }}
  /* benefits */
  .benefit {{ display:flex; gap:10px; margin:10px 0; align-items:flex-start; }}
  .benefit .tick {{ color:{accent}; font-weight:800; }}
  /* email gate */
  .gate {{ border:2px solid {accent}; background:linear-gradient(180deg,#fff9f2,#fff);
    border-radius:{radius}; padding:24px; margin:20px 0; }}
  .gate input {{ width:100%; padding:13px 16px; border:1.5px solid #f0e3d0;
    border-radius:13px; font-size:15px; margin:8px 0 10px; font-family:inherit; }}
  .gate button {{ width:100%; padding:15px; border:0; border-radius:999px; font-size:16px;
    font-weight:800; color:#fff; background:{cta_grad}; cursor:pointer; }}
  .gate .hint {{ font-size:12px; color:{muted}; text-align:center; margin-top:10px; }}
  .gate-msg {{ color:#159a4b; font-size:13px; min-height:18px; margin:8px 0 0; text-align:center; }}
  /* testimonials */
  .testi {{ border:1px solid #f3e6d4; border-radius:{radius}; padding:16px; margin:12px 0;
    background:#fffdf8; }}
  .testi .stars {{ color:#ffb800; letter-spacing:2px; }}
  .testi .auth {{ color:{muted}; font-size:12px; margin-top:6px; }}
  /* urgency */
  .urgent {{ background:linear-gradient(135deg,#fff3e6,#ffe9ff); border:1px solid #ffd9bf;
    border-radius:16px; padding:18px; margin:20px 0; text-align:center; }}
  .urgent .timer {{ font-size:30px; font-weight:800; color:{accent}; letter-spacing:1px; }}
  /* guarantee */
  .guarantee {{ background:#f7f3ff; border:1px solid #e6defa; border-radius:{radius};
    padding:18px; margin:20px 0; }}
  .guarantee .ico {{ font-size:34px; }}
  /* methodology / trust */
  .trustbox {{ background:#fbfbf6; border:1px solid var(--border, #f3e6d4);
    border-radius:{radius}; padding:16px 18px; margin:18px 0; }}
  /* comparison / product spotlight */
  .spot {{ border:1px solid #ffd9bf; border-radius:{radius}; background:linear-gradient(180deg,#fff8f1,#fff);
    padding:22px; margin:20px 0; box-shadow:0 14px 36px rgba(255,120,60,.10); }}
  .mt4 {{ margin-top:16px; }}
  details.faq {{ border:1px solid #f3e6d4; border-radius:14px; padding:12px 16px;
    margin:10px 0; background:#fffdf8; }}
  details.faq summary {{ cursor:pointer; font-weight:700; font-size:14.5px; }}
  details.faq p {{ margin:10px 0 2px; color:{muted}; font-size:14px; }}
  .center {{ text-align:center; }}
  .aff {{ font-size:11.5px; color:{muted}; text-align:center; margin-top:22px; }}
  """


def _section_html(section, ctx):
    """Render one section's HTML from its CMS data."""
    stype = section.get("_type", "")
    e = _esc
    pick = ctx.get("pick") or {}
    stars = pick.get("stars") if pick else None
    reviews = pick.get("reviews") if pick else None
    amazon_url = ctx.get("items") and _find_amazon_url(ctx) or ""
    asin = (pick or {}).get("asin", "")

    if stype == "hero":
        headline = section.get("headline", "The #1 pick for this niche")
        sub = section.get("subheadline", "")
        badge = section.get("badge_text", "")
        img = section.get("hero_image_url", "")
        badge_html = f'<span class="badge">{e(badge)}</span>' if badge else ""
        img_html = f'<img src="{e(img)}" style="max-width:100%;border-radius:16px;margin:14px 0">' if img else ""
        return f"""
  <div class="card" style="text-align:center">
    {badge_html}
    {img_html}
    <h1>{e(headline)}</h1>
    <p class="muted" style="font-size:16px">{e(sub)}</p>
  </div>"""

    if stype == "social_proof":
        count = section.get("proof_count", "")
        label = section.get("proof_label", "")
        items = section.get("proof_items", [])
        item_html = "".join(f"<li>{e(i)}</li>" for i in items)
        return f"""
  <div class="proofbar">
    <div class="proofstat"><div class="num">{e(count)}</div><div class="lbl">{e(label)}</div></div>
  </div>
  <div class="card" style="padding-top:8px">
    <ul class="proofitems">{item_html}</ul>
  </div>"""

    if stype == "benefits":
        title = section.get("title", "What you'll get")
        items = section.get("items", [])
        item_html = "".join(
            f'<div class="benefit"><span class="tick">✔</span><span>{e(i)}</span></div>'
            for i in items if isinstance(i, str))
        return f"""
  <div class="card">
    <h2>{e(title)}</h2>
    {item_html}
  </div>"""

    if stype == "product_spotlight":
        show_price = section.get("show_price", True)
        show_rating = section.get("show_rating", True)
        show_reviews = section.get("show_reviews", True)
        cta_text = section.get("cta_text", "Check price on Amazon →")
        if not pick or not asin:
            return "<div class='card muted'>No products for this niche yet.</div>"
        rating = ""
        if show_rating and stars:
            rating = f"⭐ {stars}"
        rcount = ""
        if show_reviews and reviews:
            rcount = f" · {int(reviews):,} reviews"
        proof = f"{rating}{rcount}".strip() or "highly rated on Amazon"
        price_html = f'<div class="price">{e(str(pick.get("price","") or ""))}</div>' if show_price and pick.get("price") else ""
        return f"""
  <div class="spot">
    <span class="badge">🏆 Top pick · {e(proof)}</span>
    <h1 style="font-size:24px">{e(pick.get('title','')[:90])}</h1>
    {price_html}
    <a class="cta" href="{e(amazon_url)}" rel="nofollow sponsored noopener"
       data-asin="{e(asin)}" data-beacon="landing-cta">{e(cta_text)}</a>
    <div class="muted center">Instant checkout · Amazon is the seller</div>
  </div>"""

    if stype == "email_gate":
        headline = section.get("headline", "Get the free guide")
        sub = section.get("subheadline", "")
        btn = section.get("button_text", "Send me the guide →")
        privacy = section.get("privacy_text", "")
        pdf_head = section.get("pdf_headline", "Your guide is ready!")
        pdf_sub = section.get("pdf_subheadline", "")
        pdf_gated = bool((ctx.get("settings") or {}).get("pdf_gated", True))
        kw = ctx.get("keyword", "")
        if not pdf_gated:
            # Direct (ungated) download — reciprocity without an email wall.
            return f"""
  <div class="gate" id="gate">
    <h2 style="text-align:center">{e(headline)}</h2>
    <p class="muted" style="text-align:center">{e(sub)}</p>
    <a class="cta" href="/_gated/pdf?keyword={e(urllib.parse.quote(kw))}"
       rel="noopener">⬇ {e(pdf_head)}</a>
    <p class="hint">{e(privacy)}</p>
  </div>"""
        return f"""
  <div class="gate" id="gate">
    <h2 style="text-align:center">{e(headline)}</h2>
    <p class="muted" style="text-align:center">{e(sub)}</p>
    <form class="courier gate-form">
      <input type="text" name="first_name" placeholder="First name" autocomplete="given-name">
      <input type="email" name="email" placeholder="you@email.com" required autocomplete="email">
      <input type="hidden" name="keyword" value="{e(kw)}">
      <input type="hidden" name="source" value="landing-gate">
      <button type="submit">{e(btn)}</button>
      <p class="gate-msg courier-msg" style="display:none"></p>
    </form>
    <a class="cta" id="gate-unlock" href="#" rel="noopener"
       style="display:none;margin-top:12px">⬇ {e(pdf_head)}</a>
    <p class="hint">{e(privacy)}</p>
  </div>"""

    if stype == "testimonials":
        title = section.get("title", "What shoppers say")
        items = section.get("items", [])
        cards = []
        for it in items:
            if isinstance(it, str):
                cards.append(f'<div class="testi"><p>{e(it)}</p></div>')
            elif isinstance(it, dict):
                stxt = it.get("text", "")
                auth = it.get("author", "Verified buyer")
                st = it.get("stars", 5)
                star_html = "★" * int(st)
                cards.append(f'<div class="testi"><div class="stars">{e(star_html)}</div>'
                             f'<p>{e(stxt)}</p>'
                             f'<div class="auth">— {e(auth)}</div></div>')
        return f"""
  <div class="card">
    <h2>{e(title)}</h2>
    {''.join(cards)}
  </div>"""

    if stype == "faq":
        title = section.get("title", "FAQ")
        items = section.get("items", [])
        cards = []
        for it in items:
            if isinstance(it, dict):
                cards.append(f'<details class="faq"><summary>{e(it.get("q",""))}</summary>'
                             f'<p>{e(it.get("a",""))}</p></details>')
        return f"""
  <div class="card">
    <h2>{e(title)}</h2>
    {''.join(cards)}
  </div>"""

    if stype == "urgency":
        head = section.get("headline", "Prices move daily")
        sub = section.get("subheadline", "")
        timer = section.get("timer_enabled", False)
        counter = section.get("counter_enabled", True)
        counter_label = section.get("counter_label", "people viewing this page")
        spots = int(section.get("spots_remaining", 0) or 0)
        timer_html = ""
        if timer:
            timer_html = ('<div class="timer" data-timer data-minutes="15">15:00</div>'
                          '<div class="muted">Offer timer — refreshes on each visit</div>')
        counter_html = ""
        if counter:
            import random
            n = random.randint(17, 48)
            counter_html = (f'<div class="muted" style="font-size:14px;margin-top:6px">'
                            f'<b style="color:{ctx.get("style",{}).get("accent","#ff6b2c")}">{n}</b> '
                            f'{e(counter_label)} right now</div>')
        spots_html = ""
        if spots:
            spots_html = (f'<div style="margin-top:8px;font-size:13px">'
                          f'⚠️ Only <b>{spots}</b> left at today\'s rate</div>')
        return f"""
  <div class="urgent">
    <h2 style="margin-top:0">{e(head)}</h2>
    <p class="muted">{e(sub)}</p>
    {timer_html}
    {counter_html}
    {spots_html}
  </div>"""

    if stype == "guarantee":
        head = section.get("headline", "Our promise")
        sub = section.get("subheadline", "")
        ico = section.get("icon_emoji", "🛡️")
        return f"""
  <div class="guarantee">
    <div class="ico">{e(ico)}</div>
    <h3>{e(head)}</h3>
    <p class="muted">{e(sub)}</p>
  </div>"""

    if stype == "methodology":
        title = section.get("title", "How we pick")
        body = section.get("body", "")
        return f"""
  <div class="trustbox">
    <h3>{e(title)}</h3>
    <p class="muted">{e(body)}</p>
  </div>"""

    if stype == "cta_band":
        head = section.get("headline", "Ready?")
        sub = section.get("subheadline", "")
        btn = section.get("button_text", "See top pick →")
        amazon_url = _find_amazon_url(ctx)
        asin = (pick or {}).get("asin", "")
        if not amazon_url:
            return ""
        return f"""
  <div class="card center">
    <h2>{e(head)}</h2>
    <p class="muted">{e(sub)}</p>
    <a class="cta" href="{e(amazon_url)}" rel="nofollow sponsored noopener"
       data-asin="{e(asin)}" data-beacon="landing-cta-band">{e(btn)}</a>
  </div>"""

    return ""


def _find_amazon_url(ctx):
    pick = ctx.get("pick") or {}
    asin = pick.get("asin")
    if not asin:
        return ""
    return amazon_mod.affiliate_url(asin)


def render_landing_page_page(context, keyword, site_url=None):
    """Render the full HTML for a CMS-driven landing page."""
    sections = context.get("sections", [])
    style = context.get("style", {})
    settings = context.get("settings", {})
    e = _esc
    for s in sections:
        s["_ctx"] = context
    section_html = "".join(_section_html(s, context) for s in sections)
    css = _style_css(style)
    slug = context.get("slug", "niche")
    base = (site_url or "").rstrip("/")
    og_image = (base + "/og/" + slug) if base else ""
    keyword_title = keyword.replace("-", " ").title()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(keyword_title)} — Best Picks</title>
<meta property="og:title" content="{e(keyword_title)}">
<meta property="og:description" content="The ranked best {e(keyword)} pick from live Amazon data — see why it wins, its price, and buy it in one click.">
<meta property="og:type" content="website">
<meta property="og:url" content="{e(base)}/lp/{e(slug)}">
<meta property="og:image" content="{e(og_image)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(keyword_title)}">
<meta name="twitter:description" content="The ranked best {e(keyword)} pick from live Amazon data.">
<style>{css}</style>
</head>
<body>
<main data-niche="{e(slug)}" data-source="landing">
<div class="wrap">
{section_html}
<p class="aff">As an Amazon Associate we earn from qualifying purchases.</p>
</div>
</main>
<script src="/courier.js" defer></script>
<script>
(function () {{
  "use strict";
  /* Live countdown for the urgency timer section. */
  var timer = document.querySelector("[data-timer]");
  if (timer) {{
    var minutes = parseInt(timer.getAttribute("data-minutes") || "15", 10);
    var end = Date.now() + minutes * 60000;
    var tick = setInterval(function () {{
      var left = Math.max(0, end - Date.now());
      var m = Math.floor(left / 60000);
      var s = Math.floor((left % 60000) / 1000);
      timer.textContent = (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s);
      if (left <= 0) {{ clearInterval(tick); timer.textContent = "00:00"; }}
    }}, 1000);
  }}
}})();
</script>
</body>
</html>"""


# Keep a compatibility reference so the existing callers that import
# build_landing_page can still work, but route through CMS when available.
def build_cms_landing_page(keyword, items, site_url=None, subscriber_count=0,
                           conn=None, freshness="today"):
    """Build a CMS-driven landing page when a DB connection is provided, else
    fall back to the legacy template."""
    import cms
    if conn is None:
        from market_engine import build_landing_page
        return build_landing_page(keyword, items, site_url=site_url)
    niche_data = {
        "products": items,
        "subscriber_count": subscriber_count,
        "freshness": freshness,
    }
    ctx = cms.build_page_context(conn, keyword, niche_data)
    return render_landing_page_page(ctx, keyword, site_url=site_url)


# -*- coding: utf-8 -*-
"""pstore CMS landing-page renderer.

Turns a CMS context (sections + style + settings) into a fully-styled,
persuasion-engineered, professional HTML page. Follows Suby's "How to Sell
Like Crazy" (dream outcome + PAS copy + dense social proof + urgency + strong
offer) and Cialdini's "Influence" (reciprocity via PDF lead magnet, commitment
via email opt-in, social proof counters, authority via methodology, scarcity).

The whole look is driven by the page's style dict (CSS custom properties), so
the one-click presets (light/dark modes) fully re-skin the page without code.
Every chrome element — promo banner, countdown boxes, sticky floating CTA,
reveal-on-scroll — is controlled from page settings toggles.
"""
import html
import json
import urllib.parse

import market_engine
import amazon as amazon_mod


def _esc(s):
    return html.escape(str(s or ""), quote=True)


def _style_css(style):
    """Build the <style> block for a landing page from the CMS style dict.

    Colors/layout are emitted once as CSS custom properties, then every rule
    references them — so a preset (or manual color edit) re-skins everything.
    """
    s = style or {}
    dark = (s.get("mode") or "light") == "dark"
    radius = s.get("border_radius", "22px")
    font = s.get("font_family",
                 "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")
    accent = s.get("accent", "#ff6b2c")
    accent2 = s.get("accent2", "#7c5cff")
    text = s.get("text", "#2b2233")
    muted = s.get("muted", "#887b94")
    bg = s.get("bg", "#fff7ec")
    card_bg = s.get("card_bg", "#ffffff")
    cta_grad = s.get("cta_gradient", "linear-gradient(135deg, #ff6b2c, #ff873c)")
    cta_grad2 = "linear-gradient(135deg, %s, %s)" % (accent2, accent)
    hero_style = s.get("hero_style", "gradient")

    if dark:
        line = "rgba(255,255,255,.12)"
        soft = "rgba(255,255,255,.06)"
        soft2 = "rgba(255,255,255,.03)"
        shadow = "0 18px 44px rgba(0,0,0,.45)"
        hero_band = "linear-gradient(135deg, rgba(245,185,66,.16), rgba(124,92,255,.14))"
        badge_bg = "rgba(124,92,255,.22)"
        inputs_bg = "#1d2740"
        inputs_bd = "rgba(255,255,255,.18)"
    else:
        line = "rgba(20,12,40,.10)"
        soft = "#ffffff"
        soft2 = "#fffdf8"
        shadow = "0 18px 44px rgba(255,120,60,.14)"
        hero_band = "linear-gradient(135deg, %s, %s)" % (accent, accent2)
        badge_bg = "#eee9ff"
        inputs_bg = "#ffffff"
        inputs_bd = "rgba(20,12,40,.16)"

    wrap = "880px"
    if s.get("layout") == "wide":
        wrap = "1000px"
    elif s.get("layout") == "split":
        wrap = "940px"

    return f"""
  * {{ box-sizing: border-box; }}
  :root {{
    --bg:{bg}; --card:{card_bg}; --text:{text}; --muted:{muted};
    --accent:{accent}; --accent2:{accent2}; --cta:{cta_grad}; --cta2:{cta_grad2};
    --radius:{radius}; --font:{font}; --line:{line}; --soft:{soft};
    --soft2:{soft2}; --shadow:{shadow}; --hero-band:{hero_band};
    --badge-bg:{badge_bg}; --inputs-bg:{inputs_bg}; --inputs-bd:{inputs_bd};
  }}
  body {{ margin:0; font-family:var(--font); color:var(--text); background:var(--bg);
    line-height:1.6; -webkit-font-smoothing:antialiased; }}
  .wrap {{ max-width:{wrap}; margin:0 auto; padding:8px 20px 96px; }}
  h1 {{ font-size:clamp(26px,4.6vw,42px); line-height:1.14; letter-spacing:-.8px; margin:12px 0 10px; font-weight:800; }}
  h2 {{ font-size:clamp(19px,3vw,25px); margin:0 0 12px; letter-spacing:-.4px; font-weight:800; }}
  h3 {{ font-size:16px; margin:14px 0 8px; }}
  p {{ margin:8px 0; }}
  img {{ max-width:100%; }}
  a {{ color:inherit; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
    padding:clamp(20px,3.4vw,34px); box-shadow:var(--shadow); margin:18px 0 0; }}
  .badge {{ display:inline-block; background:var(--badge-bg); color:var(--accent2);
    font-weight:800; font-size:12px; padding:6px 14px; border-radius:999px; letter-spacing:.2px; }}
  .muted {{ color:var(--muted); font-size:14px; }}
  .price {{ font-size:clamp(30px,5vw,40px); font-weight:900; color:var(--accent); letter-spacing:-1px; line-height:1; }}
  .price .small {{ font-size:.5em; font-weight:700; color:var(--muted); }}
  .cta {{ display:inline-block; text-align:center; background:var(--cta); color:#fff; text-decoration:none;
    font-weight:800; font-size:17px; padding:16px 30px; border-radius:999px; margin:16px 0 6px;
    box-shadow:0 12px 28px rgba(0,0,0,.18); border:0; cursor:pointer; transition:transform .15s ease, box-shadow .15s ease; }}
  a.cta {{ display:inline-block; }}
  .cta:hover {{ transform:translateY(-2px); box-shadow:0 16px 36px rgba(0,0,0,.24); }}
  .cta:nth-of-type(2), .cta.alt {{ background:var(--cta2); }}
  .center {{ text-align:center; }}
  /* promo strip */
  .promo {{ position:relative; z-index:30; background:var(--cta); color:#fff; text-align:center;
    font-size:13.5px; font-weight:700; padding:9px 14px; letter-spacing:.2px; }}
  .promo b {{ text-transform:uppercase; margin-left:8px; background:rgba(255,255,255,.18);
    padding:2px 9px; border-radius:999px; font-size:12px; }}
  /* discreet short link back to the public review page */
  .full-review {{ color:var(--muted); font-size:12.5px; font-weight:600; text-decoration:none;
    border:1px solid var(--line); padding:5px 10px; border-radius:999px; }}
  .full-review:hover {{ border-color:var(--accent); color:var(--accent); }}
  /* countdown boxes */
  .cd-wrap {{ display:flex; gap:10px; justify-content:center; margin:16px 0 0; }}
  .cd-box {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
    min-width:64px; padding:10px 8px; text-align:center; box-shadow:var(--shadow); }}
  .cd-box .num {{ font-size:clamp(26px,4vh,36px); font-weight:900; color:var(--accent); line-height:1; }}
  .cd-box .unit {{ font-size:10.5px; text-transform:uppercase; letter-spacing:1.5px; color:var(--muted); margin-top:4px; }}
  .cd-done {{ display:none; }}
  /* sticky floating CTA */
  .sticky-cta {{ position:fixed; left:0; right:0; bottom:0; z-index:40; padding:12px 16px 16px;
    background:linear-gradient(180deg, transparent, var(--bg) 40%);
    display:none; justify-content:center; }}
  body.show-sticky .sticky-cta {{ display:flex; }}
  .sticky-cta .cta {{ margin:0; width:100%; max-width:520px; font-size:16px; padding:15px 22px; }}
  /* reveal-on-scroll */
  .reveal {{ opacity:0; transform:translateY(14px); transition:opacity .5s ease, transform .5s ease; }}
  .reveal.in {{ opacity:1; transform:none; }}
  /* hero */
  .hero {{ position:relative; overflow:hidden; }}
  .hero.gradient {{ background:var(--hero-band); border:1px solid transparent; }}
  .hero.bold h1 {{ font-size:clamp(34px,6vw,56px); letter-spacing:-1.4px; }}
  .hero h1 .hi {{ color:var(--accent); }}
  .hero.minimal {{ background:var(--card); }}
  .hero.minimal h1 {{ text-align:left; }}
  .hero .stars-line {{ font-size:13px; color:var(--muted); margin-top:6px; }}
  .hero .stars-line b {{ color:#ffab00; letter-spacing:2px; }}
  /* social proof */
  .proofbar {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:12px;
    background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:20px;
    margin:20px 0 0; text-align:center; box-shadow:var(--shadow); }}
  .proofstat .num {{ font-size:clamp(24px,4vw,34px); font-weight:900; color:var(--accent); }}
  .proofstat .lbl {{ color:var(--muted); font-size:12.5px; }}
  .proofitems {{ list-style:none; padding:0; margin:6px 0 0; display:grid; gap:8px; }}
  .proofitems li {{ display:flex; gap:8px; align-items:flex-start; font-size:14px; }}
  .proofitems li:before {{ content:"✓"; color:var(--accent); font-weight:900; }}
  /* benefits */
  .benefit {{ display:flex; gap:12px; margin:12px 0; align-items:flex-start; }}
  .benefit .ico {{ font-size:22px; flex-shrink:0; width:36px; height:36px; border-radius:11px;
    background:var(--badge-bg); display:grid; place-items:center; }}
  .benefit .b-text b {{ display:block; font-size:15.5px; }}
  /* product spotlight */
  .spot {{ border:1.5px solid var(--line); border-radius:var(--radius); background:var(--card);
    padding:clamp(22px,3.6vw,36px); margin:22px 0 0; box-shadow:var(--shadow); position:relative; overflow:hidden; }}
  .spot .ribbon {{ position:absolute; top:14px; right:-38px; transform:rotate(38deg);
    background:var(--accent); color:#fff; font-size:11px; font-weight:800; padding:5px 44px;
    letter-spacing:1px; box-shadow:0 6px 16px rgba(0,0,0,.2); }}
  .spot .meta {{ display:flex; flex-wrap:wrap; gap:6px 14px; color:var(--muted); font-size:13.5px; margin:8px 0 4px; }}
  .spot .stars {{ color:#ffab00; letter-spacing:2px; font-size:15px; }}
  .spot .under-cta {{ font-size:12.5px; color:var(--muted); margin-top:4px; }}
  /* email gate */
  .gate {{ border:1.5px solid var(--accent); background:var(--card); border-radius:var(--radius);
    padding:clamp(22px,3.6vw,34px); margin:22px 0 0; text-align:center; box-shadow:var(--shadow); }}
  .gate .gift {{ font-size:44px; line-height:1; margin-bottom:4px; }}
  .gate input {{ width:100%; padding:14px 16px; border:1.5px solid var(--inputs-bd);
    border-radius:13px; font-size:15px; margin:8px 0 10px; font-family:inherit; background:var(--inputs-bg); color:var(--text); }}
  .gate button {{ width:100%; padding:16px; border:0; border-radius:999px; font-size:16px;
    font-weight:800; color:#fff; background:var(--cta); cursor:pointer; transition:filter .15s ease; }}
  .gate button:hover {{ filter:brightness(1.07); }}
  .gate .hint {{ font-size:12px; color:var(--muted); text-align:center; margin-top:10px; }}
  .gate-msg {{ color:#159a4b; font-size:13px; min-height:18px; margin:8px 0 0; text-align:center; }}
  /* testimonials */
  .testi {{ border:1px solid var(--line); border-radius:var(--radius); padding:16px 18px; margin:12px 0 0;
    background:var(--soft2); }}
  .testi .top {{ display:flex; align-items:center; gap:10px; margin-bottom:8px; }}
  .testi .ava {{ width:34px; height:34px; border-radius:50%; background:var(--cta); color:#fff;
    display:grid; place-items:center; font-weight:800; font-size:14px; }}
  .testi .stars {{ color:#ffab00; letter-spacing:2px; font-size:13px; }}
  .testi p {{ margin:4px 0; font-size:14.5px; }}
  .testi .auth {{ color:var(--muted); font-size:12px; margin-top:6px; }}
  /* urgency */
  .urgent {{ background:var(--soft); border:1px solid var(--line); border-radius:16px;
    padding:20px; margin:22px 0 0; text-align:center; }}
  .urgent .timer {{ font-size:clamp(30px,5vw,40px); font-weight:900; color:var(--accent); letter-spacing:1px; font-variant-numeric:tabular-nums; }}
  /* horizontal rules + spacing */
  .hr {{ border:0; border-top:1px solid var(--line); margin:22px 0; }}
  /* guarantee / trust / methodology */
  .guarantee, .trustbox {{ background:var(--soft); border:1px solid var(--line); border-radius:var(--radius);
    padding:22px; margin:22px 0 0; }}
  .guarantee .ico {{ font-size:38px; line-height:1; }}
  /* FAQ */
  details.faq {{ border:1px solid var(--line); border-radius:14px; padding:14px 18px;
    margin:10px 0 0; background:var(--card); }}
  details.faq summary {{ cursor:pointer; font-weight:700; font-size:15px; list-style:none; display:flex; justify-content:space-between; gap:10px; }}
  details.faq summary:after {{ content:"+"; color:var(--accent); font-weight:900; }}
  details.faq[open] summary:after {{ content:"−"; }}
  details.faq p {{ margin:10px 0 2px; color:var(--muted); font-size:14.5px; }}
  /* nav + bottom */
  .topbar {{ display:flex; align-items:center; justify-content:space-between; gap:12px;
    padding:14px 20px; font-size:13px; color:var(--muted); }}
  .topbar .brand b {{ color:var(--text); font-size:16px; letter-spacing:-.3px; }}
  .topbar .trust2 {{ display:flex; gap:14px; }}
  .aff {{ font-size:11.5px; color:var(--muted); text-align:center; margin-top:34px; }}
  .foot-legal {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
    padding:18px; margin:18px 0 0; font-size:12px; color:var(--muted); text-align:center; }}
  @media (max-width:560px) {{
    .topbar .trust2 {{ display:none; }}
    .cd-box {{ min-width:56px; }}
  }}
  """


def _global_settings_html(ctx, style):
    """Promo banner + global countdown boxes, driven by page settings toggles."""
    settings = ctx.get("settings") or {}
    parts = []
    promo = settings.get("promo") or {}
    if settings.get("promo_enabled") and (promo.get("text") or ""):
        code = promo.get("code") or ""
        code_html = f"<b>Code {_esc(code)}</b>" if code else ""
        parts.append(
            f'<div class="promo" data-niche="{_esc(ctx.get("slug",""))}" data-ev="promo">'
            f'⚡ {_esc(promo.get("text",""))}{code_html}</div>')
    if settings.get("countdown_enabled"):
        minutes = int(settings.get("countdown_minutes") or 30)
        headline = settings.get("countdown_headline", "")
        done = settings.get("countdown_done", "")
        head = f'<p style="text-align:center;font-weight:800;margin:18px 0 0">{_esc(headline)}</p>' if headline else ""
        parts.append(f"""
<div class="countdown" data-countdown="{minutes}" data-done-selector="#cd-done" data-ev="countdown">
  {head}
  <div class="cd-wrap">
    <div class="cd-box"><div class="num" data-cd="h">00</div><div class="unit">hrs</div></div>
    <div class="cd-box"><div class="num" data-cd="m">00</div><div class="unit">min</div></div>
    <div class="cd-box"><div class="num" data-cd="s">00</div><div class="unit">sec</div></div>
  </div>
  <p id="cd-done" class="cd-done" style="text-align:center;color:var(--muted);font-size:14px">{_esc(done)}</p>
</div>""")
    return "\n".join(parts)


def _sticky_cta_html(ctx):
    """Floating bottom CTA that appears after the hero is scrolled past."""
    settings = ctx.get("settings") or {}
    if not settings.get("sticky_cta", True):
        return ""
    pick = ctx.get("pick") or {}
    asin = pick.get("asin", "")
    url = _find_amazon_url(ctx)
    if not url:
        return ""
    keyword = ctx.get("keyword", "")
    return f"""
<div class="sticky-cta">
  <a class="cta" href="{_esc(url)}" rel="nofollow sponsored noopener"
     data-asin="{_esc(asin)}" data-beacon="landing-sticky" data-ev="sticky_cta">See today's {_esc(keyword[:40])} pick →</a>
</div>"""


def _section_html(section, ctx):
    """Render one section's HTML from its CMS data."""
    stype = section.get("_type", "")
    e = _esc
    style = ctx.get("style") or {}
    hero_style = style.get("hero_style", "gradient")
    pick = ctx.get("pick") or {}
    stars = pick.get("stars") if pick else None
    reviews = pick.get("reviews") if pick else None
    amazon_url = (ctx.get("items") and _find_amazon_url(ctx)) or ""
    asin = (pick or {}).get("asin", "")

    if stype == "hero":
        headline = section.get("headline", "The #1 pick for this niche")
        sub = section.get("subheadline", "")
        badge = section.get("badge_text", "")
        img = section.get("hero_image_url", "")
        badge_html = f'<span class="badge">{e(badge)}</span>' if badge else ""
        img_html = f'<img src="{e(img)}" alt="{e(headline[:60])}" style="max-width:420px;border-radius:16px;margin:16px 0 4px">' if img else ""
        stars_line = ""
        if pick and stars:
            star_str = "★" * min(5, int(stars))
            stars_line = (f'<div class="stars-line"><b>{e(star_str)}</b> '
                          f'{e(str(stars))} · {e("{:,}".format(int(reviews))) if reviews else ""} '
                          f'reviews analysed from live Amazon data</div>')
        cls = "hero " + (hero_style if hero_style in ("gradient", "minimal", "bold") else "gradient")
        txt_align = "center"
        if hero_style == "minimal":
            txt_align = "left"
        return f"""
  <div class="card {cls}" style="text-align:{txt_align}">
    {badge_html}
    <h1>{e(headline)}</h1>
    <p class="muted" style="font-size:clamp(15px,2.4vw,18px);max-width:640px;{'margin-left:auto;margin-right:auto' if txt_align=='center' else ''}">{e(sub)}</p>
    {stars_line}
    {img_html}
  </div>"""

    if stype == "social_proof":
        count = section.get("proof_count", "")
        label = section.get("proof_label", "")
        items = section.get("proof_items", [])
        item_html = "".join(f"<li>{e(i)}</li>" for i in items)
        return f"""
  <div class="proofbar">
    <div class="proofstat"><div class="num">{e(count)}</div><div class="lbl">{e(label)}</div></div>
    {item_html}
  </div>"""

    if stype == "benefits":
        title = section.get("title", "What you'll get")
        items = section.get("items", [])
        icons = ["🎯", "💰", "🏆", "🧠", "⏱️", "✅"]
        rows = []
        for i, it in enumerate(items):
            if not isinstance(it, str):
                continue
            ico = icons[i % len(icons)]
            rows.append(f'<div class="benefit"><span class="ico">{ico}</span>'
                        f'<span class="b-text"><b>{e(it)}</b></span></div>')
        return f"""
  <div class="card">
    <h2>{e(title)}</h2>
    {''.join(rows)}
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
            star_str = "★" * min(5, int(stars))
            rating = f'<span class="stars">{e(star_str)}</span> <b>{e(str(stars))}</b>'
        rcount = ""
        if show_reviews and reviews:
            rcount = f'<span>{e("{:,}".format(int(reviews)))} reviews</span>'
        price_html = ""
        if show_price and pick.get("price"):
            price_html = f'<div class="price">{e(str(pick["price"]))}</div>'
        return f"""
  <div class="spot reveal">
    <span class="ribbon">TOP PICK</span>
    <span class="badge">🏆 Independently ranked from live data</span>
    <h2 style="font-size:clamp(19px,3vw,26px);margin:12px 0 6px">{e(pick.get('title','')[:100])}</h2>
    <div class="meta">{rating} {rcount}</div>
    {price_html}
    <a class="cta" href="{e(amazon_url)}" rel="nofollow sponsored noopener"
       data-asin="{e(asin)}" data-beacon="landing-cta">{e(cta_text)}</a>
    <div class="under-cta">✔ Instant checkout · Amazon is the seller · returns covered by Amazon</div>
  </div>"""

    if stype == "email_gate":
        headline = section.get("headline", "Get the free guide")
        sub = section.get("subheadline", "")
        btn = section.get("button_text", "Send me the guide →")
        privacy = section.get("privacy_text", "")
        pdf_head = section.get("pdf_headline", "Your guide is ready!")
        pdf_sub = section.get("pdf_subheadline", "")
        pdf_gated = bool((ctx.get("settings") or {}).get("pdf_gated", True))
        gate_on = bool((ctx.get("settings") or {}).get("email_gate_enabled", True))
        kw = ctx.get("keyword", "")
        sub_html = f'<p class="muted">{e(sub)}</p>' if sub else ""
        pdf_sub_html = f'<p class="muted" style="margin-top:6px">{e(pdf_sub)}</p>' if pdf_sub else ""
        if pdf_gated and gate_on:
            return f"""
  <div class="gate reveal" id="gate">
    <div class="gift">🎁</div>
    <h2>{e(headline)}</h2>
    {sub_html}
    <form class="courier gate-form" style="max-width:430px;margin:14px auto 0">
      <input type="text" name="first_name" placeholder="First name" autocomplete="given-name">
      <input type="email" name="email" placeholder="you@email.com" required autocomplete="email">
      <input type="hidden" name="keyword" value="{e(kw)}">
      <input type="hidden" name="source" value="landing-gate">
      <button type="submit">{e(btn)}</button>
      <p class="gate-msg courier-msg" style="display:none"></p>
    </form>
    <a class="cta" id="gate-unlock" href="#" rel="noopener"
       data-ev="gate_unlock"
       style="display:none;margin-top:14px">⬇ {e(pdf_head)}</a>
    {pdf_sub_html}
    <p class="hint">{e(privacy)}</p>
  </div>"""
        # Ungated (or gate section off) — direct download, reciprocity without a wall.
        return f"""
  <div class="gate reveal" id="gate">
    <div class="gift">🎁</div>
    <h2>{e(headline)}</h2>
    {sub_html}
    <a class="cta" href="/_gated/pdf?keyword={e(urllib.parse.quote(kw))}"
       data-ev="lead_pdf"
       rel="noopener">⬇ {e(pdf_head)}</a>
    {pdf_sub_html}
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
                ini = (auth or "?")[0].upper()
                cards.append(
                    f'<div class="testi"><div class="top"><span class="ava">{e(ini)}</span>'
                    f'<div><div class="stars">{e(star_html)}</div>'
                    f'<div class="auth">{e(auth)}</div></div></div><p>{e(stxt)}</p></div>')
        return f"""
  <div class="card reveal">
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
  <div class="card reveal">
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
            timer_html = ('<div class="timer" data-sec-timer data-minutes="15">15:00</div>'
                          '<div class="muted">This page’s price snapshot refreshes in — prices move daily</div>')
        counter_html = ""
        if counter:
            import random
            n = random.randint(17, 48)
            counter_html = (f'<div class="muted" style="font-size:14px;margin-top:6px">'
                            f'<b style="color:{style.get("accent","#ff6b2c")}">{n}</b> '
                            f'{e(counter_label)} right now</div>')
        spots_html = ""
        if spots:
            spots_html = (f'<div style="margin-top:8px;font-size:13px;color:{style.get("accent","#ff6b2c")}">'
                          f'⚠️ Only <b>{spots}</b> left at today’s rate</div>')
        return f"""
  <div class="urgent reveal">
    <h2 style="margin-top:0;font-size:clamp(18px,3vw,23px)">{e(head)}</h2>
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
  <div class="guarantee reveal">
    <div class="ico">{e(ico)}</div>
    <h3>{e(head)}</h3>
    <p class="muted">{e(sub)}</p>
  </div>"""

    if stype == "methodology":
        title = section.get("title", "How we pick")
        body = section.get("body", "")
        return f"""
  <div class="trustbox reveal">
    <h3>{e(title)}</h3>
    <p class="muted">{e(body)}</p>
  </div>"""

    if stype == "cta_band":
        head = section.get("headline", "Ready?")
        sub = section.get("subheadline", "")
        btn = section.get("button_text", "See top pick →")
        if not amazon_url:
            # No live Amazon pick yet — still render the card (with the niche's
            # review page as the target) so an enabled section never vanishes.
            fallback = "/n/" + (ctx.get("slug") or "")
            return f"""
  <div class="card center reveal">
    <h2>{e(head)}</h2>
    <p class="muted">{e(sub)}</p>
    <a class="cta" href="{e(fallback)}" rel="noopener">{e(btn)}</a>
  </div>"""
        return f"""
  <div class="card center reveal">
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
    chrome = _global_settings_html(context, style)
    sticky = _sticky_cta_html(context)
    css = _style_css(style)
    slug = context.get("slug", "niche")
    base = (site_url or "").rstrip("/")
    og_image = (base + "/og/" + slug) if base else ""
    keyword_title = (keyword or slug).replace("-", " ").title()
    anim = bool(settings.get("animation", True))
    pick = context.get("pick") or {}
    top_pick_txt = (pick.get("title") or "").strip()[:60]
    top_url = _find_amazon_url(context)
    top_link = ""
    if top_url:
        top_link = f'<a href="{e(top_url)}" rel="nofollow sponsored noopener" style="color:var(--muted);font-weight:700;text-decoration:none">See the top pick →</a>'
    reveal_js = ""
    if anim:
        reveal_js = """
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
  }, { threshold: 0.08 });
  document.querySelectorAll(".reveal").forEach(function (el, i) {
    el.style.transitionDelay = (i % 3) * 60 + "ms";
    io.observe(el);
  });"""
    sticky_js = ""
    if sticky:
        sticky_js = """  /* sticky floating CTA after the hero is gone */
  var bar = document.querySelector(".sticky-cta");
  if (bar) {
    var hero = document.querySelector("main .hero") || document.querySelector(".wrap");
    var threshold = hero ? hero.offsetTop + hero.offsetHeight : 360;
    function onScroll() { document.body.classList.toggle("show-sticky", window.scrollY > threshold); }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(keyword_title)} — The Data-Backed #1 Pick</title>
<meta property="og:title" content="{e(keyword_title)} — The Data-Backed #1 Pick">
<meta property="og:description" content="The ranked best {e(keyword)} pick from live Amazon data — see why it wins, its price, and buy it in one click.">
<meta property="og:type" content="website">
<meta property="og:url" content="{e(base)}/lp/{e(slug)}">
<link rel="canonical" href="{e(base)}/lp/{e(slug)}">
<meta property="og:image" content="{e(og_image)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(keyword_title)} — The Data-Backed #1 Pick">
<meta name="twitter:description" content="The ranked best {e(keyword)} pick from live Amazon data.">
<style>{css}</style>
</head>
<body>
<main data-niche="{e(slug)}" data-source="landing" data-keyword="{e(keyword)}">
<div class="topbar">
  <div class="brand"><b>✓ {e(keyword_title[:28])} Guide</b></div>
  <div class="trust2"><span>🛒 Live Amazon prices</span><span>⭐ Real buyer ratings</span>{top_link} <a class="full-review" href="/n/{e(slug)}" rel="noopener">Full review ↗</a></div>
</div>
{chrome}
<div class="wrap">
{section_html}
<div class="card center" style="margin-top:26px">
  <div class="price small" style="font-size:15px">{e(pick.get('title','')[:70])}</div>
  {top_link or '<span class="muted">—</span>'}
</div>
<hr class="hr">
<div class="foot-legal">pstore researches live Amazon listings — rating, review volume and price — so every pick is data-backed, not guesswork.
We may earn a small commission when you buy through our links (the price you pay never changes).</div>
<p class="aff">As an Amazon Associate we earn from qualifying purchases.</p>
</div>
{sticky}
</main>
<script src="/courier.js" defer></script>
<script>
(function () {{
  "use strict";
  /* Global + section countdown timers. Each [data-countdown] gets its own
     end time; the global one shows HH:MM:SS boxes and flips to a done message. */
  function pad(n) {{ return n < 10 ? "0" + n : "" + n; }}
  var globals = document.querySelectorAll("[data-countdown]");
  globals.forEach(function (box) {{
    var minutes = parseInt(box.getAttribute("data-countdown") || "30", 10);
    var end = Date.now() + minutes * 60000;
    var doneSel = box.getAttribute("data-done-selector") || "";
    var h = box.querySelector("[data-cd=h]");
    var m = box.querySelector("[data-cd=m]");
    var sec = box.querySelector("[data-cd=s]");
    var tick = setInterval(function () {{
      var left = Math.max(0, end - Date.now());
      var hh = Math.floor(left / 3600000);
      var mm = Math.floor((left % 3600000) / 60000);
      var ss = Math.floor((left % 60000) / 1000);
      if (h) h.textContent = pad(hh);
      if (m) m.textContent = pad(mm);
      if (sec) sec.textContent = pad(ss);
      if (left <= 0) {{
        clearInterval(tick);
        if (doneSel) {{ var done = document.querySelector(doneSel); if (done) done.style.display = "block"; }}
      }}
    }}, 1000);
  }});
  var secTimer = document.querySelector("[data-sec-timer]");
  if (secTimer) {{
    var mins = parseInt(secTimer.getAttribute("data-minutes") || "15", 10);
    var end2 = Date.now() + mins * 60000;
    setInterval(function () {{
      var left = Math.max(0, end2 - Date.now());
      var mm = Math.floor(left / 60000), ss = Math.floor((left % 60000) / 1000);
      secTimer.textContent = pad(mm) + ":" + pad(ss);
    }}, 1000);
  }}
  {reveal_js}
  {sticky_js}
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
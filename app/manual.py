# -*- coding: utf-8 -*-
"""pstore user manual: the visual + printable guide to running the app at its
highest form. Two outputs from one source of truth:

  * render_admin_manual() -> the in-app admin page (/admin/manual), fully
    cross-linked to every tool, page and feature on the site.
  * build_pdf()           -> a styled, printable PDF companion that drops the
    links (a document can't click) but keeps every step, tip and checklist.

Pure stdlib: HTML for the admin page, pdfgen for the PDF.
"""

import html as _html

import pdfgen

# The accent + style tag used by the PDF cover so it matches the pstore brand.
_ACCENT = (255, 107, 44)
_BG = (255, 253, 247)


def _esc(s):
    return _html.escape(s, quote=True)


# --------------------------------------------------------------------------
# PDF build — a clean, printable companion to the on-page manual.
# pdfgen runs in Latin-1, so we stick to plain prose + bullets (no box art).
# --------------------------------------------------------------------------
def build_pdf():
    doc = pdfgen.Pdf(accent=_ACCENT, bg=_BG)
    doc.cover(
        "The pstore User Guide",
        "How to run this software at its highest form: mine niches, rank on "
        "Google, capture emails, send the 5-part buyer sequence, publish on "
        "social and keep your data fresh — step by step.",
        kicker="OWNER MANUAL",
    )

    doc.heading("What pstore is")
    doc.paragraph(
        "pstore is a self-contained Amazon affiliate business in a box. You give "
        "it one seed keyword and it builds a complete money-making machine: ranked "
        "review pages, a sales landing page, an email opt-in with a 5-part buyer "
        "sequence, an AI-written PDF lead magnet, social post kits, an SEM keyword "
        "brief, an SEO audit, click analytics and an automatic data refresher. "
        "Every Amazon link carries your affiliate tag.")
    doc.paragraph(
        "The business runs on a loop: a visitor lands on your niche page (from "
        "Google, social or email), reads the ranked picks, clicks your tagged "
        "Amazon link and buys. Along the way they can opt in to email, and the "
        "5-part sequence turns those free visitors into repeat buyers. One niche "
        "is nice; many niches, each with the loop running, is the game.")
    doc.pullquote("One seed keyword in. A full selling funnel out.")
    doc.page_break()

    doc.heading("The 4 phases")
    doc.paragraph("Every niche moves through four phases. Run all four, every time.")
    doc.bullets([
        "1. Attract — get people to the page (SEO niche pages, social posts, email).",
        "2. Convert — turn visitors into subscribers via the opt-in form.",
        "3. Deliver — send the free ebook and the 5-part email sequence.",
        "4. Multiply — use analytics to double down, and add more niches.",
    ])
    doc.paragraph(
        "These four stages are built right into the Marketing Workbench (/tool), "
        "so the app itself is your roadmap.")
    doc.page_break()

    doc.heading("Step 1 — Mine a niche")
    doc.paragraph(
        "Mine on the Marketing Workbench (/tool) via Launch marketing, or call the "
        "mining API. Enter a broad buying seed such as 'air fryer' or 'trail "
        "camera'. pstore runs your seed through Amazon's own keyless autosuggest "
        "and product search, then reports a demand score (0-10), a saturation "
        "score (0-10) and a magnet product — the best entry offer by reviews and "
        "price.")
    doc.bullets([
        "Pick seeds with high demand and lower saturation first.",
        "Saving a niche auto-generates its review page and auto-submits it to IndexNow.",
        "One seed expands into several sub-niches from autosuggest — save them all.",
    ])
    doc.page_break()

    doc.heading("Step 2 — Rank on Google (SEO)")
    doc.paragraph(
        "Open the SEO audit (/admin/seo) and check the site health strip. Every "
        "niche should show 'indexable'; fix any row with a red badge (title too "
        "long, missing description, no products). Then open the Search Funnel "
        "(/admin/sem) for each niche to get an intent brief, long-tail keywords "
        "and people-also-ask prompts that tell you exactly what to add.")
    doc.paragraph(
        "Indexing is automatic: the moment a niche is saved, pstore submits the "
        "page to IndexNow for near-instant Bing and Google discovery. Confirm the "
        "key is live on the Keys page (/keys).")
    doc.page_break()

    doc.heading("Step 3 — Capture the email")
    doc.paragraph(
        "Every niche page and the homepage already render an opt-in form that "
        "collects first name and email. The first name is used to personalise "
        "every email ('Hi Jane,' not 'Hi there'), which lifts open rates.")
    doc.bullets([
        "Emails are only sent to opted-in subscribers.",
        "Re-subscribing reactivates a previously unsubscribed address.",
        "An unsubscribe link and List-Unsubscribe header are added to every email.",
    ])
    doc.page_break()

    doc.heading("Step 4 — Send the 5-part buyer sequence")
    doc.paragraph(
        "Every subscriber enters a 5-email sequence built from the niche's top "
        "pick: (1) hook and value, (2) social proof, (3) objections, (4) soft "
        "urgency, (5) follow-up and review request. Each email carries your tagged "
        "Amazon link.")
    doc.bullets([
        "On Emails (/admin/emails) choose the recipient selector: All ready (up to 50), First 5, First 10 or First 25.",
        "Leave dry-run checked first to preview without sending real emails.",
        "Add the free PDF ebook to the sequence to lift engagement.",
    ])
    doc.page_break()

    doc.heading("Step 5-6 — Ebook lead magnet and social posts")
    doc.paragraph(
        "The Ebooks page (/admin/ebooks) turns any niche into a designed PDF in "
        "one click. Free AI providers are built in (OpenCode, Mistral, NVIDIA) so "
        "it works with no budget; add an OpenAI key for higher polish.")
    doc.paragraph(
        "The Social page (/admin/social) generates a ready-to-post kit for X, "
        "Facebook, LinkedIn, Instagram, Pinterest and Threads. Each kit has a "
        "platform caption, hashtags and a tracked link with its own code, so every "
        "post's clicks are counted individually in Analytics.")
    doc.page_break()

    doc.heading("Step 7-8 — Launch and keep data fresh")
    doc.paragraph(
        "Instead of doing steps by hand, use Launch marketing on the Workbench "
        "(/tool): it builds the entire funnel for a niche and prints a status "
        "strip showing the landing page is live, IndexNow queued, ebook ready and "
        "clicks tracking. The Workbench also provides DMs, the review pipeline, "
        "boost campaigns, text links, Markdown and QR codes.")
    doc.paragraph(
        "The Data Refresh page (/admin/refresh) keeps prices and ratings accurate. "
        "A background loop re-mines stale niches on a schedule (interval, staleness "
        "window and per-cycle cap are configurable). Use 'Refresh now' per niche or "
        "'Refresh all now' for a manual pass.")
    doc.pullquote("Fresh data protects trust — and trust protects commissions.")
    doc.page_break()

    doc.heading("Highest-form playbook")
    doc.bullets([
        "Set the affiliate tag and SMTP first — nothing else matters until those work.",
        "Stack traffic: SEO (compounding) + social (fast) + email (cheap) + QR and landing pages (offline).",
        "Watch Analytics and double down on the most-clicked products and best sources.",
        "Choose niches like a fund: high demand, lower saturation first.",
        "Comply always: opted-in emails, FTC/Associates disclosure, direct tagged links.",
    ])
    doc.paragraph(
        "Checklist for a new niche: pick a seed, mine it, confirm indexable in the "
        "SEO audit, note the SEM long-tails, Launch marketing, generate the ebook, "
        "publish one or two social posts, dry-run then send the emails, confirm "
        "auto-refresh is on, and paste the text links and QR code anywhere relevant.")
    return doc.save()


def _pdf_bytes():
    return build_pdf()


# --------------------------------------------------------------------------
# HTML admin page — the in-app manual, fully cross-linked.
# --------------------------------------------------------------------------
def render_admin_manual(nav_html, totop_html):
    def a(href, label, note=None):
        note_txt = ('<span class="n">%s</span>' % _esc(note)) if note else ""
        return '<a class="btn" style="width:100%%" href="%s">%s %s</a>' % (
            _esc(href), _esc(label), note_txt)

    # A reference grid linking to every tool, page and feature.
    tools = "".join([
        a("/dashboard", "🧭 Dashboard", "everything"),
        a("/tool", "🛠 Workbench", "launch + funnel"),
        a("/keys", "🔑 Keys", "affiliate tag + endpoints"),
        a("/admin/emails", "📧 Emails", "sequence + subscribers"),
        a("/admin/ebooks", "📕 Ebooks", "PDF lead magnet"),
        a("/admin/analytics", "📊 Analytics", "clicks + sources"),
        a("/admin/social", "📣 Social", "tracked posts"),
        a("/admin/sem", "🎯 SEM", "keywords + funnel"),
        a("/admin/seo", "🔍 SEO audit", "indexability"),
        a("/admin/refresh", "📡 Refresh", "auto + manual"),
        a("/admin", "🗺 All pages", "hub"),
    ])
    pages = "".join([
        a("/", "🏠 Home", "public landing"),
        a("/n/air-fryer", "/n/&lt;slug&gt;", "niche review page"),
        a("/lp/air-fryer", "/lp/&lt;slug&gt;", "sales landing page"),
        a("/sitemap.xml", "🗺 Sitemap", "xml"),
        a("/robots.txt", "🤖 Robots", "txt"),
    ])

    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>User manual — pstore</title>
<link rel="stylesheet" href="/style.css">
<style>.manual-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:10px;margin:6px 0 2px}}
.manual-grid a{{margin:0}}
.manual-grid a .n{{display:block;font-weight:400;font-size:12px;color:var(--muted);margin-top:2px}}
.toc-nav{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0 2px}}
.toc-nav a{{display:block;padding:10px 12px;border:1px solid var(--border);border-radius:12px;text-decoration:none;color:var(--text);font-size:13.5px;font-weight:700;background:#fff}}
.toc-nav a:hover{{border-color:var(--accent);box-shadow:var(--shadow)}}
.manual h3{{margin:26px 0 6px;font-size:17px}}
.manual h3 a{{text-decoration:none;color:var(--text)}}
.manual h3 a:hover{{color:var(--accent)}}
.manual h3 .step{{color:var(--accent);font-weight:800}}
.step-list{{list-style:none;padding:0;margin:8px 0}}
.step-list li{{padding:6px 0 6px 26px;position:relative}}
.step-list li::before{{content:"✓";position:absolute;left:2px;top:6px;color:var(--accent);font-weight:800}}
.diagram{{background:#fff;border:1px solid var(--border);border-radius:14px;padding:16px 18px;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;line-height:1.55;color:#4a4456;overflow-x:auto;white-space:pre;margin:10px 0}}
.diagram b{{color:var(--accent)}}
.tooltag{{display:inline-block;background:#fff;border:1px solid var(--border);border-radius:999px;padding:2px 10px;font-size:12px;margin:2px 4px 2px 0;color:var(--accent);text-decoration:none;font-weight:700}}
.tooltag:hover{{border-color:var(--accent)}}
.dlbar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:4px 0 2px}}
</style>
</head><body>
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>User <span>manual.</span></h1>
<p class="tagline">The complete, visual guide to running pstore at its highest form — chip every tool on this page is a real, clickable shortcut.</p></div>
{nav_html}
</header>
<main>
<section class="card"><h2>📕 The manual — download it</h2>
<div class="dlbar">
  <a class="btn warm" href="/admin/manual.pdf" download>Download PDF manual ⬇</a>
  <a class="btn" href="#toc">Jump to contents ↧</a>
  <a class="btn ghost" href="/admin">🗺 Back to all pages</a>
</div>
<p class="hint" style="margin-top:10px">A styled, printable PDF companion (no links — a document can't click). The section below is fully linked.</p>
</section>

<section class="card"><h2>🧭 Every tool &amp; feature — one click</h2>
<div class="manual-grid">{tools}</div></section>

<section class="card"><h2>🌐 Public site — the pages you rank &amp; sell from</h2>
<div class="manual-grid">{pages}</div></section>

<section class="card manual" id="toc"><h2>🧾 Contents</h2>
<nav class="toc-nav">
  <a href="#what">1 · What pstore is</a>
  <a href="#phases">2 · The 4 phases</a>
  <a href="#s1">3 · Step 1 · Mine</a>
  <a href="#s2">4 · Step 2 · SEO</a>
  <a href="#s3">5 · Step 3 · Capture</a>
  <a href="#s4">6 · Step 4 · Email</a>
  <a href="#s5">7 · Step 5-6 · Ebook + Social</a>
  <a href="#s6">8 · Step 7-8 · Launch + Refresh</a>
  <a href="#playbook">9 · Highest-form playbook</a>
  <a href="#checklist">10 · 30-min checklist</a>
</nav>

<h3 id="what">1 · What pstore is</h3>
<p>pstore is a <b>self-contained Amazon affiliate business in a box</b>. Give it one seed keyword and it builds a complete machine: ranked review pages, a sales landing page, an email opt-in with a 5-part buyer sequence, an AI-written PDF lead magnet, social post kits, an SEM brief, an SEO audit, click analytics and an automatic data refresher — every Amazon link carrying your affiliate tag.</p>
<div class="diagram">YOU give one seed        pstore auto-builds
        air fryer           ├─ review page  /n/air-fryer   (SEO)
            │               ├─ landing page /lp/air-fryer  (convert)
            ▼               ├─ ranked products (demand + saturation)
     amazon autosuggest     ├─ email opt-in + 5-part sequence
     + product search       ├─ free PDF ebook (AI)
            │               ├─ social post kits + QR codes
            ▼               ├─ SEM keywords + SEO audit
      DEMAND 0-10           └─ click analytics + auto refresh
      SATUR. 0-10                ──► every link tagged ?tag=YOURS</div>
<p>The <b>money loop</b>: visitor lands (Google / social / email) → reads ranked picks → clicks your tagged Amazon link → buys → you earn.</p>

<h3 id="phases">2 · The 4 phases (mental model)</h3>
<p>Run all four for every niche. This exact funnel is built into the <a class="tooltag" href="/tool">🛠 Workbench</a>.</p>
<div class="diagram"> 1 ATTRACT      2 CONVERT      3 DELIVER      4 MULTIPLY
  get traffic  capture email  send ebook    doubles down
  (seo, social)→(opt-in form) →+ 5 emails   →(analytics, niches)</div>

<h3 id="s1">3 · Step 1 — Mine a niche</h3>
<p>On the <a class="tooltag" href="/tool">🛠 Workbench</a> → <b>Launch marketing</b>, or call the mining API. Enter a broad buying seed (<i>air fryer</i>, <i>trail camera</i>). pstore reports a <b>demand score</b>, a <b>saturation score</b> and a <b>magnet product</b> — the best entry offer.</p>
<ul class="step-list">
<li>Pick seeds with <b>high demand</b> and <b>lower saturation</b> first.</li>
<li>Saving a niche auto-builds its review page and auto-submits it to <b>IndexNow</b>.</li>
<li>One seed expands into up to <b>5 sub-niches</b> from autosuggest — save them all.</li>
</ul>

<h3 id="s2">4 · Step 2 — Rank on Google (SEO)</h3>
<p>Open the <a class="tooltag" href="/admin/seo">🔍 SEO audit</a> and check the <b>site health strip</b> — every niche should be <i>indexable</i>. Fix red badges (title too long, missing description, no products). Then open <a class="tooltag" href="/admin/sem">🎯 SEM</a> for each niche: intent brief, long-tail keywords and people-also-ask prompts tell you exactly what to add to rank.</p>
<p>Indexing is <b>automatic</b> — saving a niche pings IndexNow. Verify the key on <a class="tooltag" href="/keys">🔑 Keys</a>.</p>

<h3 id="s3">5 · Step 3 — Capture the email</h3>
<p>Every niche page and the homepage already render an <b>opt-in form</b> that collects first name + email. The first name personalises every email (<i>"Hi Jane,"</i>) to lift open rates.</p>
<ul class="step-list">
<li>Only <b>opted-in</b> subscribers are emailed.</li>
<li>Re-subscribing reactivates an unsubscribed address.</li>
<li>Every email carries an <b>unsubscribe</b> link + <code>List-Unsubscribe</code>.</li>
</ul>

<h3 id="s4">6 · Step 4 — Send the 5-part buyer sequence</h3>
<p>Every subscriber enters a 5-email sequence from the top pick: hook → social proof → objections → soft urgency → follow-up + review. Each carries your tagged Amazon link.</p>
<ul class="step-list">
<li>On <a class="tooltag" href="/admin/emails">📧 Emails</a> pick a recipient selector: All ready (up to 50), First 5, First 10 or First 25.</li>
<li>Leave <b>dry-run</b> checked first to preview without sending.</li>
<li>Add the free PDF ebook to the sequence to lift engagement.</li>
</ul>

<h3 id="s5">7 · Step 5-6 — Ebook lead magnet + social posts</h3>
<p>The <a class="tooltag" href="/admin/ebooks">📕 Ebooks</a> page turns any niche into a designed PDF in one click. Free AI providers are built in (OpenCode, Mistral, NVIDIA); add an OpenAI key for extra polish. The <a class="tooltag" href="/admin/social">📣 Social</a> page generates a ready-to-post kit for X, Facebook, LinkedIn, Instagram, Pinterest and Threads — each with a <b>tracked link</b>, so every post's clicks are counted individually in <a class="tooltag" href="/admin/analytics">📊 Analytics</a>.</p>

<h3 id="s6">8 · Step 7-8 — Launch + keep data fresh</h3>
<p>Use <b>Launch marketing</b> on the <a class="tooltag" href="/tool">🛠 Workbench</a> to build the whole funnel in one click, then see a status strip (landing live · IndexNow queued · ebook ready · clicks tracking). The Workbench also has <b>DM scripts, review pipeline, boost campaigns, text links, Markdown and QR codes</b>.</p>
<p>The <a class="tooltag" href="/admin/refresh">📡 Refresh</a> page keeps prices and ratings accurate — a background loop re-mines stale niches automatically, and you can <b>Refresh now</b> per niche or <b>Refresh all now</b>.</p>

<h3 id="playbook">9 · Highest-form playbook</h3>
<ul class="step-list">
<li>Set the <b>affiliate tag</b> and <b>SMTP</b> first — nothing else matters until those work.</li>
<li><b>Stack traffic</b>: SEO (compounding) + social (fast) + email (cheap) + QR/landing pages (offline).</li>
<li>Watch <a class="tooltag" href="/admin/analytics">📊 Analytics</a> and double down on the most-clicked products and best sources.</li>
<li>Choose niches like a fund: <b>high demand, lower saturation</b> first.</li>
<li><b>Comply always</b>: opted-in email, FTC/Associates disclosure, direct tagged links.</li>
</ul>

<h3 id="checklist">10 · 30-minute zero-to-sale checklist</h3>
<ul class="step-list">
<li>Pick a seed (high demand, lower saturation)</li>
<li>Mine it → review demand/saturation → save the niche</li>
<li><a class="tooltag" href="/admin/seo">🔍 SEO</a> → confirm indexable, fix reds</li>
<li><a class="tooltag" href="/admin/sem">🎯 SEM</a> → note long-tails + PAA prompts</li>
<li><a class="tooltag" href="/tool">🛠 Workbench</a> → Launch marketing</li>
<li><a class="tooltag" href="/admin/ebooks">📕 Ebooks</a> → generate the PDF</li>
<li><a class="tooltag" href="/admin/social">📣 Social</a> → publish 1-2 platforms</li>
<li><a class="tooltag" href="/admin/emails">📧 Emails</a> → dry-run → send first batch</li>
<li><a class="tooltag" href="/admin/refresh">📡 Refresh</a> → confirm auto-refresh is on</li>
<li>Paste text links / Markdown / QR anywhere relevant</li>
</ul>
</section>
</main>
<footer><p>User manual — owner section, never indexed. <a href="/admin">All pages</a> · <a href="/admin/logout">Log out</a>.</p></footer>
{totop_html}
</body></html>"""
    return body

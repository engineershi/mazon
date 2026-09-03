# pstore — The Complete User Guide & Selling Playbook

> How to run pstore at its highest form, get real traffic, capture leads, and convert them into Amazon commissions — step by step, with pictures.

---

## 1. What pstore is — the whole system in one picture

pstore is a **self-contained Amazon affiliate business in a box**. You give it a seed keyword, it builds an entire money-making machine around it.

```
                     ┌──────────────────────────────────────────────────────────┐
                     │                       PSTORE                              │
                     │   (a single Python server, one SQLite DB, no framework)    │
                     └──────────────────────────────────────────────────────────┘

  YOU PROVIDE ──▶ ONE KEYWORD SEED                pstore AUTO-GENERATES...
                  e.g. "air fryer"                ┌───────────────────────────────┐
                                                  │ 1. Full niche review pages    │
                                                  │    (/n/air-fryer)  ── SEO      │
                                                  │ 2. Sales landing pages         │
                                                  │    (/lp/air-fryer) ── convert  │
                                                  │ 3. Ranking of real Amazon      │
                                                  │    products by "demand"        │
                                                  │ 4. Email opt-in + 5-part       │
                                                  │    buyer sequence              │
                                                  │ 5. A free PDF ebook lead       │
                                                  │    magnet (AI-written)         │
                                                  │ 6. Social post kits + QR codes │
                                                  │ 7. SEM keywords + SEO audit    │
                                                  │ 8. Click tracking/analytics    │
                                                  └───────────────────────────────┘

                                  Every link carries YOUR affiliate tag
                                  ──────────────────────────────────
                                    pinned below ──▶ on every Amazon link
```

**The money loop**

```
   SEARCH ENGINE        SOCIAL / EMAIL / QR            AMAZON
   (Google finds        (with your affiliate tag)       (pays you)
   your niche pages)
         │                       │                         │
         ▼                       ▼                         ▼
   VISITOR lands ──▶ reads ranked picks ──▶ clicks ──▶ buys ──▶ 3–10% commission
         │
         └──▶ opts in to email ──▶ 5 emails nurture them ──▶ more commissions
```

That is the whole business. Everything below is just "how to make each step bigger and better".

---

## 2. The mental model — 4 phases

Think of pstore as a 4-Phase machine. Run every phase for **every niche**.

```
   PHASE 1            PHASE 2            PHASE 3            PHASE 4
   ─────────          ─────────          ─────────          ─────────
   ATTRACT            CONVERT            DELIVER            MULTIPLY
   Get people to      Turn visitors      Send the lead       Re-sell to and
   the page (SEO,     into subscribers   magnet + the 5      grow everyone
   social, email)     (email opt-in)     email sequence     (analytics,
   ─────────          ─────────          ─────────          new niches)
   The niche page     The opt-in form    The ebook +         The dashboard,
   must rank          must be visible    emails must follow  analytics, refresh
```

> This exact 4-stage funnel is built into the **Marketing Workbench** (`/tool`). It is literally the app's blueprint.

---

## 3. Before you start — one-time setup (do this once)

### 3.1 Run it locally

```bash
cd /root/projects/mazon/app
python3 server.py
# opens on http://localhost:8765
```

### 3.2 Set your secrets (environment variables)

The app reads everything from environment variables. Set these before you launch:

| Variable | What it does | Example |
|---|---|---|
| `PSTORE_TAG` | **Your Amazon affiliate tag** — this is how you get paid. Put it on every link. | `myshop-20` |
| `PSTORE_MARKET` | Which Amazon store (com/co.uk/de/ca/co.jp/com.au/in) | `com` |
| `PSTORE_NAME` | Your brand name (shows in emails & signatures) | `BestPicks` |
| `PSTORE_URL` | Your live site URL (canonicals, sitemap, links) | `https://pstore-gxbv.onrender.com` |
| `PSTORE_ADMIN_EMAIL` / `PSTORE_ADMIN_PASSWORD` | Your admin login | — |
| `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD` | **Required to send emails** (use Gmail app password) | `smtp.gmail.com` |
| `SOCIAL_WEBHOOK` | Optional: POSTs posts to Zapier/Make | — |

> ⚠️ If you do not set `PSTORE_ADMIN_EMAIL` / `PSTORE_ADMIN_PASSWORD`, the server prints a warning. **Always set them.**

### 3.3 Where the features live (quick map)

Every tool is one click from the admin nav bar:

```
   🧭 Dashboard   🛠 Tools   🔑 Keys   📧 Emails   📕 Ebooks
   📊 Analytics   📣 Social   🎯 SEM   🔍 SEO   📡 Refresh   🗺 All pages
   ⚙️ Funnel   ✉️ Variants
```

| Icon | Page | What it's for |
|---|---|---|
| 🧭 Dashboard | `/dashboard` | Overview + all controls in one place |
| 🛠 Tools | `/tool` | **Marketing Workbench** — your daily home base |
| 🔑 Keys | `/keys` | View/enter API keys & endpoints |
| 📧 Emails | `/admin/emails` | Capture + send the email sequence |
| 📕 Ebooks | `/admin/ebooks` | Generate PDF lead magnets |
| 📊 Analytics | `/admin/analytics` | See clicks, conversions & real earnings |
| 📣 Social | `/admin/social` | One-click social posts + auto-amplify winners |
| 🎯 SEM | `/admin/sem` | Keyword & search-funnel intel + "Build this page" |
| 🔍 SEO | `/admin/seo` | Fix pages so Google can index them |
| 📡 Refresh | `/admin/refresh` | Keep product data fresh (auto + manual) |
| 🗺 All pages | `/admin` | Map of everything + every niche page |
| ⚙️ Funnel | `/admin/funnel` | **Real, data-backed sales funnel** (5 stages + per-niche breakdown) |
| ✉️ Variants | `/admin/variants` | **A/B test** email subject lines + social captions |

---

## 4. The Step-by-Step Selling Workflow

This is the exact order to run for **one niche**. Repeat per niche for scale.

### STEP 1 — Mine a niche (create the product)
**Where:** `/tool` → **🚀 Launch** → pick saved niche → "Launch marketing" (or `/api/mine`)

1. Enter a **seed keyword** — a broad buying phrase: `air fryer`, `noise cancelling headphones`, `trail camera`.
2. pstore runs it through Amazon's own **keyless autosuggest** + **product search**.
3. It computes a **Demand Score (0–10)** and **Saturation Score (0–10)** and picks a **magnet product** (best entry offer).

```
   SEED "air fryer"
        │
        ▼
   amazon autosuggest ──▶ 12 real buyer phrases      ("best air fryer for 1 person",
   amazon product search ─▶ real products w/ price,   "air fryer oven combo", ...)
                            stars, reviews            Expanded into sub-niches too
        │
        ▼
   DEMAND  0─10   (how much people search)
   SATUR.  0─10   (how crowded it is)
   MAGNET        (cheapest, most-reviewed entry product)
```

> 💡 **Pro tip for best results:** pick seeds with a healthy demand score but lower saturation. The niche grid on the homepage ranks your best-scoring niches automatically.

### STEP 2 — Get it on Google (SEO)
**Where:** `/admin/seo` (**🔍 SEO audit**) then `/admin/sem` (**🎯 SEM**)

By default pstore already generates SEO-ready pages with schema markup. Your job:

1. Open `/admin/seo` and look at the **site health strip**: every niche should be **"indexable"**.
2. Fix any row with a red `fix` badge (title too long, missing description, few products).
3. Open `/admin/sem` for each niche to get: **intent brief, long-tail keywords, "people also ask"** — these tell you exactly what to add to the page to rank.

```
   SEO AUDIT PAGE (per niche)
   ┌────────────────────────────────────────────┐
   │ Niche    Products Title  Desc Schema Img  │
   │ air-fryer  8        ok    ok    ok   ok    │   ✅ indexable
   │ trail-cam  0        fix   ok    fix  ok    │   ⚠ needs work  ← fix these
   └────────────────────────────────────────────┘
```

4. Indexing is **automatic**: the moment you save a niche, pstore submits the page to **IndexNow** (instant Google/Bing/Baidu indexing). Check `/keys` for the status.

5. **Go long-tail.** On `/admin/opportunities`, click **"Build long-tail pages"** for a proven niche. pstore reads live Amazon autosuggest and mints nested `/n/<niche>/<term>` pages (e.g. `/n/air-fryer/basket-air-fryers`) — each a real, distinct URL with its own ranked ItemList schema, breadcrumbs, links back to the hub, sitemap entry and IndexNow ping. These catch the "long-tail" searches you'd never rank for on their own.

> Every `/n/` verdict page already ships conversion machinery: **reciprocal internal links** (`related`), a **live-price urgency line**, and a **sticky bottom CTA** ("see it on Amazon") that appears on scroll or exit-intent — pointing straight at your #1 pick. The heavier `/lp/` sales pages still carry the promo countdown + opt-in funnel.

### STEP 3 — Capture the email (the money multiplier)
**Where:** every niche page already has an opt-in form — you don't build anything.

- The form collects **first name + email** (that's the `courier.js` form on every page).
- The **first name is captured** so your emails read "Hi Jane," not "Hi there" — this dramatically lifts open rates.

```
   VISITOR on /n/air-fryer
        │
        ▼
   ┌─────────────────────────────┐
   │ The pstore picks note       │
   │ First name: [____]          │   ← used for {{first_name}}
   │ Email:      [____]          │
   │ [ Notify me ]               │
   └─────────────────────────────┘
```

### STEP 4 — Send the 5-part buyer sequence (convert)
**Where:** `/admin/emails` (**📧 Emails**)

Every subscriber automatically receives a **5-email buyer sequence** built from the niche's top pick:

| # | Email | Job it does |
|---|---|---|
| 1 | **Hook + value** — "Ignore if this isn't for you, but [product]..." | Get the open |
| 2 | **Social proof** — "What N buyers already think" | Build trust |
| 3 | **Objections** — "3 questions people ask (answered)" | Remove doubts |
| 4 | **Soft urgency** — "Heads up before this shifts" | Nudge to act |
| 5 | **Follow-up + review** — "One small favour (+ last link)" | Last chance + review |

```
   SUBSCRIBER
      │
      ▼
   Email 1 ──▶ Email 2 ──▶ Email 3 ──▶ Email 4 ──▶ Email 5
      │           │           │           │           │
      └───────────┴─────── each carries your tagged Amazon link ──┘
                                │
                                ▼
                            (sales)
```

**How to send:**
1. On `/admin/emails`, pick a **recipient selector**: *All ready* (up to 50/run), *First 5*, *First 10*, or *First 25*.
2. Leave **dry-run** checked first (no real emails — verifies content).
3. Click **Send next batch**. pstore only emails people who **opted in**, never resends, and auto-stops at 5 emails.

**✉️ A/B-test subject lines (`/admin/variants`):** for each niche you can write **up to 3 subject-line variants** per email. pstore spreads them deterministically across subscribers, then tracks open/click performance per variant (joined against real email_events) so you can see which subject actually earns opens and kill the losers. Same pattern for **social captions** — drop in 2–3 caption variants per platform and pstore A/B serves them; the winner makes auto-amplify far more effective.

> Every email includes a working **unsubscribe link** + `List-Unsubscribe` header (required by law & Amazon). pstore handles all of this.

### STEP 5 — Give away the ebook (the lead magnet)
**Where:** `/admin/ebooks` (**📕 Ebooks**)

A free **PDF ebook** makes opt-ins irresistible and pre-sells your top pick.

```
   You click "Generate"
        │
        ▼
   AI writes chapters ──▶ Cover + title ──▶ PDF (pure stdlib, no deps)
        │                                    │
        │                                    ▼
   Free AI?  ── OpenAI   (paid)   ──▶ gpt-4o-mini
              ── opencode (FREE)   ──▶ kimi-k2.5-free   ← free fallback built in
              ── Mistral (free)    ──▶ mistral-small
              ── NVIDIA  (free)    ──▶ llama-3.3-70b
```

Share the PDF link (`/admin/ebooks/pdf?keyword=...`) on social, in the email sequence, or as a QR code. New readers come back to the niche page → opt in → enter the funnel.

### STEP 6 — Post to social (external traffic)
**Where:** `/admin/social` (**📣 Social**)

One click generates a ready-to-post kit for X, Facebook, LinkedIn, Instagram, Pinterest, and Threads — each with its own caption, hashtags, and a **tracked link** (UTM + unique code per post).

```
   /admin/social ── pick niche
        │
        ▼
   ┌───────────────────────────────────────────┐
   │ X / Twitter                               │
   │ "Best air fryers 2026 — ranked from live  │
   │  Amazon data. Full list + prices: [link]" │
   │ #BestPicks #AmazonFinds ...            │
   │ [ Publish ]  [ Copy post ]  (12 clicks)   │
   └───────────────────────────────────────────┘
        │
        ▼
   your post ──▶ someone clicks ──▶ /lp/air-fryer ──▶ tagged Amazon link ──▶ sale
```

Each post's clicks are tracked individually — you can see in `/admin/analytics` exactly which platform and post performs.

**🔁 Auto-amplify winners:** pstore watches every published post's tracked clicks and automatically **re-queues winners** (posts with real click volume) to future prime-time slots, reusing the same tracked link so you keep the clicks and the code permanently. Anti-loop guards (min age 24h, max 2 reruns, cap 3 per sweep, 48h window) stop it from spamming the same post. Use the **"⚡ Amplify winners now"** button on `/admin/social` to trigger a sweep on demand, or switch the whole feature off with the toggle. A/B-test your captions first (see **STEP on Variants** below) so the amplified post is already the best-performing one.

### STEP 7 — Launch the whole thing at once (one-click)
**Where:** `/tool` → **🚀 Launch marketing**

Instead of doing the steps manually, `Launch` builds the entire funnel for a niche and shows you a summary strip:

```
   ✅ Landing page live    ✅ IndexNow queued    ✅ Ebook ready    ✅ 0 clicks tracked
```

Then hop to the **Marketing Workbench** (`/tool`) for everything else the funnel needs:
- 💬 **DM conversation scripts** (openers, objections, closes)
- ⭐ **Review pipeline** (when + how to ask for reviews)
- 🚀 **Boost campaigns** (PAS, social proof, urgency, bundle, giveaway templates)
- ⚡ **Text links + Markdown + QR codes** (paste anywhere)

### STEP 8 — Keep data fresh (never sell stale products)
**Where:** `/admin/refresh` (**📡 Refresh**) — **automatic by default**

```
   AUTO-REFRESH LOOP (background thread)
   every PSTORE_REFRESH_INTERVAL (1 hour)
        │
        ▼
   finds STALE niches (updated_at older than
   PSTORE_REFRESH_MIN = 24h) ──▶ re-mines up to
   PSTORE_REFRESH_MAX (3) per cycle
        │
        ▼
   refreshed in place (products, score, saturation, updated_at)
```

- On `/admin/refresh` you see live status: total / refreshed / stale / in-flight, with a **fresh / stale badge** and "Refresh now" per row + "Refresh all now".
- This keeps prices, ratings, and "best pick" accurate — which protects trust and ranking.

---

## 5. The "Highest Form" Playbook — squeeze maximum performance

These are the settings and habits that separate a hobby site from an income machine.

### 5.1 Set every key (eliminate free-tier limits)
| Area | Do this |
|---|---|
| **Affiliate tag** | Set `PSTORE_TAG` FIRST. Without it you earn nothing. |
| **SMTP** | Use Gmail/Outlook **app password** (not your login password). Test with dry-run. |
| **AI** | Leave the **free** provider active (opencode/Mistral/NVIDIA) so ebooks generate with zero cost; add OpenAI only if you want higher quality. |
| **Scraper keys** | Optional: ScraperAPI/Outscraper/SerpAPI give more reliable product data than the free HTML fallback. Add via `/keys`. |
| **OAuth** | Nice-to-have: Google/Facebook login so you don't type the password. |

### 5.2 Choose niches like a hedge fund
Use the **Demand vs Saturation** pair, not gut feeling.

```
   HIGH demand + LOW saturation  ──▶ GOLD (build these first, with the ebook)
   HIGH demand + HIGH saturation ──▶ build only with a strong angle / lower price magnet
   LOW  demand                    ──▶ skip (not worth the email/social effort)
```

The homepage **niche grid** auto-ranks your saved niches — use it as a prioritisation list.

### 5.3 Expand one seed into many pages
One seed (`air fryer`) auto-expands into up to **5 sub-niches** from autosuggest. Save them all → you multiply indexable pages, each with its own funnel, email sequence, and ebook. More pages = more Google surface = more organic traffic.

Then go a level deeper: on `/admin/opportunities`, each proven winner can **"Build long-tail pages"** — up to 6 nested `/n/<niche>/<term>` URLs from live autosuggest terms. So one seed can stand for **1 hub + ~5 sub-niches + ~30 long-tail pages**, all internal-linking back to each other, each with identical schema + conversion chrome. That's your compounding organic footprint in one click.

### 5.4 Layer your traffic (don't rely on one channel)
Aim for the mix — each layer compounds:

```
   TRAFFIC LAYERS (stack them)
   ┌────────────────────────────────────────────────┐
   │ 1. SEO (niche pages)      — slow, compounding   │
   │ 2. Social posting         — fast, perishable    │
   │ 3. Email sequence         — on-demand, cheapest  │
   │ 4. QR codes / landing LPs — offline + ads       │
   └────────────────────────────────────────────────┘
```

The strength of pstore: **SEO pages drive opt-ins**, and the **email sequence turns those free visitors into repeat buyers** — so you're not begging Google every day.

### 5.5 Watch analytics, then double down
On `/admin/analytics`:

```
   ┌──────────────────────────────────────────┐
   │ Total clicks      Niches    Subscribers  │
   │     312             6          148       │
   └──────────────────────────────────────────┘
   Top pages        Most-clicked ASINs      By source
   which niche      which product          which platform
   drives clicks    people actually want   drives signups
```

- **Most-clicked ASIN** → promote it harder (feature it in emails, boost posts).
- **By source** → put more posts on the platform that converts.
- **Top pages** → add more related sub-niches to capture that demand.

### 5.6 Set the refresh schedule to your reality
| Goal | Setting |
|---|---|
| Always-fresh prices (you post often) | keep defaults (3600s interval, 24h stale, 3/cycle) |
| Save resources / slow market | raise interval to `86400` (daily), raise stale window |
| Turn auto-refresh off entirely | `PSTORE_REFRESH_INTERVAL=0` |

### 5.7 Comply (protect your commissions)
- pstore only sends to **opted-in** subscribers and always includes **unsubscribe** — stay on the right side of CAN-SPAM/GDPR.
- FTC + Amazon Associates disclosure is auto-rendered (`/disclosure`) — keep it.
- All links are **direct, tagged** Amazon URLs (no cloaking) — Amazon compliant.

---

## 6. Zero-to-sale: a 30-minute launch checklist

A quick, repeatable ritual for a brand-new niche:

```
 [ ] Pick a seed (high demand, low saturation)
 [ ] Mine it  ──▶ review demand/saturation, save the niche
 [ ] /admin/seo  ──▶ confirm "indexable" (fix reds)
 [ ] /admin/sem  ──▶ note the long-tails + PAA prompts
 [ ] /tool  ──▶ "Launch marketing"  (one click)
 [ ] /admin/ebooks  ──▶ Generate the PDF lead magnet
 [ ] /admin/social  ──▶ Publish 1–2 platforms
 [ ] /admin/emails  ──▶ dry-run → then send first batch
 [ ] /admin/refresh  ──▶ confirm auto-refresh is on
 [ ] /admin/opportunities  ──▶ "Build long-tail pages" on winners
 [ ] Paste text-links/Markdown/QR anywhere relevant
```

---

## 7. Troubleshooting quick reference

| Symptom | Cause / Fix |
|---|---|
| Emails not sending | SMTP not configured; set `SMTP_HOST/USER/PASSWORD`; try port 465 (`SMTP_STARTTLS=0`) |
| No products on a niche | Scraper has no key & Amazon fallback was blocked → add a scraper key, or re-mine (`Refresh now`) |
| Ebook is generic tempalated | No AI key → add any provider key (free ones work) |
| Niche shows "stale" | `updated_at` older than `PSTORE_REFRESH_MIN`, or auto-refresh disabled (interval=0) |
| Page not in Google | Verify `/admin/seo` shows indexable + IndexNow key present at `/keys` |
| Can't log in | `PSTORE_ADMIN_EMAIL`/`PSTORE_ADMIN_PASSWORD` not set (server warns at boot) |
| Admin APIs return 401 | Session expired — log in again (12-hour sessions) |

---

## 8. End-to-end architecture (for the curious / for debugging)

```
   BROWSER ──▶ PSTORE (python3 server.py, port 8765)
                  │
                  ├── public   /  /n/*  /lp/*  /og/*  /sitemap.xml
                  ├── admin    /admin/*  /tool  /keys  /dashboard
                  ├── api      /api/*   (mine, niches, sequence, social,
                  │                       sem, seo, refresh, track)
                  │
                  ▼
             ┌── SQLite pstore.db ──────────────┐
             │ niches  subscribers  sent_emails │
             │ clicks  social_posts             │
             └──────────────────────────────────┘
                  │
                  └── outbound: Amazon (data), SMTP (email),
                               IndexNow (indexing), AI (ebook),
                               webhook (Zapier/Make)
```

---

### Key facts to remember

- **Pure stdlib** — no pip install, no framework, runs on any Python 3.11+.
- **Keyless niche mining** — autosuggest + product search need no paid API.
- **Everything is auto-wired** — save a niche → indexed + funnel waiting.
- **The email list is your asset** — it outlives any single search ranking.
- **Refresh keeps you honest** — live prices/ratings protect trust and conversions.

---

*This guide covers the complete pstore surface. For exact routes and endpoints, see the server's `/admin` "All pages" hub and the API reference inside the code.*

# pstore — Amazon Affiliate Niche Finder

Keyless Amazon affiliate marketing: find niches, display products, push buyers.
No Amazon API key required to start — you earn from day one with `?tag=` links.

## Stack
- Stdlib Python (`http.server`, `sqlite3`, `urllib`) — no framework, no install.
- One file per concern: `amazon.py` (keyless product data), `niche.py`
  (niche mining), `seo.py` (crawlable pages), `market_engine.py` (buyer-push
  tools), `server.py` (HTTP).

## Run
```
cd app
python3 server.py            # serves http://localhost:8765
```
Set env: `PSTORE_TAG=youraffiliate-20`, `PSTORE_MARKET=com` (or co.uk/de/ca/in/...).
Set `PSTORE_ADMIN_EMAIL` + `PSTORE_ADMIN_PASSWORD` to lock the owner section
(dashboard, tools, keys, APIs). Without them the server falls back to the
default credentials and warns. Optional Google/Facebook login: set
`OAUTH_GOOGLE_CLIENT_ID`/`OAUTH_GOOGLE_CLIENT_SECRET` or
`OAUTH_FACEBOOK_APP_ID`/`OAUTH_FACEBOOK_APP_SECRET` (with `PSTORE_URL`) to add
"Continue with Google/Facebook" buttons on the login page; only the admin email
wins a session.

## Two parts
- **Public site** — fully crawlable, no login: `/` landing, `/n/<niche>`
  reviews, `/lp/<niche>` sales pages, about/legal, `sitemap.xml`, `robots.txt`.
- **Admin section** — password-gated owner tools, reachable from `/admin`
  (login = `PSTORE_ADMIN_EMAIL` / `PSTORE_ADMIN_PASSWORD`). `/admin` shows a button to every
  page on the site (admin tools + every public page + API endpoints); it also
  houses the niche finder dashboard (`/dashboard`), marketing suite (`/tool`)
  and keys page (`/keys`). Admin pages are `noindex` and never crawlable.

## What it does
- **Product search / display** — keyless: direct Amazon page parse, or plug in
  a ScraperAPI / Outscraper / SerpAPI key in Settings for a proxy.
- **Niche mining** — Amazon autosuggest demand proxy + saturation scoring.
- **SEO pages** — saved niches get crawlable `/n/<slug>` pages with meta,
  OpenGraph, JSON-LD, canonical; `sitemap.xml` + `robots.txt` auto-generated.
- **Buyer-push tools** (`/tool`) — copy-paste affiliate text links, Markdown,
  email draft, social post; `/go/<ASIN>` short redirects to tagged Amazon.

## Security
- Rate limiting (per client login/API/global), a concurrency cap, 413/414 size
  limits, cross-origin POST rejection, and HMAC-signed OAuth state tokens.
- CSP, clickjacking, MIME-sniffing, referrer and HSTS headers on every response;
  HttpOnly/SameSite/Secure cookies. All SQL is parameterized.
- Hardened by design, but no software is invulnerable — keep Render free-tier
  instances current (stale instances intermittently time out).

## Tests
```
python3 -m unittest discover -s tests -v
```
All offline (stub `amazon._urlopen`); no keys or network needed.
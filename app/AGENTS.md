# pstore

## App root
`/root/projects/mazon/app` — Amazon affiliate niche-finder. Backend is stdlib
Python (`http.server`, `sqlite3`, `urllib`). No framework, no install.

## Git note (environment)
The sandbox filesystem has a broken `link()` on `/root/projects` (returns ENOENT),
so a normal in-tree `.git` cannot write loose objects. The working repo keeps its
gitdir at `/root/.gitdirs/mazon` and `/root/projects/mazon/.git` is a gitfile:
```
gitdir: /root/.gitdirs/mazon
```
Do not `rm -rf .git` or `git init` in-tree here. To add the repo remote / push,
operate normally from `/root/projects/mazon` (git auto-reads the gitfile).

## Run
- Server: `python3 server.py` (serves `static/` + `/api/*` on port 8765).
- Env: `PSTORE_TAG=<your-tag>-NN`, `PSTORE_MARKET=com` (default),
  `PSTORE_ADMIN_EMAIL=<email>` + `PSTORE_ADMIN_PASSWORD=<pw>` (gates /admin,
  /dashboard, /tool, /keys, /api/*; falls back to a default with a startup
  warning when either is unset).
- Optional social login: set `OAUTH_GOOGLE_CLIENT_ID`/`OAUTH_GOOGLE_CLIENT_SECRET`
  and/or `OAUTH_FACEBOOK_APP_ID`/`OAUTH_FACEBOOK_APP_SECRET` (with `PSTORE_URL`
  set to the live origin). Redirect URIs are `<PSTORE_URL>/admin/oauth/google/callback`
  and `<PSTORE_URL>/admin/oauth/fb/callback`; only the admin email is granted.
  Unset providers get no button on the login page.

## Security model
- `security.py` = per-client rate limiters (login 5/15min, /api 240/min, global
  720/min), concurrency semaphore (503), 413 body / 414 URI caps, HMAC-signed
  state tokens for OAuth + CSRF. `oauth.py` = stdlib OAuth2 (urllib) for Google
  and Facebook.
- Every response carries CSP, X-Frame-Options, nosniff, Referrer-Policy,
  Permissions-Policy (and HSTS behind TLS). Cookies are HttpOnly + SameSite=Lax
  (+Secure over HTTPS). State-changing POSTs reject cross-origin Origin headers.
- All SQL is parameterized (`?` placeholders) — keep it that way; never build
  queries with string interpolation.

## Tests
```
cd /root/projects/mazon/app
python3 -m unittest discover -s tests -v
```
All tests are offline: they stub `amazon._urlopen` and keep `CACHE_TTL=0`,
`MIN_INTERVAL=0`. After changing backend code run the full suite; keep it green.

## Conventions
- Stdlib only; no third-party imports.
- `amazon.py` = keyless Amazon product data (search, autosuggest, scraper
  providers, affiliate URL builder). `niche.py` = mining. `seo.py` = crawlable
  SSR pages. `market_engine.py` = buyer-push link/text tools. `server.py` = HTTP.
- Every network call bottoms out in module-level `amazon._urlopen` so tests can
  inject fake responses; providers never raise on the happy path.
- Affiliate links are always direct and tagged via `amazon.affiliate_url(asin)`
  (or `market_engine.redirect_url(asin)`); never hardcode `?tag=` and never
  generate cloaked `/go/` links in new output (kept only as a legacy resolver).
- New tests under `tests/` as plain `unittest` classes.
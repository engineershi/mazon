# Mazon

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
- Env: `MAZON_TAG=<your-tag>-NN`, `MAZON_MARKET=com` (default).

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
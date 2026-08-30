# -*- coding: utf-8 -*-
"""pstore HTTP server: serves the static UI + JSON API on http.server (stdlib).

Endpoints
  GET  /                  -> static/index.html
  GET  /api/markets       -> available marketplaces
  GET  /api/autosuggest?q -> real Amazon keyword ideas
  GET  /api/search?q      -> products for a query (+market, +top, +category)
  GET  /api/mine?seed=    -> niche mining (niches + meta + signals)
  GET  /api/niches        -> list saved niches from sqlite
  POST /api/niches        -> save a mined niche shortlist
  GET  /api/settings      -> marketplace + affiliate tag + scraper status
  POST /api/settings      -> set marketplace / affiliate tag / scraper keys
"""
import datetime
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import amazon
import indexnow
import market_engine
import niche
import seo

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
DB = os.environ.get("PSTORE_DB", os.path.join(ROOT, "pstore.db"))
PORT = int(os.environ.get("PORT", "8765"))

# --- admin auth -------------------------------------------------------------
# Everything under /admin + the owner tools/APIs sits behind an admin email +
# password (PSTORE_ADMIN_EMAIL / PSTORE_ADMIN_PASSWORD) with an in-memory
# session cookie. Public pages keep serving without a cookie: /, /n/*, /lp/*,
# about/legal, robots, sitemap, key.
_ADMIN_EMAIL = os.environ.get("PSTORE_ADMIN_EMAIL", "salahuddinhabibisah@gmail.com")
_ADMIN_PW = os.environ.get("PSTORE_ADMIN_PASSWORD", "$_Salahu1991")
_ADMIN_EMAIL_FROM_ENV = bool(os.environ.get("PSTORE_ADMIN_EMAIL"))
_ADMIN_PW_FROM_ENV = bool(os.environ.get("PSTORE_ADMIN_PASSWORD"))
_COOKIE = "pstore_admin"
_SESSION_TTL = 12 * 60 * 60  # seconds
_SESSIONS = {}  # token -> monotonic expiry

_lock = threading.Lock()


def _db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS niches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT NOT NULL,
        market TEXT NOT NULL,
        score REAL,
        saturation REAL,
        products TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    return conn


def _init():
    amazon.MIN_INTERVAL = 0.5
    amazon.MAX_ATTEMPTS = 3
    with _lock:
        conn = _db()
        conn.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _settings(self):
        return {
            "market": amazon.MARKET,
            "markets": amazon._MARKETPLACES,
            "affiliate_tag": amazon.AFFILIATE_TAG,
            "scraper": amazon.scraper_status(),
            "marketing": market_engine.status_blurb(),
        }

    # ------------------------------------------------------------------ admin auth
    def _cookie_token(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith(_COOKIE + "="):
                return part[len(_COOKIE) + 1:]
        return None

    def _authed(self):
        tok = self._cookie_token()
        if not tok:
            return False
        with _lock:
            exp = _SESSIONS.get(tok)
            if exp is None:
                return False
            if exp < time.monotonic():
                _SESSIONS.pop(tok, None)
                return False
            return True

    def _new_session(self):
        tok = secrets.token_hex(32)
        with _lock:
            for old, exp in list(_SESSIONS.items()):
                if exp < time.monotonic():
                    _SESSIONS.pop(old, None)
            _SESSIONS[tok] = time.monotonic() + _SESSION_TTL
        return tok

    def _drop_session(self, tok):
        if tok:
            with _lock:
                _SESSIONS.pop(tok, None)

    def _set_cookie(self, tok, max_age=_SESSION_TTL):
        self.send_header("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
                         % (_COOKIE, tok, max_age))

    def _needs_admin(self, path):
        return (path.startswith("/api/") or path.startswith("/keys/") or
                path in ("/admin", "/dashboard", "/index.html", "/tool", "/keys"))

    def _redirect_login(self, next_path):
        loc = "/admin/login?next=" + urllib.parse.quote(next_path or "/dashboard")
        self.send_response(302)
        self.send_header("Location", loc)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return None

    def _login_page(self, error=None):
        err = ('<p class="msg" style="color:#d64545">%s</p>' % seo._clean(error)) if error else ""
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Admin login — pstore</title><link rel="stylesheet" href="/style.css">
<style>.login-wrap{{min-height:78vh;display:flex;align-items:center;justify-content:center;padding:24px}}
.login-card{{width:100%;max-width:380px;text-align:center}}
.login-card h1{{font-size:26px;letter-spacing:-.4px}}
.login-card input{{width:100%;padding:13px 16px;border:1px solid var(--border);border-radius:14px;font-size:15px;margin:16px 0 8px;background:#fff}}
.login-card button{{width:100%}}
.login-hint{{font-size:12.5px;color:var(--muted);margin-top:14px}}
.login-hint a{{color:var(--accent)}}</style>
</head><body>
<header><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a></header>
<main class="login-wrap"><div class="login-card">
<section class="card">
<h1>🔐 Admin <span style="color:var(--accent)">login</span></h1>
<p class="tagline" style="margin:0">Owner section — pages, tools and keys are locked behind the admin email &amp; password.</p>
{err}
<label style="display:block;text-align:left;font-size:12.5px;color:var(--muted);font-weight:700">Admin email
<input id="em" type="email" placeholder="you@example.com" autocomplete="username"></label>
<label style="display:block;text-align:left;font-size:12.5px;color:var(--muted);font-weight:700">Password
<input id="pw" type="password" placeholder="password" autocomplete="current-password"></label>
<button id="go" class="warm">Unlock admin</button>
<p id="msg" class="msg"></p>
<p class="login-hint">Public site: <a href="/">pstore home</a> · no login needed.</p>
</section></div></main>
<script>
function $(id){{return document.getElementById(id);}}
$("go").onclick = async () => {{
  const next = new URLSearchParams(location.search).get("next") || "/dashboard";
  const r = await fetch("/admin/login", {{method:"POST", headers:{{"Content-Type":"application/json"}},
    body: JSON.stringify({{email: $("em").value.trim(), password: $("pw").value, next: next}})}});
  const d = await r.json().catch(()=>({{ok:false, error:"bad response"}}));
  if (d.ok) location.href = d.next;
  else $("msg").textContent = d.error || "Login failed.";
}};
$("pw").addEventListener("keydown", e => {{ if (e.key === "Enter") $("go").onclick(); }});
</script>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _login_post(self):
        body = self._body()
        email = str(body.get("email") or "").strip().lower()
        pw = str(body.get("password") or "")
        next_path = str(body.get("next") or "/dashboard")
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/dashboard"
        if (not hmac.compare_digest(email.encode("utf-8"), _ADMIN_EMAIL.encode("utf-8"))
                or not hmac.compare_digest(pw.encode("utf-8"), _ADMIN_PW.encode("utf-8"))):
            return self._send(200, {"ok": False, "error": "Wrong email or password"})
        tok = self._new_session()
        data = json.dumps({"ok": True, "next": next_path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._set_cookie(tok)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        return None

    def _logout(self):
        self._drop_session(self._cookie_token())
        self.send_response(302)
        self.send_header("Location", "/admin/login")
        self._set_cookie("x", max_age=0)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return None

    def _admin_nav(self, active=None):
        def chip(href, label, key, accent=False):
            cls = ' class="primary"' if accent else (" class=\"%s\"" % key if key == active else "")
            return '<a href="%s"%s>%s</a>' % (href, cls, label)
        return f"""<nav>
{chip('/dashboard', '🧭 Dashboard', 'dashboard')}
{chip('/tool', '🛠 Tools', 'tool')}
{chip('/keys', '🔑 Keys', 'keys')}
{chip('/admin', '🗺 All pages', 'admin', accent=True)}
{chip('/admin/logout', '⎋ Logout', 'logout')}
</nav>"""

    def _admin_page(self):
        """Admin hub: a button for every page on the site — admin tools, the
        public site (static + every saved niche + landing page) and the APIs."""
        def btn(href, label, note=None, ghost=False):
            note_html = '<span class="n">%s</span>' % seo._clean(note) if note else ""
            return ('<a href="%s" %s>%s %s</a>'
                    % (seo._clean(href), ('class="btn ghost" style="width:100%"' if ghost else 'class="btn" style="width:100%"'),
                       seo._clean(label), note_html))

        tools = [btn("/dashboard", "🧭 Niche finder dashboard", "admin"),
                 btn("/tool", "🛠 Marketing suite", "admin"),
                 btn("/keys", "🔑 Keys & endpoints", "admin")]
        for pid, meta in amazon._SCRAPER_PROVIDERS.items():
            tools.append(btn("/keys/" + seo._clean(pid), meta["name"] + " key", pid))
        tools.append(btn("/admin/logout", "⎋ Log out", "session"))
        tools_html = "".join(tools)

        site = [btn("/", "🏠 Home / landing", "public"),
                btn("/sitemap.xml", "🗺 Sitemap", "xml"),
                btn("/robots.txt", "🤖 Robots", "txt")]
        key_path = "/%s.txt" % indexnow.key()
        site.append(btn(key_path, "🔑 IndexNow key file", "txt"))
        for slug in seo.STATIC_PAGES:
            site.append(btn("/" + slug, slug.title() + " page", slug))
        site_html = "".join(site)

        niches = []
        for n in self._all_niches():
            kw = n["keyword"]
            try:
                slug = seo._slugify(kw)
            except Exception:
                slug = "niche"
            niches.append('<div class="pair">%s%s</div>'
                          % (btn("/n/" + slug, kw, "review"),
                             btn("/lp/" + slug, "lp", "landing", ghost=True)))
        niches_html = "".join(niches) if niches else '<p class="hint">No saved niches yet — mine one on the dashboard.</p>'

        api_html = "".join('<a class="pill" href="%s">%s</a>' % (seo._clean(e), seo._clean(e))
                           for e in ("/api/settings", "/api/niches", "/api/tools",
                                     "/api/mine", "/api/search", "/api/autosuggest",
                                     "/api/indexnow"))
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Admin — all pages · pstore</title>
<link rel="stylesheet" href="/style.css">
<style>.page-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:10px;margin:6px 0 2px}}
.page-grid .pair{{display:contents}}
.page-grid a{{margin:0}}
.page-grid a.btn.ghost{{background:transparent;color:var(--accent);border:1px solid var(--accent);box-shadow:none}}
.pill{{display:inline-block;font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--accent);
border:1px solid var(--border);border-radius:999px;padding:5px 11px;margin:3px 4px 3px 0;text-decoration:none;background:#fff}}
.pill:hover{{border-color:var(--accent)}}
.api-label{{margin-top:16px}}</style>
</head><body>
<header><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>All pages, <span>one hub.</span></h1>
<p class="tagline">Every button opens a page of pstore — owner tools first, then the full public site and its APIs.</p></div>
{self._admin_nav('admin')}
</header>
<main>
<section class="card"><h2>🧭 Admin tools</h2><div class="page-grid">{tools_html}</div></section>
<section class="card"><h2>🌐 Public site — global pages</h2><div class="page-grid">{site_html}</div></section>
<section class="card"><h2>🛍 Public site — saved niches</h2>
<p class="hint">One button per saved niche (top = review page, bottom = its landing page). Open in the same tab — use Back to return.</p>
<div class="page-grid">{niches_html}</div></section>
<section class="card"><h2>📡 API endpoints</h2>
<p class="hint">Read-only links; these answer JSON only when this browser holds a valid admin session.</p>
<div class="api-label">{api_html}</div></section>
</main>
<footer><p>Admin hub — never indexed. <a href="/admin/logout">Log out</a> when done.</p></footer>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)
        try:
            # public auth flow — login/logout never need a session
            if path == "/admin/login":
                if self._authed():
                    return self._redirect_login("/dashboard")
                return self._login_page()
            if path == "/admin/logout":
                return self._logout()
            if self._needs_admin(path) and not self._authed():
                if path.startswith("/api/"):
                    return self._send(401, {"error": "unauthorized", "auth": False})
                return self._redirect_login(path or "/dashboard")
            # SEO + marketing routes first (crawlable + short links)
            if path == "/robots.txt":
                return self._send(200, seo.render_robots(), "text/plain; charset=utf-8")
            if path == "/sitemap.xml":
                return self._send(200, self._sitemap(), "application/xml; charset=utf-8")
            key_body = indexnow.serve_key(path)
            if key_body is not None:
                return self._send(200, key_body.encode("utf-8"), "text/plain; charset=utf-8")
            if path == "/api/indexnow":
                return self._indexnow(q)
            if path == "/":
                return self._send(200, self._landing(), "text/html; charset=utf-8")
            if path.startswith("/n/"):
                return self._niche_page(path, q)
            if path.startswith("/lp/"):
                return self._landing_page(path)
            go = re.match(r"^/go/([A-Z0-9]{10})$", path)
            if go:
                return self._go(go.group(1))
            if path == "/tool":
                return self._send(200, open(os.path.join(STATIC, "tool.html"), "rb").read(), "text/html; charset=utf-8")
            if path == "/admin":
                return self._admin_page()
            if path == "/keys":
                return self._keys_page()
            key_provider = re.match(r"^/keys/([a-z0-9_-]+)$", path)
            if key_provider:
                return self._keys_provider_page(key_provider.group(1))
            if path[1:] in seo.STATIC_PAGES:
                page = getattr(seo, "render_%s" % path[1:])()
                return self._send(200, page, "text/html; charset=utf-8")
            # owner dashboard (static app UI) — kept off "/" so the root stays crawlable
            if path == "/dashboard" or path == "/index.html":
                with open(os.path.join(STATIC, "index.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if path == "/app.js":
                return self._send(200, open(os.path.join(STATIC, "app.js"), "rb").read(), "application/javascript; charset=utf-8")
            if path == "/style.css":
                return self._send(200, open(os.path.join(STATIC, "style.css"), "rb").read(), "text/css; charset=utf-8")
            if path == "/api/settings":
                return self._send(200, self._settings())
            if path == "/api/autosuggest":
                qq = (q.get("q") or [""])[0].strip()
                return self._send(200, {"ideas": amazon.autosuggest(qq, limit=10)})
            if path == "/api/search":
                return self._search(q)
            if path == "/api/mine":
                return self._mine(q)
            if path == "/api/niches":
                return self._list_niches()
            if path == "/api/tools":
                return self._tools(q)
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        try:
            if parsed.path == "/admin/login":
                return self._login_post()
            if parsed.path.startswith("/api/") and not self._authed():
                return self._send(401, {"error": "unauthorized", "auth": False})
            if parsed.path == "/api/niches":
                return self._save_niche()
            if parsed.path == "/api/settings":
                return self._save_settings()
            if parsed.path == "/api/indexnow":
                return self._indexnow_post()
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype == "application/x-www-form-urlencoded":
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return {}

    def _search(self, q):
        query = (q.get("q") or [""])[0].strip()
        market = (q.get("market") or [""])[0].strip()
        category = (q.get("category") or [""])[0].strip()
        top = int((q.get("top") or ["8"])[0])
        if market:
            amazon.set_market(market)
        items, source = amazon.search(query, top=top, category=category)
        return self._send(200, {"query": query, "market": amazon.MARKET,
                                "source": source, "items": items, "count": len(items)})

    def _mine(self, q):
        seed = (q.get("seed") or [""])[0].strip()
        market = (q.get("market") or [""])[0].strip()
        top = int((q.get("top") or ["8"])[0])
        if market:
            amazon.set_market(market)
        niches, meta = niche.mine_niche(seed, top=top)
        return self._send(200, {"niches": niches, "meta": meta})

    def _save_settings(self):
        body = self._body()
        with _lock:
            if body.get("market"):
                amazon.set_market(body["market"])
            if "affiliate_tag" in body:
                amazon.set_tag(body["affiliate_tag"])
            for pid, key in (body.get("scraper") or {}).items():
                if pid in amazon._SCRAPER_PROVIDERS and key is not None:
                    amazon.set_scraper_key(pid, key)
        return self._send(200, self._settings())

    def _save_niche(self):
        body = self._body()
        products = json.dumps(body.get("products") or [])
        with _lock:
            conn = _db()
            cur = conn.execute(
                "INSERT INTO niches (keyword, market, score, saturation, products) VALUES (?,?,?,?,?)",
                (body.get("keyword"), amazon.MARKET, body.get("score"), body.get("saturation"), products))
            conn.commit()
            nid = cur.lastrowid
            conn.close()
        return self._send(200, {"id": nid})

    def _list_niches(self):
        with _lock:
            conn = _db()
            rows = conn.execute("SELECT * FROM niches ORDER BY id DESC LIMIT 50").fetchall()
            conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["products"] = json.loads(d["products"] or "[]")
            out.append(d)
        return self._send(200, {"niches": out})

    # ------------------------------------------------------------------ SEO
    def _all_niches(self):
        with _lock:
            conn = _db()
            rows = conn.execute("SELECT keyword, products FROM niches").fetchall()
            conn.close()
        return [{"keyword": r["keyword"], "products": json.loads(r["products"] or "[]")}
                for r in rows]

    def _sitemap(self):
        entries = [("/", "2026-08-28")]
        for page in seo.STATIC_PAGES:
            entries.append(("/" + page, "2026-08-28"))
        for n in self._all_niches():
            try:
                kw = seo._slugify(n["keyword"])
            except Exception:
                kw = "niche"
            entries.append((f"/n/{kw}", "2026-08-28"))
        return seo.render_sitemap(entries)

    def _landing(self):
        return seo.render_landing(self._all_niches())

    def _niche_page(self, path, q):
        slug = path[len("/n/"):].rstrip("/") or "niche"
        all_niches = self._all_niches()
        for n in all_niches:
            try:
                if slug == seo._slugify(n["keyword"]):
                    return self._send(200, seo.render_niche(n["keyword"], n, saved_niches=all_niches),
                                      "text/html; charset=utf-8")
            except Exception:
                continue
        return self._send(404, seo.render_niche(slug, {"products": [], "source": ""}),
                          "text/html; charset=utf-8")

    def _go(self, asin):
        target, market = market_engine.expand_go(asin)
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return None

    def _landing_page(self, path):
        slug = path[len("/lp/"):].rstrip("/") or "niche"
        for n in self._all_niches():
            try:
                if slug == seo._slugify(n["keyword"]):
                    html = market_engine.build_landing_page(
                        n["keyword"], n["products"], site_url=seo.BASE_URL)
                    return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            except Exception:
                continue
        return self._send(404, b"<html><body><p>Landing page not found.</p></body></html>",
                          "text/html; charset=utf-8")

    # ------------------------------------------------------------------ marketing tools
    def _all_urls(self, extra=None):
        """Absolute site URLs that should be indexed (sitemap set + extras)."""
        urls = seo.indexable_urls(self._all_niches())
        for u in (extra or []):
            if str(u).startswith("http"):
                urls.append(str(u))
            else:
                urls.append(seo.BASE_URL.rstrip("/") + str(u))
        return urls

    def _indexnow(self, q):
        return self._send(200, {
            "key": indexnow.key(),
            "key_file": indexnow.key_file_path(),
            "endpoint": indexnow.ENDPOINT,
            "url_count": len(self._all_urls()),
            "status": "ready",
        })

    def _indexnow_post(self):
        body = self._body()
        urls = self._all_urls(body.get("urls") or [])
        ok, message = indexnow.submit_urls(urls)
        return self._send(200, {"ok": ok, "message": message, "submitted": len(urls)})

    def _keys_page(self):
        """One admin page with every key/endpoint a tool needs."""
        rows = [
            ("IndexNow key", indexnow.key()),
            ("IndexNow key file", indexnow.key_file_path()),
            ("IndexNow endpoint", indexnow.ENDPOINT),
            ("Submit all URLs (POST)", seo.BASE_URL.rstrip("/") + "/api/indexnow"),
            ("Sitemap", seo.BASE_URL.rstrip("/") + "/sitemap.xml"),
            ("Affiliate tag", amazon.AFFILIATE_TAG or "(none set)"),
            ("Marketplace", amazon.MARKET),
            ("Scraper", amazon.scraper_status().get("active", "n/a")),
        ]
        cards = "".join(
            '<div class="sub"><h3>%s</h3><p class="key" '
            'onclick="navigator.clipboard&&navigator.clipboard.writeText(this.textContent)" '
            'title="Click to copy">%s</p></div>' % (k, v)
            for k, v in rows)
        prov = amazon.scraper_status()["providers"]
        prov_cards = "".join(
            '<div class="sub"><h3>%s</h3>'
            '<p class="key"><a href="/keys/%s">/keys/%s</a> · %s</p></div>'
            % (pv["name"], pid, pid,
               "key set" if pv["has_key"] else "no key set")
            for pid, pv in prov.items())
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Keys — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
<style>.key{{font-family:ui-monospace,Menlo,monospace;font-size:13px;background:var(--bg);border:1px solid var(--border);
border-radius:10px;padding:10px 12px;margin:0;word-break:break-all;cursor:copy}}
.sub{{padding:8px 0}}.sub h3{{margin:0 0 6px;font-size:13px;color:var(--muted);font-weight:700}}</style>
</head><body>
<header><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>Keys & <span>endpoints.</span></h1>
<p class="tagline">Every key, URL and endpoint the tools need — click a value to copy it.</p></div>
{self._admin_nav('keys')}
</header>
<main><section class="card"><h2>🔑 Keys & endpoints</h2>{cards}
<h2>🛢 Scraper provider keys</h2>
<p class="hint">One short page per provider — see status, open its dashboard, paste or clear the key:</p>{prov_cards}
</section></main>
<footer><p>Keep the IndexNow key secret — it proves who owns the site to the search engines.</p></footer>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _keys_provider_page(self, pid):
        """Short per-provider page: see key status, open the provider's own
        dashboard to fetch an API key, and save (or clear) it on this instance."""
        meta = amazon._SCRAPER_PROVIDERS.get(pid)
        if not meta:
            return self._send(
                404, b"<html><body><p>Unknown provider.</p></body></html>",
                "text/html; charset=utf-8")
        key = amazon._scraper_key(pid)
        has_env = bool(os.environ.get(meta["env_var"]))
        masked = (("••••" + key[-4:]) if key else "(no key set)")
        status = ("key set" if key else "no key set") + \
                 (" · from environment %s" % meta["env_var"] if has_env else "")
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{meta['name']} key — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
<style>.key{{font-family:ui-monospace,Menlo,monospace;font-size:13px;background:var(--bg);border:1px solid var(--border);
border-radius:10px;padding:10px 12px;margin:0;word-break:break-all}}
.masked{{font-size:18px;font-weight:700;letter-spacing:1px}}</style>
</head><body>
<header><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>{meta['name']} <span>API key.</span></h1>
<p class="tagline">Paste your {meta['name']} API key below, or grab a new one at their dashboard.</p></div>
{self._admin_nav('keys')}
</header>
<main>
<section class="card"><h2>🎯 {meta['name']} ({meta['kind']})</h2>
<div class="sub"><h3>Current status</h3><p class="key masked">{masked}</p><p class="hint">{status}</p></div>
<div class="sub"><h3>Get an API key</h3>
<p class="hint">Create / copy your key at the {meta['name']} dashboard, then paste it here:</p>
<p><a class="btn" href="{meta['key_url']}" target="_blank" rel="noopener">Open {meta['name']} dashboard ↗</a></p>
</div>
<div class="row">
  <label>API key
    <input id="key" placeholder="paste key…" autocomplete="off" spellcheck="false">
  </label>
  <button id="save" class="warm">Save key</button>
  <button id="clear">Clear key</button>
</div>
<p id="msg" class="msg"></p>
<a href="/keys" class="hint">← all keys</a>
</section></main>
<footer><p>Keys are stored in memory for this instance; set {meta['env_var']} as an environment variable to make them permanent.</p></footer>
<script>
function $(id){{return document.getElementById(id);}}
async function post(payload){{
  const r = await fetch("/api/settings", {{method:"POST", headers:{{"Content-Type":"application/json"}},
    body: JSON.stringify(payload)}});
  return r.ok;
}}
$("save").onclick = async () => {{
  const v = $("key").value.trim();
  if (await post({{scraper: {{"{pid}": v}}}})) {{ $("msg").textContent = "Saved ✓"; setTimeout(()=>location.reload(), 700); }}
  else $("msg").textContent = "Save failed.";
}};
$("clear").onclick = async () => {{
  if (await post({{scraper: {{"{pid}": ""}}}})) {{ $("msg").textContent = "Cleared."; setTimeout(()=>location.reload(), 700); }}
  else $("msg").textContent = "Clear failed.";
}};
$("key").addEventListener("keydown", e => {{ if (e.key === "Enter") $("save").onclick(); }});
</script>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _best_for_tools(self, q):
        items = []
        keyword = (q.get("keyword") or [""])[0].strip()
        if q.get("items"):
            try:
                items = json.loads(q["items"][0])
            except Exception:
                items = []
        if not items and keyword:
            for n in self._all_niches():
                if n["keyword"].lower() == keyword.lower():
                    items = n["products"]
                    break
        return items, keyword

    def _tools(self, q):
        items, keyword = self._best_for_tools(q)
        n = len(items or [])
        return self._send(200, {
            "keyword": keyword, "count": n, "affiliate_tag": amazon.AFFILIATE_TAG,
            "text_links": market_engine.build_text_links(items),
            "markdown": market_engine.build_markdown(items, heading=keyword),
            "email": market_engine.build_email_draft(items),
            "post": market_engine.build_post_template(items),
            "top_pick": market_engine.pick_for_buyers(items),
            "funnel": market_engine.build_funnel(keyword, items,
                                                 site_url=seo.BASE_URL,
                                                 affiliate_tag=amazon.AFFILIATE_TAG),
        })


def main():
    _init()
    if not _ADMIN_EMAIL_FROM_ENV or not _ADMIN_PW_FROM_ENV:
        print("WARNING: PSTORE_ADMIN_EMAIL / PSTORE_ADMIN_PASSWORD not both set — "
              "using the default admin credentials. Set them in Render/env before going public.")
    amazon.set_market(os.environ.get("PSTORE_MARKET", amazon.DEFAULT_MARKET))
    amazon.set_tag(os.environ.get("PSTORE_TAG", ""))
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("pstore running on http://localhost:%d" % PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

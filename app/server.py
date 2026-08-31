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
   GET  /api/ai/providers  -> AI provider status (admin)
   POST /api/ai/test       -> one-shot key test (admin)
   POST /api/ai/models     -> list provider models (admin)
   POST /api/ai/config     -> activate a provider runtime (admin)
"""
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import amazon
import ai
import ebook as ebook_mod
import indexnow
import mailer
import market_engine
import niche
import oauth
import seo
import security

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
_OAUTH_COOKIE = "pstore_oauth"
_SESSION_TTL = 12 * 60 * 60  # seconds
_SESSIONS = {}  # token -> monotonic expiry

_EBOOKS = {}  # keyword -> build_ebook() dict, LRU-ish (capped below)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

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
    conn.execute("""CREATE TABLE IF NOT EXISTS subscribers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        source TEXT,
        keyword TEXT,
        first_name TEXT,
        confirmed INTEGER DEFAULT 1,
        unsubscribed INTEGER DEFAULT 0,
        sent_index INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sent_emails (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subscriber_id INTEGER NOT NULL,
        email_index INTEGER NOT NULL,
        subject TEXT,
        sent_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS clicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT,
        source TEXT,
        ip TEXT,
        referrer TEXT,
        asin TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    try:
        conn.execute("ALTER TABLE clicks ADD COLUMN asin TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
    return conn


def _init():
    amazon.MIN_INTERVAL = 0.5
    amazon.MAX_ATTEMPTS = 3
    with _lock:
        conn = _db()
        conn.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "pstore"
    sys_version = ""

    def log_message(self, *a):
        pass

    def send_header(self, key, value):
        """Defer headers until the status line is written, so callers may set
        cookies before send_response() without corrupting the response."""
        if not getattr(self, "_resp_started", False):
            if not hasattr(self, "_resp_early"):
                self._resp_early = []
            self._resp_early.append((key, value))
            return
        super().send_header(key, value)

    def send_response(self, code, message=None):
        if not getattr(self, "_resp_started", False):
            self._resp_started = True
            super().send_response(code, message)
            for k, v in getattr(self, "_resp_early", []):
                super().send_header(k, v)
            self._resp_early = []
            return
        super().send_response(code, message)

    def _is_secure(self):
        return (self.headers.get("X-Forwarded-Proto") or "http").lower() == "https"

    def end_headers(self):
        """Security headers on every response: clickjacking, MIME sniffing,
        referrer + CSP, permissions and HSTS behind the TLS proxy."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; "
                         "font-src 'self'; connect-src 'self'; object-src 'none'; "
                         "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("Permissions-Policy",
                         "geolocation=(), microphone=(), camera=(), payment=()")
        if self._is_secure():
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        super().end_headers()

    def _client_ip(self):
        try:
            return self.client_address[0] or ""
        except Exception:
            return ""

    def _same_origin(self):
        """CSRF defense for state-changing requests: when a browser sends
        Origin, its host must match where the request actually landed."""
        origin = self.headers.get("Origin")
        if not origin:
            return True  # curl / server-to-server
        host = (self.headers.get("Host") or "").lower()

        def norm(h):
            if h.endswith(":443"):
                return h[:-4]
            if h.endswith(":80"):
                return h[:-3]
            return h
        try:
            return norm(urllib.parse.urlsplit(origin).netloc.lower()) == norm(host.split(" ")[0])
        except Exception:
            return False

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
            "mailer": {
                "configured": mailer.configured(),
                "host": mailer.SMTP_HOST or "",
                "max_per_run": mailer.MAX_EMAILS_PER_RUN,
            },
            "ai": {
                "configured": ai.configured(),
                "provider": ai.active_provider(),
                "model": ai.model_for(ai.active_provider() or "openai") if ai.configured() else "",
                "providers": ai.providers(),
                "note": "Ebook/headline copy uses the active AI provider; template fallback otherwise.",
            },
            "publish": {
                "indexnow_key": indexnow.key(),
                "sitemap": "/sitemap.xml",
                "site_url": seo.BASE_URL,
            },
        }

    # ------------------------------------------------------------------ AI panel
    def _ai_providers(self):
        return self._send(200, {"providers": ai.providers(),
                                "active": ai.active_provider()})

    def _ai_test(self):
        body = self._body()
        return self._send(200, ai.test(
            body.get("provider"), body.get("api_key") or body.get("key"),
            body.get("model"), body.get("base_url") or body.get("base")))

    def _ai_models(self):
        body = self._body()
        models = ai.list_models(
            body.get("provider"), body.get("api_key") or body.get("key"),
            body.get("base_url") or body.get("base"))
        return self._send(200, {"provider": body.get("provider"), "models": models})

    def _ai_config(self):
        body = self._body()
        out = ai.configure_runtime(
            body.get("provider"), body.get("api_key") or body.get("key"),
            body.get("model"), body.get("base_url") or body.get("base"))
        if out.get("ok"):
            with _lock:
                _EBOOKS.clear()
        return self._send(200, out)

    # ------------------------------------------------------------------ admin auth
    def _cookie_token(self, cookie=_COOKIE):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith(cookie + "="):
                return part[len(cookie) + 1:]
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

    def _set_cookie(self, tok, max_age=_SESSION_TTL, cookie=_COOKIE, path="/", secure=None):
        if secure is None:
            secure = self._is_secure()
        self.send_header("Set-Cookie", "%s=%s; Path=%s; HttpOnly; SameSite=Lax; Max-Age=%d%s"
                         % (cookie, tok, path, max_age, "; Secure" if secure else ""))

    def _prelim_guard(self):
        """Flood/DoS pre-checks shared by every request. Returns the client key
        when the request may proceed, else sends the rejection and returns None."""
        if len(self.path) > security.MAX_URL:
            self._send(414, {"error": "URI too long"})
            return None
        key = security.client_key(self.headers, self._client_ip())
        if not security.HTTP_LIMITER.hit(key):
            self._send(429, {"error": "too many requests"})
            return None
        if self.path.startswith("/api/") and not security.API_LIMITER.hit("api|" + key):
            self._send(429, {"error": "too many requests"})
            return None
        return key

    def _needs_admin(self, path):
        return (path.startswith("/api/") or path.startswith("/keys/") or
                path.startswith("/admin/") or
                path in ("/admin", "/dashboard", "/index.html", "/tool", "/keys"))

    def _redirect_login(self, next_path):
        loc = "/admin/login?next=" + urllib.parse.quote(next_path or "/dashboard")
        self.send_response(302)
        self.send_header("Location", loc)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return None

    def _login_page(self, error=None, oauth_error=None):
        err = ('<p class="msg" style="color:#d64545">%s</p>' % seo._clean(error or oauth_error)) if (error or oauth_error) else ""
        route = {"google": "google", "facebook": "fb"}
        prov = oauth.providers_configured()
        if prov:
            oauth_html = ('<div class="oauth">%s</div><p class="divider">— or sign in with email —</p>'
                          % "".join(
                              '<a class="btn oauth-btn" href="/admin/oauth/%s">%s ↗</a>'
                              % (route[p], desc) for p, _name, desc in prov))
        else:
            oauth_html = ""
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Admin login — pstore</title><link rel="stylesheet" href="/style.css">
<style>.login-wrap{{min-height:78vh;display:flex;align-items:center;justify-content:center;padding:24px}}
.login-card{{width:100%;max-width:380px;text-align:center}}
.login-card h1{{font-size:26px;letter-spacing:-.4px}}
.login-card input{{width:100%;padding:13px 16px;border:1px solid var(--border);border-radius:14px;font-size:15px;margin:8px 0 12px;background:#fff}}
.login-card button{{width:100%;margin-top:4px}}
.login-hint{{font-size:12.5px;color:var(--muted);margin-top:14px}}
.login-hint a{{color:var(--accent)}}
.oauth{{display:flex;flex-direction:column;gap:8px;margin:14px 0 4px}}
.oauth-btn{{background:#fff;color:var(--text);border:1px solid var(--border);box-shadow:none}}
.oauth-btn:hover{{transform:translateY(-2px);box-shadow:var(--shadow)}}
.divider{{color:var(--muted);font-size:12px;font-weight:600;letter-spacing:.3px}}</style>
</head><body>
<header><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a></header>
<main class="login-wrap"><div class="login-card">
<section class="card">
<h1>🔐 Admin <span style="color:var(--accent)">login</span></h1>
<p class="tagline" style="margin:0">Owner section — pages, tools and keys are locked behind the admin email &amp; password.</p>
{err}
{oauth_html}
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
        key = "login|" + security.client_key(self.headers, self._client_ip())
        if not security.LOGIN_LIMITER.hit(key):
            return self._send(429, {"error": "too many login attempts, try again later"})
        body = self._body()
        email = str(body.get("email") or "").strip().lower()
        pw = str(body.get("password") or "")
        next_path = str(body.get("next") or "/dashboard")
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/dashboard"
        if (not hmac.compare_digest(email.encode("utf-8"), _ADMIN_EMAIL.encode("utf-8"))
                or not hmac.compare_digest(pw.encode("utf-8"), _ADMIN_PW.encode("utf-8"))):
            return self._send(200, {"ok": False, "error": "Wrong email or password"})
        security.LOGIN_LIMITER.clear(key)
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
{chip('/admin/emails', '📧 Emails', 'emails')}
{chip('/admin/ebooks', '📕 Ebooks', 'ebooks')}
{chip('/admin/analytics', '📊 Analytics', 'analytics')}
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
                 btn("/keys", "🔑 Keys & endpoints", "admin"),
                 btn("/admin/emails", "📧 Email capture & auto-send", "opt-in"),
                 btn("/admin/ebooks", "📕 AI ebook generator", "PDF lead magnet"),
                 btn("/admin/analytics", "📊 Click tracking & analytics", "beacons")]
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
                                     "/api/tools/launch",
                                     "/api/mine", "/api/search", "/api/autosuggest",
                                     "/api/indexnow", "/api/subscribers", "/api/sequence/send"))
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

    def _oauth(self, provider, callback, q):
        configured = {p for p, _n, _d in oauth.providers_configured()}
        if provider not in configured:
            return self._send(404, {"error": "oauth provider not configured"})
        if not callback:
            # Start: sign a short-lived state token, stash it, bounce to provider.
            state = security.make_token("oauth:" + provider, 600)
            url = oauth.authorize_url(provider, state)
            if not url:
                return self._send(404, {"error": "oauth provider not configured"})
            self.send_response(302)
            self._set_cookie(state, max_age=600, cookie=_OAUTH_COOKIE, path="/admin/oauth")
            self.send_header("Location", url)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return None
        # Callback: verify state, exchange the code, grant only to the admin mail.
        code = (q.get("code") or [""])[0]
        state = (q.get("state") or [""])[0]
        expect = self._cookie_token(_OAUTH_COOKIE)
        scope = security.verify_token(expect) if expect else None
        if not code or not expect or not hmac.compare_digest(expect, state) or scope != "oauth:" + provider:
            self._set_cookie("x", max_age=0, cookie=_OAUTH_COOKIE, path="/admin/oauth")
            return self._login_page(oauth_error="Sign-in link was stale or tampered with — try again.")
        try:
            email, _name = oauth.exchange(provider, code)
        except Exception:
            return self._login_page(oauth_error="OAuth sign-in failed, please try again.")
        if not email or email != _ADMIN_EMAIL.lower():
            return self._login_page(oauth_error="This account is not authorized to administer pstore.")
        tok = self._new_session()
        self.send_response(302)
        self._set_cookie(tok)
        self.send_header("Location", "/dashboard")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return None

    def do_GET(self):
        if self._prelim_guard() is None:
            return
        acquired = security.CONCURRENCY.acquire(timeout=0.05)
        try:
            if not acquired:
                return self._send(503, {"error": "busy, retry shortly"})
            return self._dispatch_get()
        finally:
            if acquired:
                security.CONCURRENCY.release()

    def _dispatch_get(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)
        try:
            # public auth flow — login/logout/oauth never need a session
            if path == "/admin/login":
                if self._authed():
                    return self._redirect_login("/dashboard")
                return self._login_page()
            if path == "/admin/logout":
                return self._logout()
            oauth_route = re.match(r"^/admin/oauth/(google|fb)(/callback)?$", path)
            if oauth_route:
                provider = "google" if oauth_route.group(1) == "google" else "facebook"
                return self._oauth(provider, callback=bool(oauth_route.group(2)), q=q)
            # public opt-out + click beacons never need a session
            if path.startswith("/unsubscribe"):
                return self._unsubscribe(q)
            if path == "/api/track":
                return self._track_click()
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
            if path == "/admin/ebooks/pdf":
                return self._ebook_pdf(q)
            if path == "/admin/ebooks":
                return self._admin_ebooks(q)
            if path == "/admin/emails":
                return self._admin_emails(q)
            if path == "/admin/analytics":
                return self._admin_analytics()
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
            if path == "/courier.js":
                with open(os.path.join(STATIC, "courier.js"), "rb") as fh:
                    return self._send(200, fh.read(),
                                      "application/javascript; charset=utf-8")
            if path == "/api/settings":
                return self._send(200, self._settings())
            if path == "/api/ai/providers":
                return self._ai_providers()
            if path == "/api/autosuggest":
                qq = (q.get("q") or [""])[0].strip()
                return self._send(200, {"ideas": amazon.autosuggest(qq, limit=10)})
            if path == "/api/search":
                return self._search(q)
            if path == "/api/mine":
                return self._mine(q)
            if path == "/api/niches":
                return self._list_niches()
            if path == "/api/subscribers":
                return self._subscribers_json()
            if path == "/api/tools":
                return self._tools(q)
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        if self._prelim_guard() is None:
            return
        if not self._same_origin():
            return self._send(403, {"error": "cross-origin request blocked"})
        try:
            cl = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            cl = 0
        if cl > security.MAX_BODY:
            return self._send(413, {"error": "payload too large"})
        acquired = security.CONCURRENCY.acquire(timeout=0.05)
        try:
            if not acquired:
                return self._send(503, {"error": "busy, retry shortly"})
            return self._dispatch_post()
        finally:
            if acquired:
                security.CONCURRENCY.release()

    def _dispatch_post(self):
        parsed = urllib.parse.urlsplit(self.path)
        try:
            if parsed.path == "/admin/login":
                return self._login_post()
            if parsed.path == "/subscribe":
                return self._subscribe()
            if parsed.path == "/api/track":
                return self._track_click()
            if parsed.path.startswith("/api/") and not self._authed():
                return self._send(401, {"error": "unauthorized", "auth": False})
            if parsed.path == "/api/niches":
                return self._save_niche()
            if parsed.path == "/api/settings":
                return self._save_settings()
            if parsed.path == "/api/indexnow":
                return self._indexnow_post()
            if parsed.path == "/api/sequence/send":
                return self._sequence_send()
            if parsed.path == "/api/tools/launch":
                return self._tools_launch()
            if parsed.path == "/api/subscribers":
                return self._subscribers_json()
            if parsed.path == "/api/ai/test":
                return self._ai_test()
            if parsed.path == "/api/ai/models":
                return self._ai_models()
            if parsed.path == "/api/ai/config":
                return self._ai_config()
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_PUT(self):
        self._send(405, {"error": "method not allowed"})

    def do_DELETE(self):
        self._send(405, {"error": "method not allowed"})

    def do_PATCH(self):
        self._send(405, {"error": "method not allowed"})

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            n = 0
        if n > security.MAX_BODY:
            raise ValueError("payload too large")
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
        self._push_indexnow(body.get("keyword"))
        return self._send(200, {"id": nid})

    def _fire_indexnow(self, paths):
        """Fire-and-forget IndexNow submit for a list of site-relative paths
        (e.g. /n/keto-snacks). Never blocks the caller and never raises."""
        try:
            base = seo.BASE_URL.rstrip("/")
            urls = [base + ("/" + p.lstrip("/")) for p in (paths or [])]
        except Exception:
            return
        if not urls:
            return
        threading.Thread(target=lambda: indexnow.submit_urls(urls), daemon=True).start()

    def _push_indexnow(self, keyword):
        """Fire-and-forget IndexNow submit so a brand-new /n/ page is crawled
        in minutes instead of waiting for a sitemap re-crawl. Never blocks the
        save response and never raises."""
        try:
            slug = seo._slugify(keyword)
        except Exception:
            slug = "niche"
        self._fire_indexnow(["/n/" + slug])

    def _list_niches(self):
        with _lock:
            conn = _db()
            rows = conn.execute("SELECT * FROM niches ORDER BY id DESC LIMIT 50").fetchall()
            conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["products"] = json.loads(d["products"] or "[]")
            try:
                d["slug"] = seo._slugify(d["keyword"])
            except Exception:
                d["slug"] = ""
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

    def _workbench_payload(self, keyword, items):
        slug = seo._slugify(keyword) if keyword else ""
        niche_url = seo.BASE_URL.rstrip("/") + "/n/" + slug if slug else None
        landing_url = "/lp/" + slug if slug else None
        subs, clicks, top_product = self._niche_stats(keyword, slug)
        ebook_url = "/admin/ebooks/pdf?keyword=%s" % urllib.parse.quote(keyword) if keyword else None
        return {
            "keyword": keyword,
            "count": len(items or []),
            "affiliate_tag": amazon.AFFILIATE_TAG,
            "text_links": market_engine.build_text_links(items),
            "markdown": market_engine.build_markdown(items, heading=keyword),
            "email": market_engine.build_email_draft(items),
            "post": market_engine.build_post_template(items),
            "top_pick": market_engine.pick_for_buyers(items),
            "funnel": market_engine.build_funnel(keyword, items,
                                                 site_url=seo.BASE_URL,
                                                 affiliate_tag=amazon.AFFILIATE_TAG),
            "slug": slug,
            "niche_url": niche_url,
            "landing_url": landing_url,
            "ebook_url": ebook_url,
            "ebook_ready": keyword in _EBOOKS,
            "stats": {"subscribers_active": subs["active"],
                      "subscribers_ready": subs["ready"],
                      "clicks": clicks,
                      "top_product": top_product},
            "indexnow": {"key": indexnow.key(), "status": "ready"},
        }

    def _niche_stats(self, keyword, slug):
        """Per-niche feedback loop readings: opt-in subscribers (active / ready for
        the next email) plus click intel gathered by the courier beacon."""
        kw = (keyword or "").strip().lower()
        with _lock:
            conn = _db()
            active = conn.execute(
                "SELECT COUNT(*) c FROM subscribers WHERE unsubscribed=0 AND confirmed=1 "
                "AND lower(keyword)=?", (kw,)).fetchone()["c"]
            ready = conn.execute(
                "SELECT COUNT(*) c FROM subscribers WHERE unsubscribed=0 AND confirmed=1 "
                "AND sent_index < ? AND lower(keyword)=?",
                (mailer.SEQUENCE_LENGTH, kw)).fetchone()["c"]
            clicks = conn.execute(
                "SELECT COUNT(*) c FROM clicks WHERE slug=?", (slug,)).fetchone()["c"]
            top = conn.execute(
                "SELECT asin, COUNT(*) c FROM clicks WHERE slug=? AND asin!='' "
                "GROUP BY asin ORDER BY c DESC LIMIT 1", (slug,)).fetchone()
            conn.close()
        return {"active": active, "ready": ready}, clicks, (dict(top) if top else None)

    def _tools(self, q):
        items, keyword = self._best_for_tools(q)
        return self._send(200, self._workbench_payload(keyword, items))

    def _tools_launch(self):
        """One-click marketing for a saved niche: compile every asset, warm the
        lead-magnet ebook, ping IndexNow for the review + landing URLs, and return
        the full workbench payload plus a launch summary. Never blocks on the
        publish ping and never raises."""
        body = self._body()
        keyword = str(body.get("keyword") or "").strip()
        items = []
        for n in self._all_niches():
            if n["keyword"].lower() == keyword.lower():
                items = n["products"]
                break
        if not keyword or not items:
            return self._send(404, {"error": "no saved niche matches that keyword"})
        cached = self._ebook_for(keyword) is not None
        try:
            slug = seo._slugify(keyword)
        except Exception:
            slug = "niche"
        self._fire_indexnow(["/n/" + slug, "/lp/" + slug])
        payload = self._workbench_payload(keyword, items)
        payload["launched"] = {
            "keyword": keyword, "landing": True,
            "ebook_cached": cached, "indexnow_queued": True,
            "emails_ready": payload["stats"]["subscribers_ready"],
            "clicks": payload["stats"]["clicks"],
        }
        return self._send(200, payload)


# ------------------------------------------------------------------ email suite
    def _record_click(self, slug, source="page", referrer="", asin=""):
        ip = security.ip_token(self._client_ip())
        with _lock:
            conn = _db()
            conn.execute(
                "INSERT INTO clicks (slug, source, ip, referrer, asin) VALUES (?,?,?,?,?)",
                (slug, source, ip, referrer, asin))
            conn.commit()
            conn.close()

    def _subscribe(self):
        key = "sub|" + security.client_key(self.headers, self._client_ip())
        if not security.SUBSCRIBE_LIMITER.hit(key):
            return self._send(429, {"ok": False, "error": "Too many signups from this device — try again later."})
        body = self._body()
        email = str(body.get("email") or "").strip().lower()
        if not _EMAIL_RE.match(email):
            return self._send(200, {"ok": False, "error": "That email doesn't look right."})
        if len(email) > 200:
            return self._send(200, {"ok": False, "error": "Email too long."})
        keyword = str(body.get("keyword") or "").strip()[:120]
        first_name = str(body.get("first_name") or "").strip()[:80]
        source = (str(body.get("source") or "niche").strip()[:40]) or "niche"
        with _lock:
            conn = _db()
            row = conn.execute("SELECT id, unsubscribed FROM subscribers WHERE email=?",
                               (email,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE subscribers SET unsubscribed=0, confirmed=1, source=?, keyword=?, first_name=? "
                    "WHERE id=?", (source, keyword, first_name, row["id"]))
                sid = row["id"]
                msg = "You're subscribed again — the next update will find its way to your inbox."
            else:
                cur = conn.execute(
                    "INSERT INTO subscribers (email, source, keyword, first_name, confirmed) "
                    "VALUES (?,?,?,?,1)", (email, source, keyword, first_name))
                sid = cur.lastrowid
                msg = "Done — you'll only hear from us when these picks change, and you can unsubscribe any time."
            conn.commit()
            conn.close()
        return self._send(200, {"ok": True, "id": sid, "message": msg})

    def _unsubscribe(self, q):
        email = (q.get("e") or [""])[0].strip().lower()
        token = (q.get("t") or [""])[0].strip()
        ok = False
        if email and token and security.verify_token(token) == "unsub:" + email:
            with _lock:
                conn = _db()
                conn.execute("UPDATE subscribers SET unsubscribed=1, confirmed=0 WHERE email=?", (email,))
                conn.commit()
                conn.close()
            ok = True
        title = "You're unsubscribed · pstore" if ok else "Unsubscribe link problem · pstore"
        heading = ("<h1>You're unsubscribed.</h1><p>We've stopped keeping your email for these "
                   "updates. No hard feelings — if you ever want the picks again, just sign up "
                   "again on any niche page.</p>") if ok else \
            ("<h1>That link didn't work.</h1><p>It may have expired, or the address doesn't match "
             "what we have on file. Want out anyway? Email us at %s and we'll fix it by hand.</p>"
             % seo._clean(seo.CONTACT_EMAIL))
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{seo._clean(title)}</title>
<link rel="stylesheet" href="/style.css"></head><body>
<header><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a></header>
<main><section class="card">{heading}
<p class="hint" style="margin-top:14px"><a href="/">← back to pstore</a></p></section></main>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _track_click(self):
        key = "trk|" + security.client_key(self.headers, self._client_ip())
        if not security.TRACK_LIMITER.hit(key):
            return self._send(200, {"ok": True})  # silently drop once throttled
        parsed = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        body = self._body()
        slug = str(body.get("slug") or (q.get("slug") or ["page"])[0]).strip().lower()[:120] or "page"
        source = str(body.get("source") or (q.get("source") or ["page"])[0]).strip()[:40] or "page"
        referrer = str(body.get("referrer") or (q.get("referrer") or [""])[0]).strip()[:250]
        asin = str(body.get("asin") or (q.get("asin") or [""])[0]).strip().upper()[:40]
        self._record_click(slug, source, referrer, asin)
        return self._send(200, {"ok": True})

    def _subs_stats(self, conn):
        return {
            "total": conn.execute("SELECT COUNT(*) c FROM subscribers").fetchone()["c"],
            "active": conn.execute(
                "SELECT COUNT(*) c FROM subscribers WHERE confirmed=1 AND unsubscribed=0").fetchone()["c"],
            "unsubscribed": conn.execute(
                "SELECT COUNT(*) c FROM subscribers WHERE unsubscribed=1").fetchone()["c"],
            "emails_sent": conn.execute("SELECT COUNT(*) c FROM sent_emails").fetchone()["c"],
        }

    def _subscribers_json(self):
        with _lock:
            conn = _db()
            stats = self._subs_stats(conn)
            rows = conn.execute("SELECT * FROM subscribers ORDER BY id DESC LIMIT 200").fetchall()
            conn.close()
        return self._send(200, {"stats": stats, "subscribers": [dict(r) for r in rows]})

    def _sequence_send(self):
        body = self._body()
        dry = bool(body.get("dry_run"))
        niche_kw = str(body.get("keyword") or "").strip().lower()
        if not mailer.configured():
            return self._send(200, {"ok": False, "error": "SMTP is not configured — set SMTP_HOST/USER/PASSWORD.",
                                    "sent": 0, "errors": 0, "ready": 0, "skipped": 0, "limit": 0})
        with _lock:
            conn = _db()
            if niche_kw:
                subs = conn.execute(
                    "SELECT * FROM subscribers WHERE unsubscribed=0 AND confirmed=1 "
                    "AND sent_index < ? AND lower(keyword)=? ORDER BY id",
                    (mailer.SEQUENCE_LENGTH, niche_kw)).fetchall()
            else:
                subs = conn.execute(
                    "SELECT * FROM subscribers WHERE unsubscribed=0 AND confirmed=1 "
                    "AND sent_index < ? ORDER BY id", (mailer.SEQUENCE_LENGTH,)).fetchall()
            niche_map = {r["keyword"].strip().lower(): r
                         for r in conn.execute("SELECT keyword, products FROM niches")}
            conn.close()
        ready = []
        unready = 0
        for sub in subs:
            kw = (sub["keyword"] or "").strip()
            item_row = niche_map.get(kw.lower())
            items = json.loads(item_row["products"] or "[]") if item_row else []
            idx = (sub["sent_index"] or 0) + 1
            mail = mailer.next_email(kw, items, idx)
            if not mail:
                unready += 1
                continue
            ready.append((sub["id"], idx, mail, sub["email"], sub["first_name"] or "there"))
        if dry:
            return self._send(200, {"ok": True, "dry_run": True, "sent": 0, "errors": 0,
                                    "ready": len(ready), "skipped": unready,
                                    "keyword": niche_kw or None,
                                    "limit": mailer.MAX_EMAILS_PER_RUN})
        sent = errors = 0
        for sid, idx, mail, to, to_name in ready:
            if sent + errors >= mailer.MAX_EMAILS_PER_RUN:
                break
            text = mailer.render_body(mail, to_name=to_name, email=to)
            if mailer.send(mail["subject"], text, to):
                sent += 1
                with _lock:
                    conn = _db()
                    conn.execute("UPDATE subscribers SET sent_index=? WHERE id=?", (idx, sid))
                    conn.execute("INSERT INTO sent_emails (subscriber_id, email_index, subject) "
                                 "VALUES (?,?,?)", (sid, idx, mail["subject"]))
                    conn.commit()
                    conn.close()
            else:
                errors += 1
        return self._send(200, {"ok": True, "sent": sent, "errors": errors,
                                "ready": len(ready), "skipped": unready,
                                "keyword": niche_kw or None,
                                "limit": mailer.MAX_EMAILS_PER_RUN})

    def _subs_table(self, rows):
        def state(r):
            if r["unsubscribed"]:
                return "<span class='badge'>out</span>"
            if not r["confirmed"]:
                return "<span class='badge'>unconfirmed</span>"
            return "<span class='badge'>active</span>"
        trs = "".join(
            "<tr><td>%s</td><td>%s</td><td>%d / %d</td><td>%s</td><td>%s</td></tr>"
            % (seo._clean(r["email"]), seo._clean(r["keyword"] or "—"),
               r["sent_index"] or 0, mailer.SEQUENCE_LENGTH,
               state(r), seo._clean(r["created_at"] or ""))
            for r in rows)
        return trs

    def _email_sequence_preview(self, keyword):
        for n in self._all_niches():
            if n["keyword"].lower() == keyword.lower():
                items = n["products"]
                break
        else:
            return '<p class="hint">No saved products for that niche yet — mine one on the dashboard first.</p>'
        seq = market_engine.build_email_sequence(keyword, items)
        if not seq:
            return '<p class="hint">No sequence for this niche right now (needs at least one product).</p>'
        cards = "".join(
            '<div class="sub"><h3>%s</h3><p class="key" style="white-space:pre-wrap">%s</p></div>'
            % (seo._clean(m["name"]), seo._clean(mailer.render_body(m, email="you@example.com"))[:1600])
            for m in seq)
        return cards

    def _admin_emails(self, q):
        with _lock:
            conn = _db()
            stats = self._subs_stats(conn)
            rows = conn.execute("SELECT * FROM subscribers ORDER BY id DESC LIMIT 200").fetchall()
            conn.close()
        niches = [n["keyword"] for n in self._all_niches()]
        seq_kw = (q.get("keyword") or [""])[0].strip() or (niches[0] if niches else "")
        seq_html = self._email_sequence_preview(seq_kw) if seq_kw else \
            '<p class="hint">No saved niches yet — mine one on the dashboard to preview the sequence.</p>'
        opts = "".join('<option value="%s"%s>%s</option>' % (urllib.parse.quote(k),
                        ' selected' if k == seq_kw else "", seo._clean(k)) for k in niches)
        smtp_state = ("Configured · %s" % seo._clean(mailer.SMTP_HOST)) if mailer.configured() \
            else ("<b>Not configured.</b> Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD (and optionally "
                  "SMTP_PORT / SMTP_FROM / SMTP_STARTTLS) in the environment to actually send.")
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Emails — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
</head><body>
<header><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>Email <span>capture &amp; auto-send.</span></h1>
<p class="tagline">The opt-in widget on every niche page feeds this list. Send the next email in the 5-step buyer sequence to active subscribers.</p></div>
{self._admin_nav('emails')}
</header>
<main>
<section class="card"><h2>📨 Sender status</h2>
<p class="hint" style="margin-top:-4px">{smtp_state}</p>
<div class="row">
  <button id="send" class="warm">Send next batch (up to {mailer.MAX_EMAILS_PER_RUN})</button>
  <label style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" id="dry" style="width:auto;height:auto" checked> dry run</label>
</div>
<p id="out" class="msg"></p></section>
<section class="card"><h2>👥 Subscribers</h2>
<p class="hint">{stats['total']} total · {stats['active']} active · {stats['unsubscribed']} unsubscribed · {stats['emails_sent']} emails sent</p>
<table class="plain"><thead><tr><th>Email</th><th>Niche</th><th>Sequence</th><th>State</th><th>Joined</th></tr></thead>
<tbody>{self._subs_table(rows)}</tbody></table></section>
<section class="card"><h2>💌 Sequence preview</h2>
<form class="row" method="get" action="/admin/emails">
  <label>Niche <select name="keyword" onchange="this.form.submit()">{opts}</select></label>
</form>
{seq_html}</section>
</main>
<footer><p>Emails only ever use real scraped data (title, price, stars, reviews) plus {{first_name}} — and always carry an unsubscribe link.</p></footer>
<script>
function $(id){{return document.getElementById(id);}}
$("send").onclick = async () => {{
  $("out").textContent = "Working…";
  const r = await fetch("/api/sequence/send", {{method:"POST", headers:{{"Content-Type":"application/json"}},
    body: JSON.stringify({{dry_run: $("dry").checked}})}});
  const d = await r.json().catch(()=>({{ok:false, error:"bad response"}}));
  if (d && d.ok) {{
    if (d.dry_run) $("out").textContent = "Dry run: " + d.ready + " subscriber(s) ready for the next email.";
    else $("out").textContent = "Sent " + d.sent + " · skipped " + d.skipped + " · errors " + d.errors + " · (ready " + d.ready + ").";
  }} else $("out").textContent = (d && d.error) || "Send failed.";
  setTimeout(()=>location.reload(), 1500);
}};
</script>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _ebook_for(self, keyword):
        keyword = (keyword or "").strip()
        if not keyword:
            return None
        if keyword in _EBOOKS:
            return _EBOOKS[keyword]
        niche = None
        for n in self._all_niches():
            if n["keyword"].lower() == keyword.lower():
                niche = n
                break
        if not niche or not niche.get("products"):
            return None
        book = ebook_mod.build_ebook(keyword)
        if len(_EBOOKS) >= 20:
            try:
                _EBOOKS.pop(next(iter(_EBOOKS)))
            except StopIteration:
                pass
        _EBOOKS[keyword] = book
        return book

    def _admin_ebooks(self, q):
        niches = [n["keyword"] for n in self._all_niches()]
        keyword = (q.get("keyword") or [""])[0].strip()
        book = None
        if keyword:
            book = self._ebook_for(keyword)
        elif niches:
            keyword = niches[0]
            book = self._ebook_for(keyword)
        opts = "".join('<option value="%s"%s>%s</option>' % (seo._clean(k),
                        ' selected' if k == keyword else "", seo._clean(k)) for k in niches)
        content = ""
        if book:
            content = ('<div class="book-preview"><p class="hint" style="margin-top:-4px">%s</p>'
                       '%s<p style="margin-top:12px"><a class="btn warm" download '
                       'href="/admin/ebooks/pdf?keyword=%s">Download PDF ⬇</a></p></div>'
                       % (seo._clean(book["title"]), book["html_preview"],
                          urllib.parse.quote(keyword)))
        else:
            content = '<p class="hint">Choose a niche and generate its free guide PDF.</p>'
        cached = "".join('<a class="chip" href="/admin/ebooks?keyword=%s">%s</a>'
                         % (urllib.parse.quote(k), seo._clean(k)) for k in _EBOOKS)
        _providers = ai.providers()
        _active = ai.active_provider()
        prov_opts = "".join(
            '<option value="%s"%s>%s%s</option>'
            % (p["name"], ' selected' if p["name"] == _active else "",
               seo._clean(p["label"]), " · in use" if p["active"] else "")
            for p in _providers)
        if _active:
            for p in _providers:
                if p["name"] == _active:
                    ai_status_line = ('<span style="color:#1e8e3e"><b>%s</b> (%s)</span> — %s'
                                      % (seo._clean(p["label"]), seo._clean(p["model"]),
                                         "runtime key (resets on redeploy)"
                                         if p["source"] == "runtime" else "server env key"))
                    break
        else:
            ai_status_line = ('<span style="color:#d64545">not configured</span> — templates '
                              + "are being used. Add a key below to enable AI copy.")
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ebooks — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
</head><body>
<header><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>AI ebook <span>lead magnets.</span></h1>
<p class="tagline">Turn any saved niche into a designed PDF guide in one click — pure stdlib renderer, template copy (AI when a key is set).</p></div>
{self._admin_nav('ebooks')}
</header>
<main>
<section class="card"><h2>📕 Generate a guide</h2>
<form class="row" method="get" action="/admin/ebooks">
  <label>Niche <select name="keyword">{opts}</select></label>
  <button class="warm" type="submit">Generate</button>
</form>
{content}
</section>
<section class="card"><h2>🤖 AI provider</h2>
<p class="hint" style="margin-top:-4px">Free models: OpenCode Zen, Mistral Experiment tier and NVIDIA NIM — plus your OpenAI key. Paste a key, hit <b>Test</b>, then <b>Use</b> to generate with it.</p>
<form class="ai-form" onsubmit="return false">
  <div class="row">
    <label>Provider <select id="ai-provider" name="provider" style="min-width:230px">{prov_opts}</select></label>
    <label>API key <input id="ai-key" name="api_key" type="password" autocomplete="off" placeholder="paste key — never stored on disk"></label>
  </div>
  <div class="row">
    <label>Model <input id="ai-model" name="model" list="ai-models" placeholder="e.g. kimi-k2.5-free"></label>
    <label>Base URL <input id="ai-base" name="base_url" placeholder="optional override"></label>
  </div>
  <datalist id="ai-models"></datalist>
  <div class="row" style="gap:8px">
    <button class="warm" type="button" id="ai-test">Test key ✓</button>
    <button type="button" id="ai-models-btn">Load models</button>
    <button type="button" id="ai-use">Use this provider</button>
  </div>
  <p class="ai-msg" id="ai-out" style="margin-top:10px">Status: {ai_status_line}</p>
  <p class="hint" style="margin-top:8px">Keys live in this running process only and reset on redeploy. For permanence set the matching env var instead: <code>AI_API_KEY</code> / <code>OPENCODE_API_KEY</code> / <code>MISTRAL_API_KEY</code> / <code>NVIDIA_API_KEY</code>.</p>
</form>
</section>
<section class="card"><h2>🗂 Recently generated</h2>
<div class="chips">{cached if cached else '<span class="hint">Nothing generated yet.</span>'}</div>
<p class="hint" style="margin-top:10px">
AI status: {"<b>configured</b> (%s · %s)" % (seo._clean(_active), seo._clean(ai.model_for(_active))) if _active else "not configured — using the deterministic template copy."}
</p></section>
</main>
<footer><p>PDFs are generated server-side and never contain affiliate links — plain honest guide content that pairs with your review pages.</p></footer>
<script>
(function(){{
  var sel=document.getElementById('ai-provider'), key=document.getElementById('ai-key'),
      model=document.getElementById('ai-model'), base=document.getElementById('ai-base'),
      dl=document.getElementById('ai-models'), out=document.getElementById('ai-out');
  function say(t, ok){{ out.textContent=t; out.style.color = ok ? '#1e8e3e' : '#d64545'; }}
  function str(v){{ return (v==null ? '' : String(v)).trim(); }}
  function payload(){{
    return {{provider: sel.value, api_key: str(key.value), model: str(model.value), base_url: str(base.value)}};
  }}
  async function post(path, extra){{
    var body = payload(); for (var k in (extra||{{}})) body[k]=extra[k];
    var r = await fetch(path, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}});
    return r.json();
  }}
  document.getElementById('ai-test').onclick = async function(){{
    if(!str(key.value)){{ say('✗ Paste a key first.', false); return; }}
    out.textContent='Testing…'; out.style.color='#666';
    var d = await post('/api/ai/test');
    if(d.ok) say('✓ Valid — '+d.provider+' / '+d.model+' replied "'+d.reply+'" in '+d.latency_ms+'ms', true);
    else say('✗ Failed: '+(d.error||'unknown error'), false);
  }};
  document.getElementById('ai-models-btn').onclick = async function(){{
    if(!str(key.value)){{ say('✗ Paste a key first, then load models.', false); return; }}
    out.textContent='Loading models…'; out.style.color='#666';
    var d = await post('/api/ai/models');
    dl.innerHTML='';
    var ids = d.models||[];
    if(!ids.length){{ say('No models returned — check the key.', false); return; }}
    ids.forEach(function(id){{ var o=document.createElement('option'); o.value=id; dl.appendChild(o); }});
    model.placeholder='pick from '+ids.length+' models';
    say('Loaded '+ids.length+' models.', true);
  }};
  document.getElementById('ai-use').onclick = async function(){{
    if(!str(key.value)){{ say('✗ Paste a key first.', false); return; }}
    out.textContent='Saving…'; out.style.color='#666';
    var d = await post('/api/ai/config');
    if(d.ok){{ say('✓ '+d.provider+' is now active ('+d.model+'). Regenerating cache…', true);
              setTimeout(function(){{location.reload();}}, 1000); }}
    else say('✗ '+(d.error||'failed'), false);
  }};
}})();
</script>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _ebook_pdf(self, q):
        keyword = (q.get("keyword") or [""])[0].strip()
        book = self._ebook_for(keyword)
        if not book:
            return self._send(404, {"error": "ebook not found"})
        data = book["pdf"]
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % book["pdf_name"])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        return None

    def _admin_analytics(self):
        with _lock:
            conn = _db()
            stats = self._subs_stats(conn)
            total = conn.execute("SELECT COUNT(*) c FROM clicks").fetchone()["c"]
            top_slugs = conn.execute(
                "SELECT slug, COUNT(*) c FROM clicks GROUP BY slug ORDER BY c DESC LIMIT 12").fetchall()
            top_sources = conn.execute(
                "SELECT source, COUNT(*) c FROM clicks GROUP BY source ORDER BY c DESC LIMIT 8").fetchall()
            top_products = conn.execute(
                "SELECT asin, slug, COUNT(*) c FROM clicks WHERE asin != '' "
                "GROUP BY asin, slug ORDER BY c DESC LIMIT 10").fetchall()
            recent = conn.execute("SELECT * FROM clicks ORDER BY id DESC LIMIT 15").fetchall()
            conn.close()
        slug_rows = "".join(
            "<tr><td>%s</td><td class='ct'>%s</td></tr>" % (seo._clean(r["slug"]), r["c"])
            for r in top_slugs) or "<tr><td colspan='2' class='hint'>No clicks yet.</td></tr>"
        src_rows = "".join(
            "<tr><td>%s</td><td>%d</td></tr>" % (seo._clean(r["source"]), r["c"])
            for r in top_sources)
        product_rows = "".join(
            "<tr><td>%s</td><td>%s</td><td class='ct'>%s</td></tr>"
            % (seo._clean(r["asin"]), seo._clean(r["slug"]), r["c"])
            for r in top_products) or "<tr><td colspan='3' class='hint'>No product clicks yet — they appear as soon as a pick gets tapped (data-asin beacon).</td></tr>"
        recent_rows = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (seo._clean(r["slug"]), seo._clean(r["source"]), seo._clean(r["referrer"] or "—"),
               seo._clean(r["created_at"] or "—"))
            for r in recent) or "<tr><td colspan='4' class='hint'>No clicks yet — they're recorded when visitors tap Amazon links on the pages.</td></tr>"
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analytics — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
</head><body>
<header><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>Click <span>analytics.</span></h1>
<p class="tagline">Privacy-first: IPs are stored as one-way hashes, never raw. See which niches and sources actually earn clicks.</p></div>
{self._admin_nav('analytics')}
</header>
<main>
<section class="card"><h2>📊 At a glance</h2>
<div class="row" style="align-items:stretch">
  <div class="feature"><h3>{total}</h3><p class="hint">total clicks</p></div>
  <div class="feature"><h3>{len(top_slugs)}</h3><p class="hint">niches clicked</p></div>
  <div class="feature"><h3>{stats['active']}</h3><p class="hint">active subscribers</p></div>
  <div class="feature"><h3>{stats['emails_sent']}</h3><p class="hint">emails sent</p></div>
</div></section>
<section class="card"><h2>🛒 Top clicked pages</h2>
<table class="plain"><thead><tr><th>Niche</th><th>Clicks</th></tr></thead><tbody>{slug_rows}</tbody></table></section>
<section class="card"><h2>🏆 Most-clicked products</h2>
<p class="hint">Which ASIN earns the taps, per niche — use it to pick which products to push in emails and which pages deserve more variants.</p>
<table class="plain"><thead><tr><th>ASIN</th><th>Niche</th><th>Clicks</th></tr></thead><tbody>{product_rows}</tbody></table></section>
<section class="card"><h2>📥 By source</h2>
<table class="plain"><thead><tr><th>Source</th><th>Clicks</th></tr></thead><tbody>{src_rows}</tbody></table></section>
<section class="card"><h2>🧾 Recent clicks</h2>
<table class="plain"><thead><tr><th>Niche</th><th>Source</th><th>Referrer</th><th>When</th></tr></thead><tbody>{recent_rows}</tbody></table></section>
</main>
<footer><p>Clicks are counted server-side when a visitor's browser pings /api/track before Amazon loads. Referrers are truncated; raw IPs are never stored.</p></footer>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")


def main():
    _init()
    if not _ADMIN_EMAIL_FROM_ENV or not _ADMIN_PW_FROM_ENV:
        print("WARNING: PSTORE_ADMIN_EMAIL / PSTORE_ADMIN_PASSWORD not both set — "
              "using the default admin credentials. Set them in Render/env before going public.")
    if not oauth.providers_configured():
        print("NOTE: Google/Facebook OAuth login disabled — set OAUTH_GOOGLE_CLIENT_ID/SECRET "
              "or OAUTH_FACEBOOK_APP_ID/APP_SECRET to enable it.")
    if not mailer.configured():
        print("NOTE: SMTP not configured — /subscribe captures and /admin/emails previews work, "
              "but sequence sends are refused. Set SMTP_HOST/USER/PASSWORD to enable sending.")
    if not ai.configured():
        print("NOTE: AI not configured — ebook/headline copy uses deterministic templates. "
              "Set AI_API_KEY, OPENCODE_API_KEY, MISTRAL_API_KEY or NVIDIA_API_KEY (or add a "
              "key in /admin/ebooks) to enable generation.")
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

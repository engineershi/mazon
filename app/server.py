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
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import amazon
import ai
import cms as cms_mod
import cms_render
import ebook as ebook_mod
import indexnow
import mailer
import manual
import market_engine
import niche
import oauth
import seo
import security
import sem
import social

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
_SOCIAL_WEBHOOK = os.environ.get("SOCIAL_WEBHOOK", "")  # optional real-posting hook

_TOTOP = ('<div class="totop"><a href="#top" aria-label="Back to top">&uarr;</a></div>'
          '<script src="/ui.js" defer></script>')

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

_lock = threading.Lock()

# --- niche data refresh -------------------------------------------------------
# Manual refresh re-mines one/all saved niches so prices, ratings and stock
# stay current. An automatic background loop refreshes "stale" niches on a
# schedule (a niche is stale when it hasn't been refreshed in N minutes) and
# throttles to a few per cycle so it never hammers Amazon.
_REFRESH_STALE_MIN = int(os.environ.get("PSTORE_REFRESH_MIN", "1440"))      # 24h
_REFRESH_INTERVAL_SEC = int(os.environ.get("PSTORE_REFRESH_INTERVAL", "3600"))  # 1h
_REFRESH_MAX_PER_CYCLE = int(os.environ.get("PSTORE_REFRESH_MAX", "3"))
_REFRESHING = set()  # keyword -> in-flight guard so a niche isn't double-mined
_refresh_lock = threading.Lock()


def _refresh_stale_candidates(now):
    """Return keywords of saved niches that are stale (never refreshed or past
    their staleness window), oldest-first, capped to a small batch."""
    with _lock:
        conn = _db()
        rows = conn.execute(
            "SELECT keyword, updated_at FROM niches ORDER BY "
            "CASE WHEN updated_at IS NULL THEN 0 ELSE 1 END, updated_at ASC").fetchall()
        conn.close()
    out = []
    for r in rows:
        kw = r["keyword"]
        with _refresh_lock:
            if kw in _REFRESHING:
                continue
        if len(out) >= _REFRESH_MAX_PER_CYCLE:
            break
        updated = r["updated_at"]
        if not updated:
            out.append(kw)
            continue
        try:
            parsed = time.mktime(time.strptime(updated[:19], "%Y-%m-%d %H:%M:%S"))
        except (ValueError, TypeError):
            out.append(kw)
            continue
        if now - parsed >= _REFRESH_STALE_MIN * 60:
            out.append(kw)
    return out


def _refresh_niche(keyword):
    """Re-mine a single saved niche in place. Returns a dict with the updated
    data, or None if the keyword isn't a saved niche. Never raises: on failure
    returns a dict with 'error' set."""
    with _refresh_lock:
        if keyword in _REFRESHING:
            return {"status": "busy", "error": "already refreshing"}
        _REFRESHING.add(keyword)
    try:
        with _lock:
            conn = _db()
            row = conn.execute("SELECT keyword FROM niches WHERE keyword=?",
                               (keyword,)).fetchone()
            conn.close()
        if not row:
            return {"status": "missing", "error": "niche not found"}
        try:
            data = niche.refresh_keyword(keyword)
        except Exception as exc:  # network/provider hiccup — don't crash
            return {"status": "error", "error": str(exc)}
        with _lock:
            conn = _db()
            conn.execute(
                "UPDATE niches SET products=?, score=?, saturation=?, updated_at=datetime('now') "
                "WHERE keyword=?",
                (json.dumps(data.get("products") or []),
                 data.get("score"), data.get("saturation"), keyword))
            conn.commit()
            conn.close()
        return {"status": "ok", "keyword": keyword,
                "products": len(data.get("products") or []),
                "score": data.get("score"), "saturation": data.get("saturation")}
    finally:
        with _refresh_lock:
            _REFRESHING.discard(keyword)


def _auto_refresh_loop():
    """Daemon that periodically refreshes stale niches in small batches. Runs on
    an interval; a zero interval disables automatic refreshing."""
    while True:
        time.sleep(max(_REFRESH_INTERVAL_SEC, 300))
        if _REFRESH_INTERVAL_SEC <= 0:
            continue
        try:
            now = time.time()
            for kw in _refresh_stale_candidates(now):
                _refresh_niche(kw)
        except Exception:
            pass


def _refresh_all_worker(kws):
    """Background worker for a manual 'refresh all' — re-mines every saved
    niche in place so the request returns immediately and the admin page can
    poll status. Never raises."""
    for kw in (kws or []):
        try:
            _refresh_niche(kw)
        except Exception:
            pass


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
    conn.execute("""CREATE TABLE IF NOT EXISTS social_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL,
        keyword TEXT NOT NULL,
        platform TEXT NOT NULL,
        name TEXT,
        body TEXT,
        link TEXT,
        utm_content TEXT,
        status TEXT DEFAULT 'draft',
        created_at TEXT DEFAULT (datetime('now')),
        published_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS boosts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL,
        keyword TEXT NOT NULL,
        name TEXT NOT NULL,
        script TEXT,
        link TEXT,
        utm_content TEXT,
        runs INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL DEFAULT 'page',
        page TEXT,
        name TEXT NOT NULL,
        keyword TEXT,
        source TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    try:
        conn.execute("ALTER TABLE clicks ADD COLUMN content TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
    try:
        conn.execute("ALTER TABLE clicks ADD COLUMN asin TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
    try:
        conn.execute("ALTER TABLE niches ADD COLUMN updated_at TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
    cms_mod.ensure_tables(conn)
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
{chip('/admin/social', '📣 Social', 'social')}
{chip('/admin/sem', '🎯 SEM', 'sem')}
{chip('/admin/seo', '🔍 SEO', 'seo')}
{chip('/admin/manual', '📖 Manual', 'manual')}
{chip('/admin/refresh', '📡 Refresh', 'refresh')}
{chip('/admin/cms', '🧩 CMS', 'cms')}
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
                 btn("/admin/analytics", "📊 Click tracking & analytics", "beacons"),
                 btn("/admin/social", "📣 Social publishing", "tracked posts"),
                 btn("/admin/sem", "🎯 Search funnel (SEM)", "long-tail growth"),
                 btn("/admin/seo", "🔍 SEO audit", "indexability + schema"),
                 btn("/admin/manual", "📖 User manual", "visual + PDF guide"),
                 btn("/admin/cms", "🧩 Lead page CMS", "edit sections &amp; style"),
                 btn("/admin/refresh", "📡 Data refresh", "manual + auto re-mine")]
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
                                     "/api/indexnow", "/api/subscribers", "/api/sequence/send",
                                     "/api/social", "/api/social/publish",
                                     "/api/sem", "/api/seo-audit"))
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
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
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
{_TOTOP}
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
            if path == "/api/pageview":
                return self._page_view()
            if path == "/_gated/pdf":
                return self._gated_pdf(q)
            if self._needs_admin(path) and not self._authed():
                if path.startswith("/api/"):
                    return self._send(401, {"error": "unauthorized", "auth": False})
                return self._redirect_login(path or "/dashboard")
            # SEO + marketing routes first (crawlable + short links)
            if path == "/robots.txt":
                return self._send(200, seo.render_robots(), "text/plain; charset=utf-8")
            if path == "/sitemap.xml":
                return self._send(200, self._sitemap(), "application/xml; charset=utf-8")
            if path == "/blog":
                return self._send(200, seo.render_blog(self._all_niches()), "text/html; charset=utf-8")
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
            post = re.match(r"^/social/([a-z0-9-]+)/([A-Za-z0-9]+)$", path)
            if post:
                return self._social_post_page(post.group(1), post.group(2))
            og = re.match(r"^/og/([a-z0-9-]+)$", path)
            if og:
                return self._og_image(og.group(1))
            go = re.match(r"^/go/([A-Z0-9]{10})$", path)
            if go:
                return self._go(go.group(1))
            if path == "/tool":
                with open(os.path.join(STATIC, "tool.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
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
            if path == "/admin/social":
                return self._admin_social(q)
            if path == "/admin/sem":
                return self._admin_sem(q)
            if path == "/admin/seo":
                return self._admin_seo()
            if path == "/admin/manual":
                return self._admin_manual()
            if path == "/admin/manual.pdf":
                return self._admin_manual_pdf()
            if path == "/admin/cms":
                return self._admin_cms(q)
            if path == "/api/cms/pages":
                return self._cms_pages_api()
            cms_page = re.match(r"^/api/cms/pages/([0-9]+)(/sections)?$", path)
            if cms_page:
                return self._cms_page_api(cms_page, q)
            if path == "/admin/refresh":
                return self._admin_refresh(q)
            if path == "/api/sem":
                return self._sem_api(q)
            if path == "/api/seo-audit":
                return self._seo_audit_api()
            if path == "/api/social":
                return self._social_api(q)
            if path == "/keys":
                return self._keys_page()
            key_group = re.match(r"^/keys/([a-z0-9_-]+)/([a-z0-9_.-]+)$", path)
            if key_group:
                return self._keys_managed_page(key_group.group(1), key_group.group(2))
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
                with open(os.path.join(STATIC, "app.js"), "rb") as fh:
                    return self._send(200, fh.read(), "application/javascript; charset=utf-8")
            if path == "/style.css":
                with open(os.path.join(STATIC, "style.css"), "rb") as fh:
                    return self._send(200, fh.read(), "text/css; charset=utf-8")
            if path == "/courier.js":
                with open(os.path.join(STATIC, "courier.js"), "rb") as fh:
                    return self._send(200, fh.read(),
                                      "application/javascript; charset=utf-8")
            if path == "/table-flow.js":
                with open(os.path.join(STATIC, "table-flow.js"), "rb") as fh:
                    return self._send(200, fh.read(),
                                      "application/javascript; charset=utf-8")
            if path == "/ui.js":
                with open(os.path.join(STATIC, "ui.js"), "rb") as fh:
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
            if path == "/api/refresh/status":
                return self._refresh_status()
            if path == "/api/subscribers":
                return self._subscribers_json()
            if path == "/api/tools":
                return self._tools(q)
            if path == "/api/boosts":
                return self._boosts_api(q)
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
            if parsed.path == "/api/pageview":
                return self._page_view()
            if parsed.path.startswith("/api/") and not self._authed():
                return self._send(401, {"error": "unauthorized", "auth": False})
            if parsed.path == "/api/niches":
                return self._save_niche()
            if parsed.path == "/api/refresh":
                return self._refresh_post()
            if parsed.path == "/api/refresh-all":
                return self._refresh_all_post()
            if parsed.path == "/api/settings":
                return self._save_settings()
            if parsed.path == "/api/indexnow":
                return self._indexnow_post()
            if parsed.path == "/api/sequence/send":
                return self._sequence_send()
            if parsed.path == "/api/social/publish":
                return self._social_publish()
            if parsed.path == "/api/tools/launch":
                return self._tools_launch()
            if parsed.path == "/api/boosts/run":
                return self._run_boosts()
            if parsed.path == "/api/boosts/social":
                return self._boosts_to_social()
            if parsed.path == "/api/cms/page":
                return self._cms_save_page()
            if parsed.path == "/api/cms/preset":
                return self._cms_apply_preset()
            if parsed.path == "/api/cms/generate":
                return self._cms_generate()
            cms_section = re.match(r"^/api/cms/pages/([0-9]+)/sections/([0-9]+)$", parsed.path)
            if cms_section:
                return self._cms_update_section(cms_section.group(1), cms_section.group(2))
            if parsed.path == "/api/subscribers":
                return self._subscribers_json()
            if parsed.path == "/api/ai/test":
                return self._ai_test()
            if parsed.path == "/api/keys/test":
                return self._keys_test()
            if parsed.path == "/api/keys/save":
                return self._keys_save()
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

    # -------------------------------------------------- Niche data refresh
    def _refresh_status(self):
        with _lock:
            conn = _db()
            total = conn.execute("SELECT COUNT(*) c FROM niches").fetchone()["c"]
            rows = conn.execute("SELECT updated_at FROM niches").fetchall()
            conn.close()
        now = time.time()
        refreshed = stale = 0
        for r in rows:
            u = r["updated_at"]
            if not u:
                stale += 1
                continue
            refreshed += 1
            try:
                parsed = time.mktime(time.strptime(u[:19], "%Y-%m-%d %H:%M:%S"))
            except (ValueError, TypeError):
                stale += 1
                continue
            if now - parsed >= _REFRESH_STALE_MIN * 60:
                stale += 1
        with _refresh_lock:
            inflight = sorted(_REFRESHING)
        return self._send(200, {
            "total": total, "refreshed": refreshed, "stale": stale,
            "inflight": inflight,
            "auto_interval_s": _REFRESH_INTERVAL_SEC,
            "stale_min": _REFRESH_STALE_MIN,
            "max_per_cycle": _REFRESH_MAX_PER_CYCLE})

    def _refresh_post(self):
        body = self._body() or {}
        kw = (body.get("keyword") or "").strip()
        if not kw:
            return self._send(400, {"error": "keyword required"})
        return self._send(200, _refresh_niche(kw))

    def _refresh_all_post(self):
        with _lock:
            conn = _db()
            rows = conn.execute("SELECT keyword FROM niches").fetchall()
            conn.close()
        kws = [r["keyword"] for r in rows]
        threading.Thread(target=_refresh_all_worker, args=(kws,), daemon=True).start()
        return self._send(200, {"status": "started", "queued": len(kws)})

    # ------------------------------------------------------------------ SEO
    def _all_niches(self):
        with _lock:
            conn = _db()
            rows = conn.execute("SELECT keyword, products FROM niches").fetchall()
            conn.close()
        return [{"keyword": r["keyword"], "products": json.loads(r["products"] or "[]")}
                for r in rows]

    def _sitemap(self):
        entries = [("/", "2026-08-28"), ("/blog", "2026-08-28")]
        for page in seo.STATIC_PAGES:
            entries.append(("/" + page, "2026-08-28"))
        with _lock:
            conn = _db()
            rows = conn.execute("SELECT keyword, created_at FROM niches").fetchall()
            conn.close()
        for r in rows:
            try:
                kw = seo._slugify(r["keyword"])
            except Exception:
                kw = "niche"
            lm = (r["created_at"] or "")[:10] or "2026-08-28"
            entries.append((f"/n/{kw}", lm))
            entries.append((f"/lp/{kw}", lm))
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
                    # Try CMS render first; fall back to the legacy template.
                    html = self._cms_landing_html(n)
                    if html is None:
                        html = market_engine.build_landing_page(
                            n["keyword"], n["products"], site_url=seo.BASE_URL)
                    return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            except Exception:
                continue
        return self._send(404, b"<html><body><p>Landing page not found.</p></body></html>",
                          "text/html; charset=utf-8")

    def _cms_landing_html(self, niche):
        """Render a CMS-driven landing page for a niche. Returns None if CMS
        rendering is disabled for this niche (fall back to legacy)."""
        with _lock:
            conn = _db()
            try:
                page = cms_mod.get_or_create_page(conn, niche["keyword"])
                settings = page.get("settings") or {}
                if settings.get("use_cms", True) is False:
                    return None
                sub_count = conn.execute(
                    "SELECT COUNT(*) c FROM subscribers WHERE unsubscribed=0 AND confirmed=1 "
                    "AND lower(keyword)=?", (niche["keyword"].strip().lower(),)
                ).fetchone()["c"]
                ctx = cms_mod.build_page_context(conn, niche["keyword"], {
                    "products": niche.get("products") or [],
                    "subscriber_count": sub_count,
                })
            finally:
                conn.close()
        return cms_render.render_landing_page_page(
            ctx, niche["keyword"], site_url=seo.BASE_URL)

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
            ("Scraper providers", "%d / %d keyed" % (
                sum(1 for p in amazon.scraper_status()["providers"].values()
                    if p["has_key"]),
                len(amazon.scraper_status()["providers"]))),
        ]
        cards = "".join(
            '<div class="sub"><h3>%s</h3><p class="key" '
            'onclick="navigator.clipboard&&navigator.clipboard.writeText(this.textContent)" '
            'title="Click to copy">%s</p></div>' % (k, v)
            for k, v in rows)
        # Every manage-able key group, each entry linking to its own page + the
        # platform where the real key lives.
        group_html = []
        for g in self._managed_groups():
            entries = "".join(
                '<div class="sub"><h3>%s</h3>'
                '<p class="key"><a href="%s">%s</a> · %s'
                '%s</p></div>'
                % (seo._clean(e["name"]), e["page"], e["page"],
                   seo._clean(e["status"]),
                   (" · <a href='%s' target='_blank' rel='noopener'>⚙ platform ↗</a>" % seo._clean(e["home"]))
                   if e.get("home") else "")
                for e in g["entries"])
            group_html.append(
                '<h2>%s</h2>\n<p class="hint">%s</p>%s' % (g["label"], g["hint"], entries))
        groups = "\n".join(group_html)
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Keys — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
<style>.key{{font-family:ui-monospace,Menlo,monospace;font-size:13px;background:var(--bg);border:1px solid var(--border);
border-radius:10px;padding:10px 12px;margin:0;word-break:break-all;cursor:copy}}
.sub{{padding:8px 0}}.sub h3{{margin:0 0 6px;font-size:13px;color:var(--muted);font-weight:700}}</style>
</head><body>
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>Keys & <span>endpoints.</span></h1>
<p class="tagline">Every key, URL and endpoint the tools need — click a value to copy it, or open a key page to test & save.</p></div>
{self._admin_nav('keys')}
</header>
<main><section class="card"><h2>🔑 Keys & endpoints</h2>{cards}
<h2>🗝 Manage every key</h2>
<p class="hint">Each key gets its own page — see status, open the platform console directly, test the key and save it here:</p>
{groups}
</section></main>
<footer><p>Keys saved here apply immediately and are kept for the running process; set the matching env var to make them permanent across restarts.</p></footer>
{_TOTOP}
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
<div class="sub"><h3>Test the key</h3>
<p class="hint">Fires a real, free request to {meta['name']} — the provider confirms the key is accepted, with zero search quota used.</p>
<button id="test" class="warm">▶ Test key</button>
<p id="tmsg" class="msg"></p>
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
async function post(path, payload){{
  const r = await fetch(path, {{method:"POST", headers:{{"Content-Type":"application/json"}},
    body: JSON.stringify(payload)}});
  return r;
}}
$("test").onclick = async () => {{
  const t = $("tmsg"), key = $("key").value.trim();
  t.textContent = "Testing…"; t.className = "msg";
  let d, r;
  try {{ r = await post("/api/keys/test", {{pid: "{pid}", key: key}}); d = await r.json(); }}
  catch(e) {{ t.textContent = "✗ Could not reach the server."; return; }}
  if (d.ok) {{ t.textContent = "✓ Valid — the provider accepted this key." +
      (d.latency_ms ? " (" + d.latency_ms + "ms)" : "") + (d.detail ? " · " + d.detail : "");
      t.className = "msg ok"; }}
  else {{ t.textContent = "✗ " + (d.error || "Test failed."); t.className = "msg err"; }}
}};
$("save").onclick = async () => {{
  const v = $("key").value.trim();
  if (await (await post("/api/settings", {{scraper: {{"{pid}": v}}}})).ok) {{ $("msg").textContent = "Saved ✓"; setTimeout(()=>location.reload(), 700); }}
  else $("msg").textContent = "Save failed.";
}};
$("clear").onclick = async () => {{
  if (await (await post("/api/settings", {{scraper: {{"{pid}": ""}}}})).ok) {{ $("msg").textContent = "Cleared."; setTimeout(()=>location.reload(), 700); }}
  else $("msg").textContent = "Clear failed.";
}};
$("key").addEventListener("keydown", e => {{ if (e.key === "Enter") $("save").onclick(); }});
</script>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _keys_test(self):
        body = self._body()
        group = str(body.get("group") or "scraper").strip().lower()
        key_id = str(body.get("keyid") or body.get("pid") or "").strip().lower()
        key = str(body.get("key") or "").strip()
        if group == "ai":
            return self._send(200, ai.test(key_id or "openai", key,
                                           body.get("model"), body.get("base")))
        if group == "oauth":
            # Nothing from a live provider here — report whether the pair is set
            # and valid; the real test is the provider's own dashboard.
            conf = oauth.providers_configured()
            names = {p: n for p, n, _d in conf}
            if key_id == "google":
                ok = bool(oauth.GOOGLE_CLIENT_ID and oauth.GOOGLE_CLIENT_SECRET)
            elif key_id == "facebook":
                ok = bool(oauth.FACEBOOK_APP_ID and oauth.FACEBOOK_APP_SECRET)
            else:
                ok = bool(names.get(key_id))
            return self._send(200, {
                "ok": ok, "provider": key_id,
                "error": None if ok else ("%s credentials not configured" % key_id),
                "detail": "configured via environment" if ok else
                          "set OAUTH_* env vars (clear+restart to apply) or use the platform link above"})
        if group == "site":
            if key_id == "indexnow":
                val = key or indexnow.key()
                ok = bool(val) and len(val) == 32 and all(c in "0123456789abcdef" for c in val)
                return self._send(200, {"ok": ok, "provider": "indexnow",
                                        "error": None if ok else "IndexNow key must be 32 lowercase hex",
                                        "key_file": indexnow.key_file_path() if bool(val) else None})
            if key_id == "gsc":
                val = key or seo.google_site_verification()
                ok = bool(val.strip())
                return self._send(200, {"ok": ok, "provider": "gsc",
                                        "error": None if ok else
                                        "no token set — grab it from Google Search Console's HTML-tag method",
                                        "save": True})
            return self._send(200, {"ok": False, "error": "unknown site token"})
        return self._send(200, amazon.test_scraper_key(key_id, key))

    def _keys_save(self):
        """Persist a managed key for this process (runtime override; set the
        matching env var to survive a restart). Returns the updated status."""
        body = self._body()
        group = str(body.get("group") or "scraper").strip().lower()
        key_id = str(body.get("keyid") or body.get("pid") or "").strip().lower()
        key = str(body.get("key") or "").strip()
        if group == "ai":
            out = ai.configure_runtime(key_id or "openai", key, body.get("model"),
                                       body.get("base"))
            return self._send(200, out)
        if group == "site":
            if key_id == "indexnow":
                indexnow.set_key(key)
                return self._send(200, {"ok": True, "provider": "indexnow",
                                        "live": indexnow.key(),
                                        "note": "applies now; set INDEXNOW_KEY env to persist"})
            if key_id == "gsc":
                seo.set_google_site_verification(key)
                return self._send(200, {"ok": True, "provider": "gsc",
                                        "set": bool(key),
                                        "note": "applies now; set PSTORE_GOOGLE_SITE_VERIFICATION env to persist"})
            return self._send(200, {"ok": False, "error": "unknown site token"})
        if group == "market":
            if key_id == "affiliate":
                amazon.set_tag(key)
                return self._send(200, {"ok": True, "provider": "affiliate",
                                        "tag": amazon.AFFILIATE_TAG,
                                        "note": "applies now; set PSTORE_TAG env to persist"})
            if key_id == "market":
                amazon.set_market(key)
                return self._send(200, {"ok": True, "provider": "market",
                                        "market": amazon.MARKET})
        if group == "scraper":
            if key_id in amazon._SCRAPER_PROVIDERS:
                amazon.set_scraper_key(key_id, key)
                return self._send(200, {"ok": True, "provider": key_id})
        return self._send(200, {"ok": False, "error": "unknown key"})

    # ------------------------------------------------------------------ managed keys hub
    def _managed_groups(self):
        """Metadata for every key group shown on /keys — each entry carries the
        direct link to the platform where the owner manages the real key."""
        scraper_prov = amazon.scraper_status()["providers"]
        ai_prov = ai.providers()
        return [
            {
                "id": "scraper",
                "label": "🛢 Scraper providers",
                "hint": "Data providers used to search & scrape Amazon listings. Each is its own page.",
                "entries": [{
                    "id": pid, "name": pv["name"], "env": amazon._SCRAPER_PROVIDERS[pid]["env_var"],
                    "url": amazon._SCRAPER_PROVIDERS[pid].get("key_url", ""),
                    "home": amazon._SCRAPER_PROVIDERS[pid].get("key_url", ""),
                    "status": "key set" if pv.get("has_key") else "no key set",
                    "page": "/keys/%s" % pid,
                } for pid, pv in scraper_prov.items()],
            },
            {
                "id": "ai",
                "label": "🤖 AI (LLM) providers",
                "hint": "Writes the ebook/headline copy. Paste a key, test it, and save.",
                "entries": [{
                    "id": pid, "name": m["label"], "env": m["key_env"],
                    "url": m["home"], "home": m["home"],
                    "hint": m.get("key_hint", ""),
                    "status": ("active" if ai.active_provider() == pid else "set") if bool(ai.key_for(pid)) else "no key set",
                    "page": "/keys/ai/%s" % pid,
                } for pid, m in ai.PROVIDERS.items()],
            },
            {
                "id": "oauth",
                "label": "🔐 Sign-in (OAuth) keys",
                "hint": "Optional Google / Facebook login for the admin. Configured via environment.",
                "entries": [
                    {"id": "google", "name": "Google", "env": "OAUTH_GOOGLE_CLIENT_ID + OAUTH_GOOGLE_CLIENT_SECRET",
                     "url": "https://console.developers.google.com/apis/credentials",
                     "home": "https://console.developers.google.com/apis/credentials",
                     "status": "set" if oauth.GOOGLE_CLIENT_ID and oauth.GOOGLE_CLIENT_SECRET else "no key set",
                     "page": "/keys/oauth/google"},
                    {"id": "facebook", "name": "Facebook", "env": "OAUTH_FACEBOOK_APP_ID + OAUTH_FACEBOOK_APP_SECRET",
                     "url": "https://developers.facebook.com/apps",
                     "home": "https://developers.facebook.com/apps",
                     "status": "set" if oauth.FACEBOOK_APP_ID and oauth.FACEBOOK_APP_SECRET else "no key set",
                     "page": "/keys/oauth/facebook"},
                ],
            },
            {
                "id": "site",
                "label": "🌐 Site ownership & indexing",
                "hint": "Proves you own the site to search engines. Visible here, saved in memory, env-persisted.",
                "entries": [
                    {"id": "indexnow", "name": "IndexNow key", "env": "INDEXNOW_KEY",
                     "url": "https://www.indexnow.org/", "home": "https://www.indexnow.org/",
                     "status": ("%s" % indexnow.key()[-6:]) if indexnow.key() else "no key set",
                     "page": "/keys/site/indexnow"},
                    {"id": "gsc", "name": "Google Search Console token", "env": "PSTORE_GOOGLE_SITE_VERIFICATION",
                     "url": "https://search.google.com/search-console",
                     "home": "https://search.google.com/search-console",
                     "status": "set" if seo.google_site_verification() else "not set",
                     "page": "/keys/site/gsc"},
                ],
            },
            {
                "id": "market",
                "label": "🛒 Marketplace & affiliates",
                "hint": "Amazon marketplace + your Associates tag on every product link.",
                "entries": [
                    {"id": "affiliate", "name": "Amazon Associates tag", "env": "PSTORE_TAG",
                     "url": "https://affiliate-program.amazon.com/account",
                     "home": "https://affiliate-program.amazon.com/account",
                     "status": amazon.AFFILIATE_TAG or "no tag set",
                     "page": "/keys/market/affiliate"},
                    {"id": "market", "name": "Amazon marketplace", "env": "PSTORE_MARKET",
                     "url": "https://affiliate-program.amazon.com/account",
                     "home": "https://affiliate-program.amazon.com/account",
                     "status": amazon.MARKET or "default",
                     "page": "/keys/market/market"},
                ],
            },
        ]

    def _managed_entry(self, group_id, entry_id):
        for g in self._managed_groups():
            if g["id"] == group_id:
                for e in g["entries"]:
                    if e["id"] == entry_id:
                        return g, e
        return None, None

    def _keys_managed_page(self, group_id, entry_id):
        """One managed-key page (mirrors the scraper provider page): current
        status, a direct link to the platform, and an input to test + save."""
        group, entry = self._managed_entry(group_id.lower(), entry_id.lower())
        if not entry:
            return self._send(404, b"<html><body><p>Unknown key.</p></body></html>",
                              "text/html; charset=utf-8")
        group_label = group["label"]
        name = entry["name"]
        env = entry.get("env", "")
        home = entry.get("home", "")
        key_url = entry.get("url", "")
        masked = ""
        cur = ""
        editable = group_id.lower() in ("ai", "site", "market") or group_id.lower() == "scraper"
        if group_id == "scraper":
            cur = amazon._scraper_key(entry_id)
            masked = (("••••" + cur[-4:]) if cur else "(no key set)")
        elif group_id == "ai":
            cur = ai.key_for(entry_id)
            masked = (("••••" + cur[-4:]) if cur else "(no key set)")
        elif group_id == "site":
            if entry_id == "indexnow":
                cur = indexnow.key()
                masked = cur or "(no key set)"
            else:
                cur = seo.google_site_verification()
                masked = cur or "(no key set)"
        elif group_id == "market":
            if entry_id == "affiliate":
                cur = amazon.AFFILIATE_TAG or "(none set)"
            else:
                cur = amazon.MARKET or "(default)"
            masked = cur
        elif group_id == "oauth":
            masked = ("configured" if entry["status"] == "set" else "(env not set)")
        test_group = "scraper" if group_id == "scraper" else group_id
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{seo._clean(name)} — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
<style>.key{{font-family:ui-monospace,Menlo,monospace;font-size:13px;background:var(--bg);border:1px solid var(--border);
border-radius:10px;padding:10px 12px;margin:0;word-break:break-all}}
.masked{{font-size:16px;font-weight:700;letter-spacing:1px}}</style>
</head><body>
<header><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>{seo._clean(name)} <span>key.</span></h1>
<p class="tagline">Paste or refresh this key, test it, then save it here.</p></div>
{self._admin_nav('keys')}
</header>
<main>
<section class="card"><h2>🎯 {seo._clean(group_label)} — {seo._clean(name)}</h2>
<div class="sub"><h3>Current status</h3><p class="key masked">{seo._clean(masked)}</p>
<p class="hint">{seo._clean(entry['status'])}{(' · env ' + seo._clean(env)) if env else ''}</p></div>
<div class="sub"><h3>Manage your key at the platform</h3>
<p class="hint">Get, reset or revoke the real key at the provider's own console — then paste it back here.</p>
<p><a class="btn" href="{seo._clean(home)}" target="_blank" rel="noopener">Open {seo._clean(name)} console ↗</a></p>
{('' if key_url == home else '<p class="hint">Direct reference: <a href="' + seo._clean(key_url) + '" target="_blank" rel="noopener">' + seo._clean(key_url) + '</a></p>')}
</div>
<div class="sub"><h3>Test the key</h3>
<p class="hint">{'Fires a real, free request to confirm the provider accepts this key' if group_id in ('ai','scraper') else 'Checks the value is present and well-formed.'}</p>
<button id="test" class="warm">▶ Test</button>
<p id="tmsg" class="msg"></p></div>
{('' if group_id == 'oauth' else f'''<div class="row">
  <label>Value <input id="key" placeholder="paste or enter…" autocomplete="off" spellcheck="false"></label>
  <button id="save" class="warm">Save</button>
  <button id="clear">Clear</button></div>''')}
<p id="msg" class="msg"></p>
<a href="/keys" class="hint">← all keys</a>
</section></main>
<footer><p>Saved here applies to this process immediately; set <code>{seo._clean(env)}</code> as an environment variable to make it permanent across restarts.</p></footer>
<script>
function $(id){{return document.getElementById(id);}}
async function post(path, payload){{
  const r = await fetch(path, {{method:"POST", headers:{{"Content-Type":"application/json"}},
    body: JSON.stringify(payload)}});
  return r;
}}
const testBtn = $("test");
if (testBtn) testBtn.onclick = async () => {{
  const t = $("tmsg"), key = $("key") ? $("key").value.trim() : "";
  t.textContent = "Testing…"; t.className = "msg";
  let d, r;
  try {{ r = await post("/api/keys/test", {{group:"{test_group}", keyid:"{entry_id}", key:key}}); d = await r.json(); }}
  catch(e) {{ t.textContent = "✗ Could not reach the server."; return; }}
  if (d.ok) {{ t.textContent = "✓ OK" + (d.latency_ms ? " (" + d.latency_ms + "ms)" : "") + (d.detail ? " · " + d.detail : ""); t.className = "msg ok"; }}
  else {{ t.textContent = "✗ " + (d.error || "Test failed."); t.className = "msg err"; }}
}};
const saveBtn = $("save");
if (saveBtn) saveBtn.onclick = async () => {{
  const v = $("key").value.trim();
  const r = await post("/api/keys/save", {{group:"{group_id}", keyid:"{entry_id}", key:v}});
  const d = await r.json();
  if (d.ok) {{ $("msg").textContent = "Saved ✓ " + (d.note || "applies now."); $("msg").className = "msg ok"; setTimeout(()=>location.reload(), 900); }}
  else {{ $("msg").textContent = "Save failed: " + (d.error || "unknown"); $("msg").className = "msg err"; }}
}};
const clearBtn = $("clear");
if (clearBtn) clearBtn.onclick = async () => {{
  const r = await post("/api/keys/save", {{group:"{group_id}", keyid:"{entry_id}", key:""}});
  const d = await r.json();
  if (d.ok) {{ $("msg").textContent = "Cleared."; setTimeout(()=>location.reload(), 700); }}
  else {{ $("msg").textContent = "Clear failed."; }}
}};
const inp = $("key");
if (inp) inp.addEventListener("keydown", e => {{ if (e.key === "Enter" && saveBtn) saveBtn.onclick(); }});
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
        boosts = self._boosts_for(keyword) if keyword else []
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
            "boosts": boosts,
            "slug": slug,
            "niche_url": niche_url,
            "landing_url": landing_url,
            "ebook_url": ebook_url,
            "ebook_ready": keyword in _EBOOKS,
            "social_kit": social.post_kits(keyword, items, base_url=seo.BASE_URL),
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


# ------------------------------------------------------------------ boosts suite
    def _boost_longtail(self, keyword):
        """Long-tail phrases (SEM autosuggest) to weave into boost copy on a run.
        Gated off in offline tests (CACHE_TTL=0) so runs stay fast and hermit."""
        if amazon.CACHE_TTL <= 0:
            return ()
        try:
            niche = self._saved_niche(keyword)
            if not niche:
                return ()
            info = sem.brief(keyword, niche, seo.BASE_URL,
                             audit_row=seo.audit_niche(niche),
                             indexnow_key=indexnow.key())
            out = [g.get("phrase") for g in (info.get("longtail") or [])]
            out = [p for p in out if p]
            for q in (info.get("paa") or []):
                if len(out) >= 3:
                    break
                if q.get("question"):
                    out.append(q["question"])
            return tuple(out[:3])
        except Exception:
            return ()

    def _boosts_for(self, keyword, keywords=()):
        """Boost campaigns for a niche — live builds merged with run/publish
        history and per-campaign click totals (attribution via utm_content)."""
        niche = self._saved_niche(keyword)
        if not niche:
            return []
        slug = seo._slugify(keyword)
        campaigns = market_engine.build_boost_campaigns(
            keyword, niche["products"] or [], base_url=seo.BASE_URL,
            slug=slug, keywords=keywords)
        with _lock:
            conn = _db()
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM boosts WHERE lower(slug)=? ORDER BY id", (slug,)).fetchall()]
            stat = {}
            for c in campaigns:
                stat[c["code"]] = conn.execute(
                    "SELECT COUNT(*) c FROM clicks WHERE lower(slug)=? AND content=?",
                    (slug, str(c["code"]))).fetchone()["c"]
            conn.close()
        code_from = {r["utm_content"]: r for r in rows if r.get("utm_content")}
        run_map = {r["name"]: r for r in rows}
        seen = set()

        def social_path(code, published_ok=True):
            if not code:
                return None
            if published_ok:
                r = code_from.get(code)
                if not r or r.get("status") != "published":
                    return None
            return "/social/" + slug + "/" + str(code)

        def entry(c, r):
            code = c.get("code") or r.get("utm_content") or ""
            return {"name": c["name"], "id": c.get("id") or r.get("id"),
                    "target": c.get("target", "landing"),
                    "script": c.get("script", ""),
                    "link": c.get("link", ""), "code": code,
                    "qr": c.get("qr", ""), "runs": r.get("runs") or 0,
                    "status": r.get("status") or "ready",
                    "clicks": stat.get(code) or 0,
                    "social": social_path(code),
                    "updated_at": r.get("updated_at")}

        out = []
        for c in campaigns:
            r = run_map.get(c["name"]) or {}
            seen.add(c["name"])
            out.append(entry(c, r))
        for r in rows:
            if r["name"] in seen:
                continue
            c = {"name": r["name"], "link": r["link"], "code": r["utm_content"],
                 "target": "landing", "script": r["script"], "qr": ""}
            out.append(entry(c, r))
        return out

    def _boosts_api(self, q):
        keyword = (q.get("keyword") or [""])[0].strip()
        if not keyword:
            return self._send(200, {"keyword": "", "boosts": []})
        return self._send(200, {"keyword": keyword,
                                 "boosts": self._boosts_for(keyword)})

    def _run_boosts(self):
        """Run boost campaigns for a niche for real: persist each campaign with
        its tracked link, weave in SEM long-tail copy, warm the lead-magnet PDF,
        and ping IndexNow for the landing URL. Returns the live boosts feed."""
        body = self._body()
        keyword = str(body.get("keyword") or "").strip()
        niche = self._saved_niche(keyword)
        if not niche:
            return self._send(404, {"error": "no saved niche matches that keyword"})
        slug = seo._slugify(keyword)
        keywords = self._boost_longtail(keyword)
        campaigns = market_engine.build_boost_campaigns(
            keyword, niche["products"] or [], base_url=seo.BASE_URL,
            slug=slug, keywords=keywords)
        names = body.get("names") or []
        if isinstance(names, str):
            names = [n.strip() for n in names.split(",") if n.strip()]
        pick = campaigns
        if names:
            pick = [c for c in campaigns
                    if c["name"] in names or c.get("id") in names]
            if not pick:
                return self._send(400, {"error": "no campaign matches those names"})
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with _lock:
            conn = _db()
            for c in pick:
                row = conn.execute(
                    "SELECT id FROM boosts WHERE lower(slug)=? AND name=?",
                    (slug, c["name"])).fetchone()
                if row:
                    conn.execute(
                        "UPDATE boosts SET script=?, link=?, utm_content=?, "
                        "runs=runs+1, status='active', updated_at=? WHERE id=?",
                        (c["script"], c["link"], c["code"], now, row["id"]))
                else:
                    conn.execute(
                        "INSERT INTO boosts (slug, keyword, name, script, link, "
                        "utm_content, runs, status, updated_at) VALUES (?,?,?,?,?,?,1,'active',?)",
                        (slug, keyword, c["name"], c["script"], c["link"],
                         c["code"], now))
            conn.commit()
            conn.close()
        try:
            dry = self._ebook_for(keyword) is not None
        except Exception:
            dry = False
        self._fire_indexnow(["/lp/" + slug])
        boosts = self._boosts_for(keyword, keywords=keywords)
        return self._send(200, {
            "ok": True, "keyword": keyword, "ran": len(pick),
            "runs_total": sum(b["runs"] for b in boosts),
            "ebook_cached": dry, "indexnow_queued": True,
            "sem_keywords": list(keywords), "boosts": boosts,
        })

    def _boosts_to_social(self):
        """Push one boost campaign to the social page as a published "Boost"
        post — same UTM link, real webhook fire, click attribution included."""
        body = self._body()
        keyword = str(body.get("keyword") or "").strip()
        name = str(body.get("campaign") or "").strip()
        if not keyword or not name:
            return self._send(400, {"error": "keyword and campaign required"})
        boosts = self._boosts_for(keyword)
        c = next((b for b in boosts if b["name"] == name or b["id"] == name), None)
        if not c:
            return self._send(404, {"error": "no matching boost campaign"})
        slug = seo._slugify(keyword)
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        kit = {"platform": "Boost", "name": "Boost · %s" % c["name"],
               "body": c["script"], "link": c["link"],
               "utm_content": c["code"], "keyword": keyword}
        with _lock:
            conn = _db()
            row = conn.execute(
                "SELECT id FROM social_posts WHERE lower(slug)=? AND utm_content=?",
                (slug, c["code"])).fetchone()
            if row:
                conn.execute(
                    "UPDATE social_posts SET status='published', published_at=?, "
                    "name=?, body=?, link=? WHERE id=?",
                    (now, kit["name"], kit["body"], kit["link"], row["id"]))
            else:
                conn.execute(
                    "INSERT INTO social_posts (slug, keyword, platform, name, body, "
                    "link, utm_content, status, published_at) "
                    "VALUES (?,?,?,?,?,?,?, 'published', ?)",
                    (slug, keyword, "Boost", kit["name"], kit["body"],
                     kit["link"], c["code"], now))
            conn.commit()
            conn.close()
        self._webhook_publish([kit])
        _, stats = self._social_db(keyword, slug, None)
        return self._send(200, {"ok": True, "post": kit,
                                "clicks": c["clicks"], "stats": stats})


# ------------------------------------------------------------------ CMS suite
    def _cms_pages_payload(self):
        """List all CMS-managed pages with their niche keyword, status and stats."""
        with _lock:
            conn = _db()
            try:
                pages = cms_mod.list_pages(conn)
                out = []
                for p in pages:
                    sections = cms_mod.get_sections(conn, p["id"])
                    out.append({
                        "id": p.get("id"),
                        "keyword": p.get("keyword"),
                        "slug": p.get("slug"),
                        "enabled": bool(p.get("enabled", 1)),
                        "section_count": len(sections),
                        "live_url": "/lp/" + p.get("slug", ""),
                        "updated_at": p.get("updated_at"),
                    })
            finally:
                conn.close()
        return {"pages": out}

    def _cms_pages_api(self):
        return self._send(200, self._cms_pages_payload())

    def _cms_page_payload(self, page_id):
        with _lock:
            conn = _db()
            try:
                page = cms_mod._row_to_dict(conn.execute(
                    "SELECT * FROM lead_pages WHERE id=?", (page_id,)).fetchone())
                if not page:
                    return None
                sections = cms_mod.get_sections(conn, page["id"])
                # merge defaults so the editor shows the full value set
                full_sections = []
                for s in sections:
                    st = s.get("section_type")
                    defaults = cms_mod._DEFAULT_SECTIONS.get(st, {})
                    merged = dict(defaults)
                    merged.update(s.get("content") or {})
                    full_sections.append({
                        "id": s.get("id"),
                        "type": st,
                        "label": cms_mod.SECTION_TYPES.get(st, {}).get("label", st),
                        "icon": cms_mod.SECTION_TYPES.get(st, {}).get("icon", "🧩"),
                        "fields": cms_mod.SECTION_TYPES.get(st, {}).get("fields", []),
                        "enabled": bool(s.get("enabled", 1)),
                        "sort_order": s.get("sort_order", 0),
                        "content": merged,
                    })
                page["sections"] = full_sections
                page["section_types"] = list(cms_mod.SECTION_TYPES.keys())
                page["style_defaults"] = cms_mod.DEFAULT_STYLE
                return page
            finally:
                conn.close()

    def _cms_page_api(self, match, q):
        page_id = int(match.group(1))
        page = self._cms_page_payload(page_id)
        if not page:
            return self._send(404, {"error": "page not found"})
        return self._send(200, page)

    def _cms_save_page(self):
        """Save page-level settings (style, settings, section_order)."""
        body = self._body()
        page_id = int(body.get("page_id") or 0)
        with _lock:
            conn = _db()
            try:
                row = conn.execute(
                    "SELECT id FROM lead_pages WHERE id=?", (page_id,)).fetchone()
                if not row:
                    return self._send(404, {"error": "page not found"})
                update = {}
                if "style" in body:
                    update["style"] = body["style"]
                if "settings" in body:
                    update["settings"] = body["settings"]
                if "section_order" in body:
                    update["section_order"] = body["section_order"]
                if "enabled" in body:
                    update["enabled"] = int(bool(body["enabled"]))
                cms_mod.update_page(conn, page_id, update)
            finally:
                conn.close()
        return self._send(200, self._cms_page_payload(page_id))

    def _cms_apply_preset(self):
        """One-click restyle: apply a named style preset (color/mode/shape)."""
        body = self._body()
        page_id = int(body.get("page_id") or 0)
        name = str(body.get("preset") or "").strip()
        if name not in cms_mod.STYLE_PRESETS:
            return self._send(400, {"error": "unknown preset"})
        with _lock:
            conn = _db()
            try:
                row = conn.execute(
                    "SELECT id FROM lead_pages WHERE id=?", (page_id,)).fetchone()
                if not row:
                    return self._send(404, {"error": "page not found"})
                cms_mod.apply_preset(conn, page_id, name)
            finally:
                conn.close()
        return self._send(200, self._cms_page_payload(page_id))

    def _cms_generate(self):
        """One-click copy generation: reseed all section copy from the niche's
        live data (persuasion defaults). Keeps style + settings toggles."""
        body = self._body()
        page_id = int(body.get("page_id") or 0)
        keyword = str(body.get("keyword") or "").strip()
        with _lock:
            conn = _db()
            try:
                row = conn.execute(
                    "SELECT keyword FROM lead_pages WHERE id=?", (page_id,)).fetchone()
                if not row:
                    return self._send(404, {"error": "page not found"})
                keyword = keyword or row["keyword"]
                count = cms_mod.generate_copy(conn, page_id, keyword)
            finally:
                conn.close()
        return self._send(200, {"ok": True, "sections": count,
                                "payload": self._cms_page_payload(page_id)})

    def _cms_update_section(self, page_id, section_id):
        """Update one section's content/enabled/sort_order."""
        page_id = int(page_id)
        section_id = int(section_id)
        body = self._body()
        with _lock:
            conn = _db()
            try:
                row = conn.execute(
                    "SELECT id FROM lead_sections WHERE id=? AND page_id=?",
                    (section_id, page_id)).fetchone()
                if not row:
                    return self._send(404, {"error": "section not found"})
                update = {}
                if "content" in body:
                    update["content"] = body["content"]
                if "enabled" in body:
                    update["enabled"] = int(bool(body["enabled"]))
                if "sort_order" in body:
                    update["sort_order"] = int(body["sort_order"])
                cms_mod.update_section(conn, section_id, update)
            finally:
                conn.close()
        return self._send(200, self._cms_page_payload(page_id))

    def _admin_cms(self, q):
        """Admin content-management editor for lead pages."""
        niches = [n["keyword"] for n in self._all_niches()]
        keyword = (q.get("keyword") or [""])[0].strip() or (niches[0] if niches else "")
        opts = "".join('<option value="%s"%s>%s</option>' % (seo._clean(k),
                        ' selected' if k == keyword else "", seo._clean(k)) for k in niches)
        page_id = None
        page_payload = None
        if keyword:
            with _lock:
                conn = _db()
                try:
                    p = cms_mod.get_or_create_page(conn, keyword)
                    page_id = p["id"]
                finally:
                    conn.close()
            page_payload = self._cms_page_payload(page_id)
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CMS — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
<style>
.cms-layout {{ display:grid; grid-template-columns: 1fr 340px; gap: 20px; align-items:start; }}
@media (max-width:860px) {{ .cms-layout {{ grid-template-columns:1fr; }} }}
.field {{ margin-bottom:14px; }}
.field label {{ font-weight:700; margin-bottom:6px; }}
.section-editor {{ border:1px solid var(--border); border-radius:16px; padding:16px;
  margin-bottom:14px; background:#fffdf8; }}
.section-editor h3 {{ margin:0; font-size:15px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
.section-editor textarea {{ min-height:70px; }}
.items-editor textarea {{ min-height:110px; font-family:ui-monospace,Menlo,monospace; font-size:12px; }}
.cols2 {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
.color-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }}
.color-grid label {{ font-size:11.5px; }}
.outline {{ border:1px solid var(--border); border-radius:12px; padding:12px; margin:10px 0; background:#fff; }}
.preset-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; }}
.preset-btn {{ text-align:left; border:2px solid var(--border); border-radius:16px; padding:12px; cursor:pointer;
  background:#fff; transition:border-color .15s ease, transform .15s ease; }}
.preset-btn:hover {{ transform:translateY(-2px); }}
.preset-btn.active {{ border-color:var(--accent,#ff6b2c); background:#fff6ee; }}
.preset-btn .swatches {{ display:flex; gap:6px; margin-bottom:8px; }}
.preset-btn .swatches i {{ width:22px; height:22px; border-radius:50%; border:1px solid rgba(0,0,0,.12); display:inline-block; }}
.preset-btn b {{ display:block; font-size:13.5px; }}
.preset-btn span {{ font-size:11.5px; color:var(--muted); }}
/* toggle switch */
.sw {{ position:relative; display:inline-block; width:44px; height:24px; vertical-align:middle; flex-shrink:0; }}
.sw input {{ opacity:0; width:0; height:0; }}
.sw .sl {{ position:absolute; inset:0; background:#cbd5e1; border-radius:999px; transition:.2s; cursor:pointer; }}
.sw .sl:before {{ content:""; position:absolute; width:18px; height:18px; left:3px; top:3px; background:#fff;
  border-radius:50%; transition:.2s; box-shadow:0 1px 3px rgba(0,0,0,.25); }}
.sw input:checked + .sl {{ background:#22c55e; }}
.sw input:checked + .sl:before {{ transform:translateX(20px); }}
.toggle-row {{ display:flex; align-items:center; gap:10px; padding:8px 0; }}
.toggle-row .lbl {{ font-weight:700; font-size:14px; }}
.toggle-row .sub {{ color:var(--muted); font-size:12px; }}
.sec-head {{ display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }}
.sec-actions {{ display:flex; align-items:center; gap:10px; }}
.copy-expand {{ border-top:1px dashed var(--border); margin-top:12px; padding-top:12px; }}
details.copy-details summary {{ cursor:pointer; color:var(--accent,#ff6b2c); font-weight:700; font-size:13px; }}
.hint-sm {{ font-size:12px; color:var(--muted); }}
.bigbtn {{ width:100%; padding:16px; font-size:17px; font-weight:800; border-radius:14px;
  border:0; cursor:pointer; color:#fff; background:linear-gradient(135deg,#ff6b2c,#ff873c); }}
</style>
</head><body>
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>Lead page <span>CMS.</span></h1>
<p class="tagline">Edit every element of a niche's landing page — style, sections, CTA, email-gated PDF download, persuasion elements — without touching code.</p></div>
{self._admin_nav('cms')}
</header>
<main>
<section class="card"><h2>🧩 Choose a niche</h2>
<form class="row" method="get" action="/admin/cms">
  <label>Niche <select name="keyword" onchange="this.form.submit()">{opts}</select></label>
</form>
{('<p class="hint">Select a saved niche to start editing its landing page. Each niche gets its own editable page with sensible, persuasion-engineered defaults.</p>' if not keyword else '')}
</section>
{self._cms_admin_editor_html(keyword, page_id, page_payload)}
</main>
<footer><p>CMS edits go live on the page immediately after saving. Uses the “How to Sell Like Crazy” and “Influence” playbook — reciprocity, commitment, social proof, authority, scarcity — all editable per section.</p></footer>
{_TOTOP}
<script>
(function () {{
  "use strict";
  var PAGE_ID = {int(page_id or 0)};
  var KEYWORD = {json.dumps(keyword or "")};
  function $(id) {{ return document.getElementById(id); }}
  function say(m, ok) {{
    var el = $("save-msg");
    if (!el) return;
    el.textContent = m;
    el.style.color = ok ? "#159a4b" : "#d64545";
  }}
  function post(path, body) {{
    return fetch(path, {{
      method: "POST", headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify(body)
    }}).then(function (r) {{ return r.json(); }});
  }}
  function collectStyle() {{
    var st = {{}};
    document.querySelectorAll("[data-style]").forEach(function (i) {{
      st[i.getAttribute("data-style")] = i.value;
    }});
    st.font_family = $("cms-font") ? $("cms-font").value : "";
    st.border_radius = $("cms-radius") ? $("cms-radius").value : "";
    st.cta_gradient = $("cms-cta") ? $("cms-cta").value : "";
    st.layout = $("cms-layout") ? $("cms-layout").value : "centered";
    st.hero_style = $("cms-hero") ? $("cms-hero").value : "gradient";
    st.mode = $("cms-mode") ? $("cms-mode").value : "light";
    var preset = document.querySelector("[data-preset].active");
    if (preset) st.preset = preset.getAttribute("data-preset");
    return st;
  }}
  function collectSettings() {{
    var s = {{ use_cms: $("use-cms") ? $("use-cms").checked : true }};
    var gate = $("cms-gate");
    if (gate) s.pdf_gated = gate.value === "1";
    s.email_gate_enabled = gate ? s.pdf_gated : true;
    s.promo_enabled = $("cms-promo") ? $("cms-promo").checked : false;
    s.promo = {{ text: $("cms-promo-text") ? $("cms-promo-text").value : "",
                code: $("cms-promo-code") ? $("cms-promo-code").value : "" }};
    s.countdown_enabled = $("cms-countdown") ? $("cms-countdown").checked : false;
    s.countdown_minutes = parseInt($("cms-cd-min") ? $("cms-cd-min").value : "30", 10);
    s.countdown_headline = $("cms-cd-head") ? $("cms-cd-head").value : "";
    s.countdown_done = $("cms-cd-done") ? $("cms-cd-done").value : "";
    s.sticky_cta = $("cms-sticky") ? $("cms-sticky").checked : true;
    s.animation = $("cms-anim") ? $("cms-anim").checked : true;
    return s;
  }}
  function sectionContent(sec) {{
    var content = {{}};
    sec.querySelectorAll(".sec-input").forEach(function (txt) {{
      var field = txt.getAttribute("data-field");
      var raw = txt.value;
      if (txt.classList.contains("items-editor")) {{
        try {{ content[field] = JSON.parse(raw); }}
        catch (e) {{ content[field] = raw; }}
      }} else {{ content[field] = raw; }}
    }});
    return content;
  }}
  function saveAll(ev) {{
    ev && ev.preventDefault();
    say("Saving…");
    post("/api/cms/page", {{ page_id: PAGE_ID, style: collectStyle(), settings: collectSettings() }})
      .then(function (d) {{ say("Page settings saved ✓ — live now.", true); }})
      .catch(function () {{ say("Save failed.", false); }});
  }}
  function saveSection(sec, enabled) {{
    var sid = sec.getAttribute("data-sec");
    var toggle = sec.querySelector(".sec-enable");
    var body = {{ section_id: parseInt(sid, 10), content: sectionContent(sec) }};
    body.enabled = enabled !== undefined ? enabled : (toggle ? toggle.checked : true);
    say("Saving section…");
    post("/api/cms/pages/" + PAGE_ID + "/sections/" + sid, body)
      .then(function (d) {{ say("Section saved ✓ — live now.", true); }})
      .catch(function () {{ say("Section save failed.", false); }});
  }}
  function applyPreset(name) {{
    say("Applying " + name + " template…");
    post("/api/cms/preset", {{ page_id: PAGE_ID, preset: name }})
      .then(function (d) {{
        if (d.error) {{ say("Couldn't apply template.", false); return; }}
        location.reload();
      }})
      .catch(function () {{ say("Template apply failed.", false); }});
  }}
  function generatePage() {{
    say("Generating fresh copy from your niche data…");
    post("/api/cms/generate", {{ page_id: PAGE_ID, keyword: KEYWORD }})
      .then(function (d) {{
        if (d.ok) {{ say("Page regenerated ✓ — " + d.sections + " sections written.", true); setTimeout(function () {{ location.reload(); }}, 700); }}
        else {{ say("Generation failed.", false); }}
      }})
      .catch(function () {{ say("Generation failed.", false); }});
  }}
  var saveAllBtn = $("save-all");
  if (saveAllBtn) saveAllBtn.onclick = saveAll;
  var genBtn = $("generate-page");
  if (genBtn) genBtn.onclick = generatePage;
  function syncToggle(boxId, panelId, hiddenId) {{
    var box = $(boxId), panel = $(panelId);
    if (!box) return;
    var sync = function () {{
      if (panel) panel.style.display = box.checked ? "block" : "none";
      if (hiddenId) {{
        var h = $(hiddenId);
        if (h) h.value = box.checked ? "1" : "0";
      }}
    }};
    box.addEventListener("change", sync);
    sync();
  }}
  syncToggle("cms-promo", "promo-fields");
  syncToggle("cms-countdown", "cd-fields");
  syncToggle("cms-gate2", null, "cms-gate");
  /* feature toggles persist the moment they flip, so what you see on the
     live page follows the switch — same instant behavior as section toggles */
  var autoSaveTimer = null;
  function autoSaveSettings() {{
    clearTimeout(autoSaveTimer);
    autoSaveTimer = setTimeout(function () {{
      say("Saving settings…");
      post("/api/cms/page", {{ page_id: PAGE_ID, style: collectStyle(), settings: collectSettings() }})
        .then(function (d) {{ say("Settings saved ✓ — live now.", true); }})
        .catch(function () {{ say("Save failed.", false); }});
    }}, 350);
  }}
  ["cms-promo", "cms-countdown", "cms-sticky", "cms-anim", "cms-gate2", "use-cms"]
    .forEach(function (id) {{
      var box = $(id);
      if (box) box.addEventListener("change", autoSaveSettings);
    }});
  document.querySelectorAll(".preset-btn").forEach(function (btn) {{
    btn.onclick = function () {{ applyPreset(btn.getAttribute("data-preset")); }};
  }});
  document.querySelectorAll(".section-editor").forEach(function (sec) {{
    var b = sec.querySelector(".sec-save");
    if (b) b.onclick = function (ev) {{ ev.preventDefault(); saveSection(sec); }};
    var t = sec.querySelector(".sec-enable");
    if (t) t.onchange = function () {{ saveSection(sec, t.checked); }};
  }});
  document.querySelectorAll(".field textarea, .field input, .field select").forEach(function (el) {{
    el.addEventListener("keydown", function (ev) {{
      if ((ev.ctrlKey || ev.metaKey) && ev.key === "s") {{ ev.preventDefault(); saveAll(); }}
    }});
  }});
}})();
</script>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _cms_admin_editor_html(self, keyword, page_id, page):
        """The actual editor UI for one page. If no page payload, return nothing.

        One-click first: style templates (presets), page toggles (promo,
        countdown, sticky CTA, email gate), per-section show/hide switches, and
        a big "Generate page" button that re-writes copy from niche data.
        Fine-grained copy + color editing collapse under "Edit copy" so the
        screen stays clean.
        """
        if not page:
            return ""
        e = seo._clean
        style = dict(page.get("style") or {})
        if not style.get("preset"):
            style["preset"] = "sunset"
        s = cms_mod.merge_settings(page.get("settings") or {})
        promo = s.get("promo") or {}
        sections = page.get("sections") or []
        live_url = "/lp/" + e(seo._slugify(keyword))
        current_preset = style.get("preset", "sunset")

        # ── one-click style templates ──────────────────────────────────────
        preset_cards = []
        for name, p in cms_mod.STYLE_PRESETS.items():
            sw = "".join('<i style="background:%s"></i>' % e(c) for c in p.get("swatches", []))
            active = " active" if name == current_preset else ""
            preset_cards.append(
                '<button class="preset-btn%s" data-preset="%s">'
                '<span class="swatches">%s</span><b>%s</b><span>%s</span></button>'
                % (active, e(name), sw, e(p.get("label", name)), e(p.get("desc", ""))))
        presets_html = "".join(preset_cards)

        # ── fine style controls ────────────────────────────────────────────
        color_fields = {
            "bg": style.get("bg"), "card_bg": style.get("card_bg"),
            "accent": style.get("accent"), "accent2": style.get("accent2"),
            "text": style.get("text"), "muted": style.get("muted"),
        }
        color_html = "".join(
            '<label>{name}<input type="color" value="{v}" data-style="{k}"></label>'
            .format(name=k.replace("_", " ").title(), k=k, v=e(v or "#ffffff"))
            for k, v in color_fields.items())
        layout_opts = ""
        for lo in ("centered", "wide", "split"):
            sel = ' selected' if (style.get('layout') or 'centered') == lo else ''
            layout_opts += '<option value="%s"%s>%s</option>' % (lo, sel, lo.title())
        hero_labels = {"gradient": "Gradient", "minimal": "Minimal", "bold": "Bold headline"}
        hero_opts = ""
        for ho in ("gradient", "minimal", "bold"):
            sel = ' selected' if (style.get('hero_style') or 'gradient') == ho else ''
            hero_opts += '<option value="%s"%s>%s</option>' % (ho, sel, hero_labels[ho])
        mode_opts = '<option value="light"%s>Light</option><option value="dark"%s>Dark</option>' % (
            ' selected' if (style.get('mode') or 'light') == 'light' else '',
            ' selected' if (style.get('mode') or 'light') == 'dark' else '')

        # ── section editors (toggle + collapsible copy) ────────────────────
        section_editors = []
        for s_idx, sec in enumerate(sections):
            fid = sec["id"]
            content = sec.get("content") or {}
            body_editor = ""
            for field in sec.get("fields", []):
                val = content.get(field, "")
                if isinstance(val, (dict, list)):
                    import json as _json
                    val = _json.dumps(val, indent=1)
                    body_editor += ('<div class="field"><label>%s</label>'
                                    '<textarea class="sec-input items-editor" data-field="%s" '
                                    'data-sec="%d">%s</textarea></div>'
                                    % (field.replace("_", " ").title(), field, fid, e(val)))
                else:
                    body_editor += ('<div class="field"><label>%s</label>'
                                    '<textarea class="sec-input" data-field="%s" data-sec="%d">%s</textarea></div>'
                                    % (field.replace("_", " ").title(), field, fid, e(val)))
            enabled = sec.get("enabled", True)
            section_editors.append(f"""
<div class="section-editor" data-sec="{fid}" id="sec-{fid}">
  <div class="sec-head">
    <h3>{sec.get('icon','🧩')} {e(sec.get('label',''))}</h3>
    <div class="sec-actions">
      <span class="hint-sm">Show on page</span>
      <label class="sw"><input type="checkbox" class="sec-enable" data-sec="{fid}" {'checked' if enabled else ''}>
        <span class="sl"></span></label>
      <button class="mini sec-save" data-sec="{fid}">Save copy</button>
    </div>
  </div>
  <details class="copy-details">
    <summary>✏️ Edit copy for this section</summary>
    <div class="outline">{body_editor}</div>
  </details>
</div>""")
        section_html = "".join(section_editors) if section_editors else \
            '<p class="hint">No sections yet — hit “Generate page” to seed defaults.</p>'

        return f"""
<section class="card"><h2>⚡ Build your page</h2>
<div class="row" style="gap:10px">
  <a class="btn" href="{live_url}" target="_blank" rel="noopener">👁 View live page ↗</a>
  <button class="warm" id="save-all">💾 Save all</button>
</div>
<button class="bigbtn" id="generate-page" style="margin-top:12px">⚡ Generate page copy</button>
<p class="hint-sm" style="margin-top:6px">One click re-writes all section copy from your niche's live data (headline, offers, proof, urgency, FAQ, CTA). Your chosen template and toggles stay untouched.</p>
<p id="save-msg" class="msg"></p>
</section>

<section class="card"><h2>🎨 Pick a template</h2>
<p class="hint-sm" style="margin-top:-4px">One click restyles the whole page. Fine-tune colors below after picking.</p>
<div class="preset-grid">{presets_html}</div>
<details class="copy-details" style="margin-top:14px">
  <summary>🎛️ Advanced style (colors / layout)</summary>
  <div class="outline">
    <div class="cols2 color-grid">{color_html}</div>
    <div class="cols2" style="margin-top:12px">
      <div class="field"><label>Mode</label><select id="cms-mode">{mode_opts}</select></div>
      <div class="field"><label>Layout</label><select id="cms-layout">{layout_opts}</select></div>
      <div class="field"><label>Hero style</label><select id="cms-hero">{hero_opts}</select></div>
      <div class="field"><label>Border radius</label><input type="text" id="cms-radius" value="{e(style.get('border_radius',''))}" placeholder="22px"></div>
      <div class="field"><label>Font family</label><input type="text" id="cms-font" value="{e(style.get('font_family',''))}" placeholder="CSS font stack"></div>
      <div class="field"><label>CTA gradient (CSS)</label><input type="text" id="cms-cta" value="{e(style.get('cta_gradient',''))}" placeholder="linear-gradient(...)"></div>
    </div>
  </div>
</details>
</section>

<section class="card"><h2>🚀 Page features</h2>
<div class="toggle-row">
  <label class="sw"><input type="checkbox" id="cms-promo" {'checked' if s.get('promo_enabled') else ''}><span class="sl"></span></label>
  <div><div class="lbl">Promo banner</div><div class="sub">Colored announcement strip at the very top</div></div>
</div>
<div class="outline" id="promo-fields" style="{'display:block' if s.get('promo_enabled') else 'display:none'}">
  <div class="cols2">
    <div class="field"><label>Promo text</label><input type="text" id="cms-promo-text" value="{e(promo.get('text',''))}" placeholder="Free shipping on orders over $25"></div>
    <div class="field"><label>Promo code</label><input type="text" id="cms-promo-code" value="{e(promo.get('code',''))}" placeholder="SAVE10"></div>
  </div>
</div>
<div class="toggle-row">
  <label class="sw"><input type="checkbox" id="cms-countdown" {'checked' if s.get('countdown_enabled') else ''}><span class="sl"></span></label>
  <div><div class="lbl">Countdown timer</div><div class="sub">Live HH:MM:SS boxes under the header — scarcity in one click</div></div>
</div>
<div class="outline" id="cd-fields" style="{'display:block' if s.get('countdown_enabled') else 'display:none'}">
  <div class="cols2">
    <div class="field"><label>Countdown length</label>
      <select id="cms-cd-min">{''.join('<option value="%d"%s>%d minutes</option>' % (m, ' selected' if int(s.get('countdown_minutes') or 30) == m else '', m) for m in (5, 10, 15, 30, 45, 60))}</select></div>
    <div class="field"><label>Headline above timer</label><input type="text" id="cms-cd-head" value="{e(s.get('countdown_headline',''))}" placeholder="⏳ Today's pricing refresh starts soon"></div>
  </div>
  <div class="field"><label>Message when time runs out</label><input type="text" id="cms-cd-done" value="{e(s.get('countdown_done',''))}" placeholder="Pricing just refreshed — see today's best rate below."></div>
</div>
<div class="toggle-row">
  <label class="sw"><input type="checkbox" id="cms-sticky" {'checked' if s.get('sticky_cta', True) else ''}><span class="sl"></span></label>
  <div><div class="lbl">Sticky “see the pick” bar</div><div class="sub">Floating CTA that follows the visitor after the hero</div></div>
</div>
<div class="toggle-row">
  <label class="sw"><input type="checkbox" id="cms-anim" {'checked' if s.get('animation', True) else ''}><span class="sl"></span></label>
  <div><div class="lbl">Scroll animations</div><div class="sub">Gentle reveal-on-scroll</div></div>
</div>
<div class="toggle-row">
  <label class="sw"><input type="checkbox" id="cms-gate2" {'checked' if s.get('pdf_gated', True) else ''}><span class="sl"></span></label>
  <div><div class="lbl">Email-gated PDF lead magnet</div><div class="sub">On = visitors give email to download the guide; Off = direct download</div></div>
  <input type="hidden" id="cms-gate" value="{1 if s.get('pdf_gated', True) else 0}">
</div>
<div class="toggle-row">
  <label class="sw"><input type="checkbox" id="use-cms" {'checked' if s.get('use_cms', True) else ''}><span class="sl"></span></label>
  <div><div class="lbl">Use CMS renderer</div><div class="sub">Turn off to fall back to the legacy template</div></div>
</div>
</section>

<section class="card"><h2>🧱 Page sections</h2>
<p class="hint-sm" style="margin-top:-4px">Flip a switch to show or hide a section (applies instantly). Open “Edit copy” to reword it — complex lists use a compact JSON list.</p>
{section_html}
</section>
"""

    def _saved_niche(self, keyword):
        keyword = (keyword or "").strip().lower()
        for n in self._all_niches():
            if (n["keyword"] or "").strip().lower() == keyword:
                return n
        return None

    def _webhook_url(self):
        """Effective SOCIAL_WEBHOOK — live env first, then whatever was set at
        boot (so a webhook can be flipped on without restarting, and tests can
        inject one at runtime)."""
        return os.environ.get("SOCIAL_WEBHOOK", "") or _SOCIAL_WEBHOOK

    def _social_kits(self, keyword):
        """Six UTM-tracked post kits for the niche's top pick (empty when the
        niche has no products or no saved niche matches). Kit URLs are stable
        per (slug, platform): once a post exists its attribution code is reused,
        so re-publishing flips status instead of forking a new tracked link."""
        n = self._saved_niche(keyword)
        if not n:
            return []
        slug = seo._slugify(n["keyword"])
        kits = social.post_kits(n["keyword"], n["products"] or [],
                                base_url=seo.BASE_URL, slug=slug)
        if not kits:
            return kits
        with _lock:
            conn = _db()
            rows = conn.execute(
                "SELECT platform, utm_content FROM social_posts WHERE lower(slug)=?",
                (slug,)).fetchall()
            conn.close()
        keep = {}
        for r in rows:
            keep.setdefault(r["platform"], r["utm_content"])
        for kit in kits:
            code = keep.get(kit["platform"])
            if code:
                kit["utm_content"] = code
                kit["link"] = social.track_link(seo.BASE_URL, slug,
                                                kit["platform"], code)
        return kits

    def _social_db(self, keyword, slug, kits):
        """Published posts + per-post click counts (attribution via utm_content)."""
        codes = [k.get("utm_content") for k in kits or []]
        with _lock:
            conn = _db()
            rows = conn.execute(
                "SELECT * FROM social_posts WHERE lower(slug)=? ORDER BY id",
                (slug or "",)).fetchall()
            stats = {}
            for code in codes:
                if not code:
                    continue
                stats[code] = conn.execute(
                    "SELECT COUNT(*) c FROM clicks WHERE lower(slug)=? AND content=?",
                    (slug or "", code)).fetchone()["c"]
            extra = [r["utm_content"] for r in rows
                     if r["utm_content"] and r["utm_content"] not in stats]
            for code in extra:
                stats[code] = conn.execute(
                    "SELECT COUNT(*) c FROM clicks WHERE lower(slug)=? AND content=?",
                    (slug or "", code)).fetchone()["c"]
            conn.close()
        return [dict(r) for r in rows], stats

    def _og_image(self, slug):
        """Generated 1200x630 SVG share card — the og:image/twitter:image the
        SEO pages point at. Served publicly + cacheable (static by slug)."""
        for n in self._all_niches():
            try:
                if slug != seo._slugify(n["keyword"]):
                    continue
                items = n["products"] or []
                pick = market_engine.pick_for_buyers(items)
                title = (pick or {}).get("title") or ("Best " + n["keyword"])
                stars = (pick or {}).get("stars")
                reviews = (pick or {}).get("reviews")
                svg = social.og_svg(slug, n["keyword"], title, stars, reviews)
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.send_header("Content-Length", str(len(svg)))
                self.end_headers()
                self.wfile.write(svg)
                return None
            except Exception:
                continue
        return self._send(404, {"error": "og image not found"})

    def _social_post_page(self, slug, code):
        """Public, standalone page for one published post. Renders the actual
        post copy (platform + body) instead of redirecting to the landing page,
        so the admin's "View live" / "open <a>" links show the real post."""
        with _lock:
            conn = _db()
            try:
                row = conn.execute(
                    "SELECT * FROM social_posts WHERE lower(slug)=? AND utm_content=? "
                    "AND status='published' ORDER BY id DESC LIMIT 1",
                    (slug.lower(), code)).fetchone()
            finally:
                conn.close()
        if not row:
            return self._send(404, "<h1>Post not found</h1>", "text/html; charset=utf-8")
        d = dict(row)
        keyword = d.get("keyword") or slug
        title = d.get("name") or ("%s post" % d.get("platform"))
        body = (d.get("body") or "").replace("\n", "<br>")
        link = d.get("link") or "/lp/" + slug
        when = (d.get("published_at") or d.get("created_at") or "").split(" ")[0]
        desc = (("%s · pstore" % keyword)[:160]) if keyword else title
        head = seo._head(title, desc, "/social/%s/%s" % (slug, code),
                         "/social/%s/%s" % (slug, code), noindex=True)
        page = f"""<!DOCTYPE html>
<html lang="en"><head>{head.decode("utf-8")}</head><body>
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<nav><a href="/">Home</a><a href="/blog">Blog</a><a href="/n/{seo._clean(slug)}">Full review</a></nav></header>
<main class="wrap" style="max-width:720px;margin:0 auto;padding:24px">
<section class="card">
  <p class="hint">📣 {seo._clean(d.get('platform') or 'Social')} · {seo._clean(when)}</p>
  <h1>{seo._clean(title)}</h1>
  <div style="font-size:15px;color:var(--text,#333);line-height:1.6">{body}</div>
  <p class="key" style="margin-top:16px"><a href="{seo._clean(link)}" rel="noopener" target="_blank">{seo._clean(link)}</a></p>
  <p class="hint">Best {seo._clean(keyword)} — researched live from Amazon.</p>
</section>
</main>
</body></html>"""
        return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def _social_api(self, q):
        keyword = (q.get("keyword") or [""])[0].strip()
        webhook = bool(self._webhook_url())
        if not keyword:
            return self._send(200, {"keyword": "", "kits": [], "published": [],
                                    "stats": {}, "webhook": webhook})
        kits = self._social_kits(keyword)
        slug = seo._slugify(keyword)
        published, stats = self._social_db(keyword, slug, kits)
        return self._send(200, {"keyword": keyword, "kits": kits,
                                "published": published, "stats": stats,
                                "webhook": webhook})

    def _webhook_publish(self, kits):
        """Fire-and-forget SOCIAL_WEBHOOK POSTs for each published kit, so
        Zapier/Make/browser tools (or a future native API) can post for real.
        Never raises and never blocks the publish response."""
        webhook = self._webhook_url()
        if not webhook or not kits:
            return

        def fire():
            for kit in kits:
                try:
                    req = urllib.request.Request(
                        webhook,
                        data=json.dumps({"body": kit["body"], "link": kit["link"],
                                         "platform": kit["platform"]}).encode("utf-8"),
                        headers={"Content-Type": "application/json"}, method="POST")
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        resp.read()
                except Exception:
                    continue
        threading.Thread(target=fire, daemon=True).start()

    def _social_publish(self):
        """Publish one (or all) post kit(s) for a niche: upsert the social_posts
        row to status 'published' and fire the webhook for genuine posting."""
        body = self._body()
        keyword = str(body.get("keyword") or "").strip()
        platform = str(body.get("platform") or "").strip()
        if not keyword:
            return self._send(400, {"error": "keyword required"})
        kits = self._social_kits(keyword)
        if not kits:
            return self._send(404, {"error": "no saved niche or top pick for that keyword"})
        if platform and platform != "all" and platform not in social.PLATFORMS:
            return self._send(400, {"error": "unknown platform"})
        pick_list = kits if platform == "all" else \
            [k for k in kits if k["platform"] == platform]
        slug = seo._slugify(keyword)
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with _lock:
            conn = _db()
            ids = []
            for kit in pick_list:
                row = conn.execute(
                    "SELECT id FROM social_posts WHERE lower(slug)=? AND utm_content=?",
                    (slug, kit["utm_content"])).fetchone()
                if row:
                    conn.execute(
                        "UPDATE social_posts SET status='published', published_at=?, "
                        "name=?, body=?, link=? WHERE id=?",
                        (now, kit["name"], kit["body"], kit["link"], row["id"]))
                    ids.append(row["id"])
                else:
                    cur = conn.execute(
                        "INSERT INTO social_posts (slug, keyword, platform, name, body, "
                        "link, utm_content, status, published_at) VALUES (?,?,?,?,?,?,?, "
                        "'published', ?)",
                        (slug, keyword, kit["platform"], kit["name"], kit["body"],
                         kit["link"], kit["utm_content"], now))
                    ids.append(cur.lastrowid)
            conn.commit()
            conn.close()
        self._webhook_publish(pick_list)
        _, stats = self._social_db(keyword, slug, kits)
        return self._send(200, {"ok": True, "published": len(ids), "posts": pick_list,
                                "stats": stats, "webhook": bool(self._webhook_url()),
                                "keyword": keyword})

    def _admin_social(self, q):
        niches = [n["keyword"] for n in self._all_niches()]
        keyword = (q.get("keyword") or [""])[0].strip() or (niches[0] if niches else "")
        opts = "".join('<option value="%s"%s>%s</option>' % (seo._clean(k),
                        ' selected' if k == keyword else "", seo._clean(k)) for k in niches)
        if keyword:
            kits = self._social_kits(keyword)
            slug = seo._slugify(keyword)
            published, stats = self._social_db(keyword, slug, kits)
        else:
            kits, published, stats, slug = [], [], {}, ""
        webhook_state = ("<b>Configured</b> — publishing also fires your <code>SOCIAL_WEBHOOK</code>."
                         if self._webhook_url() else
                         "Not set — one click here flips the post to <b>published</b> and shows the "
                         "copy-ready kit to paste anywhere. Set <code>SOCIAL_WEBHOOK</code> to also "
                         "POST <code>{body, link, platform}</code> to Zapier/Make for real posting.")
        kit_cards = []
        for kit in kits:
            hashing = stats.get(kit["utm_content"]) or 0
            badge = ""
            for p in published:
                if p["utm_content"] == kit["utm_content"] and p["status"] == "published":
                    badge = "<span class='badge' style='background:#e6ffe8;color:#1e8e3e'>published %s</span>" % \
                        seo._clean(p.get("published_at") or "")
                    break
            kit_cards.append(f"""<div class="sub soc-kit">
<h3>📣 {seo._clean(kit['platform'])} <span class="who">· {seo._clean(kit['name'])}</span> {badge} <span class="who">· {hashing} click(s) on this post</span></h3>
<textarea readonly rows="5">{seo._clean(kit['body'])}</textarea>
<p class="key" title="Tracked link (UTM) — every share uses this exact URL">{seo._clean(kit['link'])}</p>
<div class="row">
<button class="warm soc-pub" data-kw="{seo._clean(keyword)}" data-platform="{seo._clean(kit['platform'])}">Publish</button>
<button class="soc-copy">Copy post</button>
<a class="btn" target="_blank" rel="noopener" href="/social/{seo._clean(slug)}/{seo._clean(kit['utm_content'])}">View live ↗</a>
</div>
</div>""")
        kit_html = "".join(kit_cards) if kit_cards else \
            '<p class="hint">Choose a saved niche with products — each kit turns its top pick into a tracked post.</p>'
        pub_rows = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='ct'>%s</td>"
            "<td class='ct'><a target='_blank' rel='noopener' href='%s'>open ↗</a></td></tr>"
            % (seo._clean(p["platform"]), seo._clean(p["name"]), seo._clean(p["utm_content"]),
               seo._clean(p["published_at"] or p["created_at"]),
               stats.get(p["utm_content"]) or 0,
               "/social/" + seo._clean(p["slug"]) + "/" + seo._clean(p["utm_content"]))
            for p in published) or \
            "<tr><td colspan='6' class='hint'>Nothing published yet — hit Publish above.</td></tr>"
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Social — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
<style>
.soc-kit textarea {{ width:100%; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; }}
.soc-kit .key {{ margin:8px 0 0; word-break:break-all; }}
</style>
</head><body>
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>One-click <span>social publishing.</span></h1>
<p class="tagline">Every post is pre-written, UTM-tracked and points back to the niche landing page — publish here, then watch clicks land in Analytics.</p></div>
{self._admin_nav('social')}
</header>
<main>
<section class="card"><h2>📣 Tracked post kits</h2>
<p class="hint" style="margin-top:-4px">Webhook: {webhook_state}</p>
<form class="row" method="get" action="/admin/social">
  <label>Niche <select name="keyword" onchange="this.form.submit()">{opts}</select></label>
</form>
<div class="cols">{kit_html}</div>
<p id="out" class="msg"></p>
</section>
<section class="card"><h2>✅ Published posts &amp; clicks</h2>
<p class="hint">Each code counts clicks on the landing page with that exact UTM tag — so you can see, per post, who clicked through.</p>
<div class="table-wrap"><table class="plain"><thead><tr><th>Platform</th><th>Post</th><th>Code</th><th>Published</th><th>Clicks</th><th>Live</th></tr></thead>
<tbody>{pub_rows}</tbody></table></div></section>
</main>
<footer><p>Posts only use real scraped data (title, price, stars, reviews) — no fabricated claims. Landing page carries the courier beacon, so every tap is attributed.</p></footer>
<script src="/table-flow.js" defer></script>
{_TOTOP}
<script>
function $(id){{return document.getElementById(id);}}
async function pub(btn){{
  $("out").textContent = "Publishing…";
  const r = await fetch("/api/social/publish", {{method:"POST", headers:{{"Content-Type":"application/json"}},
    body: JSON.stringify({{keyword: btn.dataset.kw, platform: btn.dataset.platform}})}});
  const d = await r.json().catch(()=>({{ok:false}}));
  $("out").textContent = d && d.ok
    ? "Published " + d.published + " post(s) for “" + d.keyword + "”. See the table below."
    : (d && d.error) || "Publish failed.";
  setTimeout(()=>location.reload(), 900);
}}
document.addEventListener("click", (e)=>{{
  const p = e.target.closest(".soc-pub");
  if (p){{ pub(p); return; }}
  const c = e.target.closest(".soc-copy");
  if (c){{
    const box = c.closest(".soc-kit");
    const body = box.querySelector("textarea").value;
    const link = box.querySelector(".key").textContent.trim();
    const text = body + "\\n\\n" + link;
    navigator.clipboard ? navigator.clipboard.writeText(text).then(()=>{{const t=c.textContent;c.textContent="Copied ✓";setTimeout(()=>c.textContent=t,1200);}}) : (document.execCommand("copy"), c.textContent="Copied ✓");
  }}
}});
</script>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")


# ------------------------------------------------------------------ SEM / SEO suite
    def _seo_audit_payload(self):
        """Refresh the audit off the current saved niches (no network)."""
        return seo.audit_sites(self._all_niches())

    def _admin_seo(self):
        """SEO audit hub: indexability of every saved niche + site-level config."""
        audit = self._seo_audit_payload()
        with _lock:
            _conn = _db()
            try:
                active_subs = _conn.execute(
                    "SELECT COUNT(*) c FROM subscribers WHERE confirmed=1 AND unsubscribed=0"
                ).fetchone()["c"]
            finally:
                _conn.close()
        strip = (
            '<div class="feature"><h3>%d</h3><p class="hint">niches saved</p></div>'
            '<div class="feature"><h3>%d</h3><p class="hint">fully indexable</p></div>'
            '<div class="feature"><h3>%d</h3><p class="hint">need work</p></div>'
            '<div class="feature"><h3>%d</h3><p class="hint">active subscribers</p></div>'
            '<div class="feature"><h3>%s</h3><p class="hint">Search Console token</p></div>'
            % (audit["count"], audit["indexable"], audit["needs_work"],
               active_subs,
               "✓ set" if audit["google_verification"] else "—"))
        rows = ""
        for r in audit["niches"]:
            c = r["checks"]
            def mark(ok):
                return ('<span class="badge" style="background:#e6ffe8;color:#1e8e3e">ok</span>'
                        if ok else '<span class="badge" style="background:#ffe6e6;color:#c0392b">fix</span>')
            rows += (
                "<tr class='%s'>"
                "<td class='ct'><a href='%s'>%s</a></td>"
                "<td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % ("top" if r["indexable"] else "",
                   seo._clean(r["url"]), seo._clean(r["keyword"]),
                   r["products"],
                   mark(c.get("title_ok", False)), mark(c.get("desc_ok", False)),
                   mark(c.get("schema", False)), mark(c.get("og_image", False)),
                   mark(c.get("word_count", False)),
                   ("indexable" if r["indexable"] else "noindex")))
        if not rows:
            rows = "<tr><td colspan='8' class='hint'>No saved niches yet — mine one on the dashboard.</td></tr>"
        gsc_state = ('<span style="color:#1e8e3e">Configured</span> — the site emits your google-site-verification meta.' if audit["google_verification"]
                     else '<span style="color:#c0392b">Not set</span> — prove Search Console ownership to get the site indexed.')
        site_keys = ('<div class="row" style="align-items:stretch;margin-top:10px">'
                     '<div class="feature"><h3>✓</h3><p class="hint">Search Console token<br>'
                     '<a href="/keys/site/gsc">/keys/site/gsc ↗</a></p></div>'
                     '<div class="feature"><h3>✓</h3><p class="hint">IndexNow key<br>'
                     '<a href="/keys/site/indexnow">/keys/site/indexnow ↗</a></p></div>'
                     '<div class="feature"><h3>✓</h3><p class="hint">Sitemap submit<br>'
                     '<a href="/keys">all keys hub ↗</a></p></div></div>')
        engine_link = lambda name, url: (
            '<tr><td>%s</td><td class="key ct"><a href="%s" target="_blank" rel="noopener">%s ↗</a></td>'
            '<td class="key ct" onclick="navigator.clipboard&&navigator.clipboard.writeText(this.textContent)" '
            'title="click to copy">%s</td></tr>'
            % (name, seo._clean(url), name, seo._clean(audit['sitemap'])))
        engines = ("".join([
            engine_link("Google Search Console",
                        "https://search.google.com/search-console?resource_id=" + urllib.parse.quote(audit['site_url'], safe="")
                        + "&hl=en"),
            engine_link("Bing / IndexNow", "https://www.bing.com/indexnow"),
            engine_link("Yandex Webmaster", "https://webmaster.yandex.com/"),
            engine_link("Yahoo (via Bing)", "https://www.bing.com/webmasters"),
        ]))
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEO audit — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
<style>.audit-note{{max-width:720px}}</style>
</head><body>
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>SEO <span>audit.</span></h1>
<p class="tagline">Every saved niche, checked against the same rules its live page is served with — title/description length, structured data, share image and indexability.</p></div>
{self._admin_nav('seo')}
</header>
<main>
<section class="card"><h2>🔍 Site health</h2>
<div class="row" style="align-items:stretch">{strip}</div>
<p class="hint" style="margin-top:10px">Search Console owner token: {gsc_state}</p>
<p class="hint" style="margin-top:6px">Sitemap <a href="{seo._clean(audit['sitemap'])}">{seo._clean(audit['sitemap'])}</a> · Robots <a href="{seo._clean(audit['robots'])}">{seo._clean(audit['robots'])}</a> · Canonical base <code>{seo._clean(audit['site_url'])}</code></p>
{site_keys}
<div class="sub"><h3>🚀 Push to the engines now</h3>
<p class="hint">Ping IndexNow with every live URL ({audit['count']} niches → {len(self._all_urls())} URLs). Bing, Yandex, Naver & Seznam crawl in minutes. To also reach Google, verify ownership via <a href="/keys/site/gsc">/keys/site/gsc</a> and submit the sitemap in Search Console.</p>
<button id="submitNow" class="warm">▶ Submit all URLS to IndexNow</button>
<p id="inmsg" class="msg"></p>
<div class="table-wrap"><table class="plain"><thead><tr><th>Engine</th><th>Console / submit</th><th>Sitemap to submit (click to copy)</th></tr></thead><tbody>{engines}</tbody></table></div></div>
</section>
<section class="card"><h2>📄 Per-niche checks</h2>
<p class="hint" style="margin-top:-4px">Green = passes the live-page rule. Red = the page is served with that gap today. Titles 30–60 chars, descriptions 70–160.</p>
<div class="table-wrap"><table class="plain"><thead><tr>
<th>Niche</th><th>Products</th><th>Title</th><th>Desc</th><th>Schema</th><th>Share img</th><th>Word count</th><th>Status</th>
</tr></thead><tbody>{rows}</tbody></table></div></section>
</main>
<footer><p>Audit reflects the live pages, not aspirational settings. Set the Search Console and IndexNow keys under <a href="/keys">Keys ↗</a> — pick <code>PSTORE_GOOGLE_SITE_VERIFICATION</code> env or save it there for the running process.</p></footer>
<script>
const $=s=>document.getElementById(s);
if ($("submitNow")) $("submitNow").onclick = async () => {{
  const m = $("inmsg"); m.textContent = "Submitting all URLs…"; m.className = "msg";
  let d, r;
  try {{ r = await fetch("/api/indexnow", {{method:"POST", headers:{{"Content-Type":"application/json"}}, body:"{{}}"}}); d = await r.json(); }}
  catch(e) {{ m.textContent = "✗ Could not reach the server."; return; }}
  m.textContent = (d.ok ? "✓ IndexNow " : "✗ ") + (d.message || "");
  m.className = d.ok ? "msg" : "msg";
}};
</script>
<script src="/table-flow.js" defer></script>
{_TOTOP}
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _admin_manual(self):
        """In-app user manual: a visual, fully cross-linked guide to every tool,
        page and feature, with an optional printable PDF download."""
        body = manual.render_admin_manual(self._admin_nav("manual"), _TOTOP)
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _admin_manual_pdf(self):
        """Serve the styled, printable PDF companion to the user manual."""
        data = manual.build_pdf()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", 'attachment; filename="pstore-user-manual.pdf"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        return None

    def _admin_refresh(self, q):
        """Manual niche data refresh: re-mine one or all saved niches now, and
        show the auto-refresh schedule so prices/ratings/stock stay current."""
        with _lock:
            conn = _db()
            rows = conn.execute(
                "SELECT keyword, products, updated_at FROM niches "
                "ORDER BY CASE WHEN updated_at IS NULL THEN 0 ELSE 1 END, updated_at ASC").fetchall()
            conn.close()
        now = time.time()
        rows_html = ""
        for r in rows:
            kw = r["keyword"]
            prods = json.loads(r["products"] or "[]")
            updated = r["updated_at"]
            if not updated:
                when = '<span class="badge" style="background:#ffe9e9;color:#c0392b">never refreshed</span>'
                stale = True
            else:
                try:
                    parsed = time.mktime(time.strptime(updated[:19], "%Y-%m-%d %H:%M:%S"))
                    age_min = int((now - parsed) // 60)
                except (ValueError, TypeError):
                    age_min = 0
                stale = age_min >= _REFRESH_STALE_MIN
                when = ("%s · <b>%d min ago</b>" % (updated, age_min))
                if stale:
                    when += ' <span class="badge" style="background:#ffe9e9;color:#c0392b">stale</span>'
                else:
                    when += ' <span class="badge" style="background:#e6ffe8;color:#1e8e3e">fresh</span>'
            rows_html += (
                "<tr class='%s'><td class='ct'>%s</td><td>%d</td><td>%s</td>"
                "<td><button class='mini' data-kw=\"%s\">Refresh now</button></td></tr>"
                % ("top" if stale else "", seo._clean(kw), len(prods), when,
                   seo._clean(kw)))
        if not rows_html:
            rows_html = "<tr><td colspan='4' class='hint'>No saved niches yet — mine one on the dashboard.</td></tr>"
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Data refresh — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
</head><body>
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>Niche data <span>refresh.</span></h1>
<p class="tagline">Re-mine saved niches so prices, ratings and stock reflect current Amazon listings. Manual here, plus an automatic loop runs on a schedule.</p></div>
{self._admin_nav('refresh')}
</header>
<main>
<section class="card"><h2>🔄 Schedule &amp; status</h2>
<div class="row" style="align-items:stretch">
<div class="feature"><h3>{_REFRESH_INTERVAL_SEC}s</h3><p class="hint">auto-loop interval (0 = off)</p></div>
<div class="feature"><h3>{_REFRESH_STALE_MIN}m</h3><p class="hint">staleness window</p></div>
<div class="feature"><h3>{_REFRESH_MAX_PER_CYCLE}</h3><p class="hint">niches per cycle</p></div>
</div>
<p id="status" class="hint" style="margin-top:8px">Fetching status…</p>
</section>
<section class="card"><h2>🛍 Saved niches</h2>
<div class="row" style="margin-bottom:8px">
<button id="refresh-all" class="warm">Refresh all now</button>
</div>
<p id="out" class="msg"></p>
<div class="table-wrap"><table class="plain"><thead><tr>
<th>Niche</th><th>Products</th><th>Last refreshed</th><th></th>
</tr></thead><tbody>{rows_html}</tbody></table></div></section>
</main>
<footer><p>Each refresh re-runs Amazon autosuggest + product search for that niche. Prices move — refreshing keeps the live prices honest on every page.</p></footer>
<script src="/table-flow.js" defer></script>
{_TOTOP}
<script>
function $(id){{return document.getElementById(id);}}
async function fresh() {{
  try {{
    const r = await fetch("/api/refresh/status");
    const d = await r.json();
    $("status").textContent = d.total + " niches · " + d.refreshed + " refreshed · " + d.stale + " stale · in-flight: " + (d.inflight.length ? d.inflight.join(", ") : "none");
  }} catch(e) {{ $("status").textContent = "Status unavailable."; }}
}}
async function run(path, body, out) {{
  out.textContent = "Working…";
  try {{
    const r = await fetch(path, {{method:"POST", headers:{{"Content-Type":"application/json"}}, body: JSON.stringify(body)}});
    const d = await r.json();
    out.textContent = (d.status === "started")
      ? "Queued " + (d.queued||0) + " niche(s) — refreshing in the background. Watch the status line / table update."
      : ((d.status === "ok") ? "Refreshed: " + d.keyword + " (" + d.products + " products) — new score " + d.score + "."
                              : ("Failed: " + (d.error || d.status || "unknown")));
  }} catch(e) {{ out.textContent = "Request failed."; }}
  fresh(); setTimeout(()=>location.reload(), 1200);
}}
$("refresh-all").onclick = () => {{ run("/api/refresh-all", {{}}, $("out")); }};
document.querySelectorAll("button.mini[data-kw]").forEach(b => {{
  b.onclick = () => {{ run("/api/refresh", {{keyword: b.dataset.kw}}, $("out")); }};
}});
fresh();
</script>
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _seo_audit_api(self):
        return self._send(200, self._seo_audit_payload())

    def _sem_payload(self, keyword):
        keyword = (keyword or "").strip()
        if not keyword:
            return {"keyword": "", "error": "keyword required", "brief": None}
        niche = self._saved_niche(keyword)
        if not niche:
            return {"keyword": keyword, "error": "no saved niche matches that keyword", "brief": None}
        audit_row = seo.audit_niche(niche)
        entries = [seo._slugify(n["keyword"]) for n in self._all_niches()]
        return sem.brief(keyword, niche, seo.BASE_URL,
                         audit_row=audit_row, indexnow_key=indexnow.key(),
                         sitemap_entries=entries)

    def _sem_api(self, q):
        keyword = (q.get("keyword") or [""])[0].strip()
        return self._send(200, self._sem_payload(keyword))

    def _admin_sem(self, q):
        niches = [n["keyword"] for n in self._all_niches()]
        keyword = (q.get("keyword") or [""])[0].strip() or (niches[0] if niches else "")
        opts = "".join('<option value="%s"%s>%s</option>' % (seo._clean(k),
                        ' selected' if k == keyword else "", seo._clean(k)) for k in niches)
        if not keyword:
            return self._send(200, ("""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEM — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow"></head><body>
<header><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>Search <span>funnel.</span></h1>
<p class="tagline">Turn a review page into a search funnel — long-tail expansion, intent brief, people-also-ask and a performance checklist.</p></div>
{self._admin_nav('sem')}</header><main>
<section class="card"><h2>🎯 Search-engine marketing</h2>
<p class="hint">No saved niches yet — mine one on the dashboard first.</p></section></main></body></html>""").encode("utf-8"), "text/html; charset=utf-8")
        brief = self._sem_payload(keyword)
        info = brief or {}
        if not info:
            return self._send(200, ("<h3>%s</h3><p>%s</p>" % (seo._clean(keyword),
                               seo._clean(brief.get("error") or "no brief"))).encode("utf-8"),
                              "text/html; charset=utf-8")

        lt = info.get("longtail") or []
        intent = info.get("intent") or {}
        paa = info.get("paa") or []
        perf = info.get("performance") or []
        page = info.get("page") or {}
        boosts = self._boosts_for(keyword) if keyword else []
        long_phrases = [g.get("phrase") for g in lt if g.get("phrase")]
        boost_kw = " · ".join(long_phrases[:3]) if long_phrases else "none yet"
        boost_rows = "".join(
            '<div class="sub"><h3>🚀 %s</h3>'
            '<p class="who">%s ✓ · %d run · %d clicks</p>'
            '<p class="key"><a href="%s">%s</a></p></div>'
            % (seo._clean(b["name"]),
               b["status"], b["runs"] or 0, b["clicks"] or 0,
               seo._clean(b["link"] or "#"), seo._clean(b["link"] or "—"))
            for b in boosts) or '<p class="hint">Run boosts on the Workbench — they weave these long-tail terms in.</p>'

        def chip(t, s):
            return '<span class="badge %s">%s</span>' % (s, t)
        lt_html = "".join(
            '<div class="sub"><h3>%s %s</h3>'
            '<p class="key">/n/%s</p></div>'
            % (seo._clean(g["phrase"]), chip(g["intent"],
                 "source" if g["intent"] == "target" else "demand"),
               seo._clean(g["slug"])) for g in lt) or '<p class="hint">No suggestions yet.</p>'
        fixes_html = "".join(
            '<li><b>%s</b> — %s</li>' % (seo._clean(f["label"]), seo._clean(f["detail"]))
            for f in (intent.get("fixes") or []))
        paa_html = "".join(
            ('<details class="faq"><summary>%s</summary>'
             '<p>%s</p></details>' % (seo._clean(qa["question"]),
                                      seo._clean(qa["answer"])))
            for qa in paa) or '<p class="hint">No questions yet.</p>'
        perf_html = "".join(
            '<div class="sub"><h3>✓ %s</h3><p class="who">%s</p></div>'
            % (seo._clean(p["label"]), seo._clean(p["detail"])) for p in perf)
        page_state = ("<b>in sitemap</b>" if page.get("sitemap") else "not in sitemap") + \
            (" · <b>IndexNow key ready</b>" if page.get("indexnow_key") else " · IndexNow key missing")
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEM — {seo._clean(keyword)} · pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
<style>.key{{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:8px 10px;margin:4px 0 0;word-break:break-all}}</style>
</head><body>
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>Search <span>funnel.</span></h1>
<p class="tagline">Turn the “best {seo._clean(keyword)}” review page into a search funnel — long-tail expansion, intent brief, people-also-ask and a performance checklist.</p></div>
{self._admin_nav('sem')}
</header>
<main>
<section class="card"><h2>🎯 Choose a niche</h2>
<form class="row" method="get" action="/admin/sem">
  <label>Niche <select name="keyword" onchange="this.form.submit()">{opts}</select></label>
</form></section>
<section class="card"><h2>🔎 Intent brief</h2>
<p class="hint">The one job of this page, stated plainly, with concrete copy targets.</p>
<div class="sub"><h3>Primary question</h3><p class="who">{seo._clean(intent.get('primary_question',''))}</p></div>
<div class="sub"><h3>Search intent</h3><p class="who">{seo._clean(intent.get('search_intent',''))}</p></div>
<div class="sub"><h3>H1 target</h3><p class="key">{seo._clean(intent.get('h1_target',''))}</p></div>
<div class="sub"><h3>Meta target</h3><p class="key">{seo._clean(intent.get('meta_target',''))}</p></div>
<div class="sub"><h3>Quick wins</h3><ul>{fixes_html}</ul></div></section>
<section class="card"><h2>🗂 Long-tail expansion</h2>
<p class="hint">Related searches (from Amazon's autosuggest) the page should ideally cover — view each as its own crawlable URL.</p>
{lt_html}</section>
<section class="card"><h2>❓ People also ask</h2>
<p class="hint">Copy-ready FAQ prompts in SERP phrasing. <code>__blank__</code> answers are yours to fill in with honest copy.</p>
{paa_html}</section>
<section class="card"><h2>⚡ Performance checklist</h2>
{perf_html}</section>
<section class="card"><h2>🌐 Live URL status</h2>
<p class="hint" style="margin-top:-4px">Page status: {page_state}</p>
<div class="sub"><h3>Canonical</h3><p class="key"><a href="{seo._clean(page.get('canonical',''))}">{seo._clean(page.get('canonical',''))}</a></p></div>
<div class="sub"><h3>Share image</h3><p class="key"><a href="{seo._clean(page.get('og_image',''))}">{seo._clean(page.get('og_image',''))}</a></p></div>
<div class="sub"><h3>Landing page</h3><p class="key"><a href="{seo._clean(page.get('landing_url',''))}">{seo._clean(page.get('landing_url',''))}</a></p></div></section>
<section class="card"><h2>🚀 Long-tail boosts</h2>
<p class="hint">These campaigns weave the {seo._clean(boost_kw or 'long-tail')} terms above into social-ready promo copy, each with a stable UTM-tracked link back to the landing page. Run them in the Workbench.</p>
<p class="hint"><b>Fold-in phrases:</b> {seo._clean(boost_kw)}</p>
{boost_rows}
<p class="hint"><a href="/tool?keyword={seo._clean(keyword)}">Open Workbench for “{seo._clean(keyword)}” →</a></p></section>
</main>
<footer><p>The funnel grows the keyword pool around each review page. All suggestions are honest — we never assert traffic or rankings we can't observe.</p></footer>
{_TOTOP}
</body></html>"""
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")


# ------------------------------------------------------------------ email suite
    def _record_click(self, slug, source="page", referrer="", asin="", content=""):
        ip = security.ip_token(self._client_ip())
        with _lock:
            conn = _db()
            conn.execute(
                "INSERT INTO clicks (slug, source, ip, referrer, asin, content) VALUES (?,?,?,?,?,?)",
                (slug, source, ip, referrer, asin, content))
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
        # Signed, short-lived token lets this just-opted-in visitor grab the
        # gated PDF lead magnet immediately (Cialdini's reciprocity in action).
        token = security.make_token("pdf:" + keyword, 10 * 60) if keyword else ""
        return self._send(200, {"ok": True, "id": sid, "message": msg,
                                "download_token": token})

    def _gated_pdf(self, q):
        """Public endpoint that serves the niche's PDF lead magnet:
        - when a page's settings have pdf_gated=false the PDF is served freely
          (reciprocity without an email wall);
        - otherwise a token from /subscribe (HMAC-signed, scoped to the exact
          keyword, short-lived) is required so only just-opted-in visitors can
          grab the lead magnet.
        """
        keyword = (q.get("keyword") or [""])[0].strip()
        if not keyword:
            return self._send(403, {"error": "not authorized"})
        gated = True
        with _lock:
            conn = _db()
            page = cms_mod.get_page(conn, keyword)
            if page and (page.get("settings") or {}).get("pdf_gated") is False:
                gated = False
            conn.close()
        if gated:
            token = (q.get("token") or [""])[0].strip()
            if not token or security.verify_token(token) != "pdf:" + keyword:
                return self._send(403, {"error": "not authorized"})
        book = self._ebook_for(keyword)
        if not book:
            return self._send(404, {"error": "guide not found"})
        data = book.get("pdf") or b""
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % book.get("pdf_name", "guide.pdf"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        return None

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
        content = str(body.get("content") or (q.get("content") or [""])[0]).strip()[:40]
        self._record_click(slug, source, referrer, asin, content)
        return self._send(200, {"ok": True})

    def _page_view(self):
        """Public pageview/event beacon (GET or POST). Records lead-page + public
        site activity (views, promo/countdown/sticky clicks, PDF downloads) —
        rate-limited and IP-anonymized, same privacy posture as /api/track."""
        key = "pv|" + security.client_key(self.headers, self._client_ip())
        if not security.PAGEVIEW_LIMITER.hit(key):
            return self._send(200, {"ok": True})
        parsed = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        body = self._body()
        source = str(body.get("source") or (q.get("source") or [""])[0]).strip()[:40]
        entry = {
            "slug": str(body.get("slug") or (q.get("slug") or ["page"])[0]).strip()[:120] or "page",
            "page": (body.get("page") or (q.get("page") or [""])[0]).strip()[:160] or "/",
            "name": (body.get("name") or (q.get("name") or ["view"])[0]).strip()[:40] or "view",
            "keyword": (body.get("keyword") or (q.get("keyword") or [""])[0]).strip()[:120],
            "source": source or "organic",
        }
        if entry["slug"] in ("page", "") and not entry["keyword"]:
            entry["slug"] = entry["page"].strip("/").split("?")[0][:120] or "page"
        self._record_event(entry)
        return self._send(200, b'{"ok": true}', "application/json")

    def _record_event(self, e):
        e = {k: (v or "").strip()[:k_limit] for k, (v, k_limit) in {
            "slug": (e.get("slug"), 120), "page": (e.get("page"), 160),
            "name": (e.get("name"), 40), "keyword": (e.get("keyword"), 120),
            "source": (e.get("source"), 40)}.items()}
        with _lock:
            conn = _db()
            conn.execute(
                "INSERT INTO events (slug, page, name, keyword, source) "
                "VALUES (?,?,?,?,?)",
                (e["slug"], e["page"], e["name"] or "view",
                 e["keyword"], e["source"]))
            conn.commit()
            conn.close()

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
        try:
            limit = int(body.get("limit") or 0)
        except (TypeError, ValueError):
            limit = 0
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
            ready.append((sub["id"], idx, mail, sub["email"], sub["first_name"] or ""))
        cap = limit if limit and limit > 0 else mailer.MAX_EMAILS_PER_RUN
        target = ready[:cap] if limit and limit > 0 else ready[:mailer.MAX_EMAILS_PER_RUN]
        eff_limit = min(len(ready), cap)
        if dry:
            return self._send(200, {"ok": True, "dry_run": True, "sent": 0, "errors": 0,
                                    "ready": eff_limit, "skipped": len(ready) - eff_limit,
                                    "keyword": niche_kw or None,
                                    "limit": cap})
        sent = errors = 0
        for sid, idx, mail, to, to_name in target:
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
                                "ready": eff_limit, "skipped": len(ready) - eff_limit,
                                "keyword": niche_kw or None,
                                "limit": cap})

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
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>Email <span>capture &amp; auto-send.</span></h1>
<p class="tagline">The opt-in widget on every niche page feeds this list. Send the next email in the 5-step buyer sequence to active subscribers.</p></div>
{self._admin_nav('emails')}
</header>
<main>
 <section class="card"><h2>📨 Sender status</h2>
 <p class="hint" style="margin-top:-4px">{smtp_state}</p>
 <div class="row">
   <label>Recipients
     <select id="count">
       <option value="0" selected>All ready (up to {mailer.MAX_EMAILS_PER_RUN} per run)</option>
       <option value="5">First 5</option>
       <option value="10">First 10</option>
       <option value="25">First 25</option>
     </select>
   </label>
   <button id="send" class="warm">Send next batch</button>
   <label style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" id="dry" style="width:auto;height:auto" checked> dry run</label>
 </div>
 <p id="out" class="msg"></p></section>
 <section class="card"><h2>👥 Subscribers</h2>
 <p class="hint">{stats['total']} total · {stats['active']} active · {stats['unsubscribed']} unsubscribed · {stats['emails_sent']} emails sent</p>
 <div class="table-wrap"><table class="plain"><thead><tr><th>Email</th><th>Niche</th><th>Sequence</th><th>State</th><th>Joined</th></tr></thead>
 <tbody>{self._subs_table(rows)}</tbody></table></div></section>
<section class="card"><h2>💌 Sequence preview</h2>
<form class="row" method="get" action="/admin/emails">
  <label>Niche <select name="keyword" onchange="this.form.submit()">{opts}</select></label>
</form>
{seq_html}</section>
</main>
<footer><p>Emails greet each reader by name — <code>{{first_name}}</code> uses the captured name (or one derived from their email), and <code>{{your_name}}</code> signs as “{seo._clean(mailer.STORE_NAME)}” (set <code>PSTORE_NAME</code> to change it). Only real scraped data, and every email carries an unsubscribe link.</p></footer>
<script src="/table-flow.js" defer></script>
{_TOTOP}
<script>
function $(id){{return document.getElementById(id);}}
$("send").onclick = async () => {{
  $("out").textContent = "Working…";
  const r = await fetch("/api/sequence/send", {{method:"POST", headers:{{"Content-Type":"application/json"}},
    body: JSON.stringify({{dry_run: $("dry").checked, limit: parseInt($("count").value || "0", 10)}})}});
  const d = await r.json().catch(()=>({{ok:false, error:"bad response"}}));
  if (d && d.ok) {{
    if (d.dry_run) $("out").textContent = "Dry run: " + d.ready + " subscriber(s) ready for the next email (limit " + (d.limit||d.ready) + ").";
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
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
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
{_TOTOP}
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
            views = conn.execute(
                "SELECT COUNT(*) c FROM events WHERE name='view'").fetchone()["c"]
            event_breakdown = conn.execute(
                "SELECT name, COUNT(*) c FROM events GROUP BY name ORDER BY c DESC LIMIT 12").fetchall()
            event_pages = conn.execute(
                "SELECT page, COUNT(*) c FROM events WHERE name='view' "
                "GROUP BY page ORDER BY c DESC LIMIT 10").fetchall()
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
        ev_rows = "".join(
            "<tr><td>%s</td><td class='ct'>%d</td></tr>"
            % (seo._clean(r["name"]), r["c"]) for r in event_breakdown
        ) or "<tr><td colspan='2' class='hint'>No pageview events yet.</td></tr>"
        ev_page_rows = "".join(
            "<tr><td>%s</td><td class='ct'>%d</td></tr>"
            % (seo._clean(r["page"] or "—"), r["c"]) for r in event_pages
        ) or "<tr><td colspan='2' class='hint'>Views appear as pages load.</td></tr>"
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analytics — pstore</title><link rel="stylesheet" href="/style.css">
<meta name="robots" content="noindex,nofollow">
</head><body>
<header id="top"><a class="logo" href="/"><span class="mark">P</span><span>pstore</span></a>
<div class="hero"><h1>Click <span>analytics.</span></h1>
<p class="tagline">Privacy-first: IPs are stored as one-way hashes, never raw. See which niches and sources actually earn clicks.</p></div>
{self._admin_nav('analytics')}
</header>
<main>
<section class="card"><h2>📊 At a glance</h2>
<div class="row" style="align-items:stretch">
  <div class="feature"><h3>{total}</h3><p class="hint">total clicks</p></div>
  <div class="feature"><h3>{len(top_slugs)}</h3><p class="hint">niches clicked</p></div>
  <div class="feature"><h3>{views}</h3><p class="hint">page views (leads + site)</p></div>
  <div class="feature"><h3>{stats['active']}</h3><p class="hint">active subscribers</p></div>
  <div class="feature"><h3>{stats['emails_sent']}</h3><p class="hint">emails sent</p></div>
</div></section>
<section class="card"><h2>👀 Page views by URL</h2>
<div class="table-wrap"><table class="plain"><thead><tr><th>Page</th><th>Views</th></tr></thead><tbody>{ev_page_rows}</tbody></table></div></section>
<section class="card"><h2>⚡ Lead-page interactions</h2>
<p class="hint">Promo taps, countdown timing, sticky-CTAs, gate unlocks and lead-PDF downloads — every element tagged <code>[data-ev]</code> on the landing/build pages.</p>
<div class="table-wrap"><table class="plain"><thead><tr><th>Event</th><th>Count</th></tr></thead><tbody>{ev_rows}</tbody></table></div></section>
<section class="card"><h2>🛒 Top clicked pages</h2>
<div class="table-wrap"><table class="plain"><thead><tr><th>Niche</th><th>Clicks</th></tr></thead><tbody>{slug_rows}</tbody></table></div></section>
<section class="card"><h2>🏆 Most-clicked products</h2>
<p class="hint">Which ASIN earns the taps, per niche — use it to pick which products to push in emails and which pages deserve more variants.</p>
<div class="table-wrap"><table class="plain"><thead><tr><th>ASIN</th><th>Niche</th><th>Clicks</th></tr></thead><tbody>{product_rows}</tbody></table></div></section>
<section class="card"><h2>📥 By source</h2>
<div class="table-wrap"><table class="plain"><thead><tr><th>Source</th><th>Clicks</th></tr></thead><tbody>{src_rows}</tbody></table></div></section>
<section class="card"><h2>🧾 Recent clicks</h2>
<div class="table-wrap"><table class="plain"><thead><tr><th>Niche</th><th>Source</th><th>Referrer</th><th>When</th></tr></thead><tbody>{recent_rows}</tbody></table></div></section>
</main>
<footer><p>Views + interactions are captured privacy-first (IP hashes only) via /api/pageview; clicks via /api/track. Promo/countdown/sticky/gate elements are auto-tagged so you can see exactly which page behavior earns engagement and conversions.</p></footer>
<script src="/table-flow.js" defer></script>
{_TOTOP}
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
    if _REFRESH_INTERVAL_SEC > 0:
        threading.Thread(target=_auto_refresh_loop, daemon=True).start()
        print("niche auto-refresh: every %ds, stale after %dm, %d/cycle"
              % (_REFRESH_INTERVAL_SEC, _REFRESH_STALE_MIN, _REFRESH_MAX_PER_CYCLE))
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("pstore running on http://localhost:%d" % PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

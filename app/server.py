# -*- coding: utf-8 -*-
"""Mazon HTTP server: serves the static UI + JSON API on http.server (stdlib).

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
import json
import os
import re
import sqlite3
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import amazon
import market_engine
import niche
import seo

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
DB = os.path.join(ROOT, "mazon.db")
PORT = int(os.environ.get("PORT", "8765"))

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

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)
        try:
            # SEO + marketing routes first (crawlable + short links)
            if path == "/robots.txt":
                return self._send(200, seo.render_robots(), "text/plain; charset=utf-8")
            if path == "/sitemap.xml":
                return self._send(200, self._sitemap(), "application/xml; charset=utf-8")
            if path == "/":
                return self._send(200, self._landing(), "text/html; charset=utf-8")
            if path.startswith("/n/"):
                return self._niche_page(path, q)
            go = re.match(r"^/go/([A-Z0-9]{10})$", path)
            if go:
                return self._go(go.group(1))
            if path == "/tool":
                return self._send(200, open(os.path.join(STATIC, "tool.html"), "rb").read(), "text/html; charset=utf-8")
            # static + API
            if path == "/" or path == "/index.html":
                return self._send(200, open(os.path.join(STATIC, "index.html"), "rb").read(), "text/html; charset=utf-8")
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
            if parsed.path == "/api/niches":
                return self._save_niche()
            if parsed.path == "/api/settings":
                return self._save_settings()
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
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
        top = int((q.get("top") or ["8"])[0])
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
        for n in self._all_niches():
            try:
                if slug == seo._slugify(n["keyword"]):
                    return self._send(200, seo.render_niche(n["keyword"], n), "text/html; charset=utf-8")
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

    # ------------------------------------------------------------------ marketing tools
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
        })


def main():
    _init()
    amazon.set_market(os.environ.get("MAZON_MARKET", amazon.DEFAULT_MARKET))
    amazon.set_tag(os.environ.get("MAZON_TAG", ""))
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("Mazon running on http://localhost:%d" % PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

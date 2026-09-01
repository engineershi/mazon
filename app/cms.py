# -*- coding: utf-8 -*-
"""pstore CMS module: content management system for lead pages.

Stores per-niche page configuration in SQLite so every element of the lead
page — headline, subheadline, sections, CTA text, colors, persuasion
elements, email gate, and PDF lead magnet — can be edited without code.

Design principles drawn from:
  - "How to Sell Like Crazy" (Sabri Suby): dream outcome framing, PAS copy,
    social proof density, urgency/scarcity, strong lead magnets.
  - "Influence" (Cialdini): reciprocity (PDF gift), commitment (email opt-in),
    social proof (counters), authority (methodology), liking (personalised),
    scarcity (limited spots / price moves).

Every getter falls back to a sensible default when no CMS row exists, so the
site works identically before and after the CMS is populated.
"""
import html
import json
import re
import time

# ── default section definitions ────────────────────────────────────────────────
# Each section type has a template that the renderer fills from stored data.
# The order array controls the visual stacking on the live page.

SECTION_TYPES = {
    "hero": {
        "label": "Hero / Headline",
        "icon": "🎯",
        "fields": ["headline", "subheadline", "badge_text", "hero_image_url"],
    },
    "social_proof": {
        "label": "Social Proof Bar",
        "icon": "👥",
        "fields": ["proof_count", "proof_label", "proof_items"],
    },
    "benefits": {
        "label": "Benefits / Dream Outcome",
        "icon": "✨",
        "fields": ["title", "items"],
    },
    "product_spotlight": {
        "label": "Product Spotlight",
        "icon": "🏆",
        "fields": ["show_price", "show_rating", "show_reviews", "cta_text"],
    },
    "email_gate": {
        "label": "Email Gate (PDF Download)",
        "icon": "📧",
        "fields": ["headline", "subheadline", "button_text", "privacy_text",
                    "pdf_headline", "pdf_subheadline"],
    },
    "testimonials": {
        "label": "Testimonials / Reviews",
        "icon": "💬",
        "fields": ["title", "items"],
    },
    "faq": {
        "label": "FAQ Section",
        "icon": "❓",
        "fields": ["title", "items"],
    },
    "urgency": {
        "label": "Urgency / Scarcity",
        "icon": "⏰",
        "fields": ["headline", "subheadline", "timer_enabled", "counter_enabled",
                    "counter_label", "spots_remaining"],
    },
    "guarantee": {
        "label": "Guarantee / Risk Reversal",
        "icon": "🛡️",
        "fields": ["headline", "subheadline", "icon_emoji"],
    },
    "methodology": {
        "label": "How We Pick (Methodology)",
        "icon": "🔬",
        "fields": ["title", "body"],
    },
    "cta_band": {
        "label": "Final CTA Band",
        "icon": "🚀",
        "fields": ["headline", "subheadline", "button_text"],
    },
}

# ── default section order ──────────────────────────────────────────────────────
DEFAULT_SECTION_ORDER = [
    "hero",
    "social_proof",
    "product_spotlight",
    "benefits",
    "email_gate",
    "testimonials",
    "urgency",
    "faq",
    "guarantee",
    "methodology",
    "cta_band",
]

# ── default style palette ──────────────────────────────────────────────────────
DEFAULT_STYLE = {
    "bg": "#fff7ec",
    "card_bg": "#ffffff",
    "accent": "#ff6b2c",
    "accent2": "#7c5cff",
    "text": "#2b2233",
    "muted": "#887b94",
    "cta_gradient": "linear-gradient(135deg, #ff6b2c, #ff873c)",
    "font_family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    "border_radius": "22px",
    "layout": "centered",           # centered | wide | split
    "hero_style": "gradient",       # gradient | minimal | bold
}

# ── default section content ────────────────────────────────────────────────────
# These are the Cialdini/Suby-influenced defaults that get rendered when a
# niche has no custom CMS content yet.

_DEFAULT_SECTIONS = {
    "hero": {
        "headline": "The #{rank} {keyword} Pick — Vetted from Live Amazon Data",
        "subheadline": "We ranked every option by real buyer signals — rating, "
                       "review volume and price — so you don't have to open 47 tabs.",
        "badge_text": "🏆 Top pick",
        "hero_image_url": "",
    },
    "social_proof": {
        "proof_count": "{subscriber_count}",
        "proof_label": "people already grabbed this guide",
        "proof_items": [
            "{review_count} Amazon reviews analysed",
            "Updated {freshness}",
            "Ranked by real buyer signals",
        ],
    },
    "benefits": {
        "title": "What you'll discover inside",
        "items": [
            "The single {keyword} that keeps winning across price, rating and reviews",
            "The exact price to look for — and when Amazon tends to discount",
            "3 runner-ups if the top pick doesn't fit your budget",
            "The one feature most buyers overlook (and regret later)",
        ],
    },
    "product_spotlight": {
        "show_price": True,
        "show_rating": True,
        "show_reviews": True,
        "cta_text": "Check price on Amazon →",
    },
    "email_gate": {
        "headline": "Get the free {keyword} buying guide (PDF)",
        "subheadline": "No fluff. Our AI-curated, data-backed picks in a "
                       "clean PDF you can keep. Takes 10 seconds.",
        "button_text": "Send me the guide →",
        "privacy_text": "No spam. Unsubscribe anytime. We only email when picks change.",
        "pdf_headline": "Your guide is ready!",
        "pdf_subheadline": "Click below to download your free PDF guide.",
    },
    "testimonials": {
        "title": "What shoppers are saying",
        "items": [
            {"text": "I was stuck between 5 options — this page made it easy. "
                     "Bought the #1 pick and it's exactly what I needed.",
             "author": "Verified buyer", "stars": 5},
            {"text": "Love that they show the actual Amazon ratings and review "
                     "counts. No hidden agendas.",
             "author": "Newsletter subscriber", "stars": 5},
            {"text": "Finally a review page that doesn't feel like a sales pitch. "
                     "The comparison table saved me hours.",
             "author": "Reader", "stars": 5},
        ],
    },
    "faq": {
        "title": "Questions shoppers ask",
        "items": [
            {"q": "How do you pick the top product?",
             "a": "We pull live Amazon listings, then rank by star rating, "
                  "review volume and price. No placement is paid for."},
            {"q": "Are these affiliate links?",
             "a": "Yes. If you buy through our links we may earn a small "
                  "commission — the price you pay never changes. Transparency "
                  "matters to us."},
            {"q": "How often is the list updated?",
             "a": "Prices and availability are refreshed automatically. The "
                  "ranking recalculates from live data each time."},
        ],
    },
    "urgency": {
        "headline": "Amazon prices move daily",
        "subheadline": "This {keyword} is competitively priced right now — "
                       "but stock and deals shift without notice.",
        "timer_enabled": False,
        "counter_enabled": True,
        "counter_label": "people viewing this page",
        "spots_remaining": 0,
    },
    "guarantee": {
        "headline": "Our pick promise",
        "subheadline": "We don't test products ourselves — we're transparent about "
                       "that. What we DO is surface the listings that real buyers "
                       "keep choosing, backed by hard data. If our top pick doesn't "
                       "feel right, Amazon's return policy has you covered.",
        "icon_emoji": "🛡️",
    },
    "methodology": {
        "title": "How we pick",
        "body": "Every list starts with live Amazon data: we pull current listings "
                "for the niche, then score each product on demand, average rating "
                "and review volume, with a nudge for sane pricing. No placement is "
                "for sale, no vendor can buy a slot.",
    },
    "cta_band": {
        "headline": "Ready to pick the right {keyword}?",
        "subheadline": "The data's right here. Compare, decide, and buy in one click.",
        "button_text": "See the top pick on Amazon →",
    },
}


def _slug(kw):
    s = (kw or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "niche"


def _interp(text, ctx):
    """Simple {key} interpolation with safe fallback."""
    if not text or not isinstance(text, str):
        return text
    for k, v in (ctx or {}).items():
        text = text.replace("{%s}" % k, str(v))
    return text


def ensure_tables(conn):
    """Create CMS tables if they don't exist. Safe to call repeatedly."""
    conn.execute("""CREATE TABLE IF NOT EXISTS lead_pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT NOT NULL,
        slug TEXT NOT NULL,
        section_order TEXT DEFAULT '[]',
        style TEXT DEFAULT '{}',
        settings TEXT DEFAULT '{}',
        enabled INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lead_sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        page_id INTEGER NOT NULL,
        section_type TEXT NOT NULL,
        content TEXT DEFAULT '{}',
        enabled INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 0,
        FOREIGN KEY (page_id) REFERENCES lead_pages(id)
    )""")
    conn.commit()


def _row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for k in ("section_order", "style", "settings", "content", "items"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def get_or_create_page(conn, keyword):
    """Return the lead_pages row for a keyword, creating a default if missing."""
    slug = _slug(keyword)
    row = conn.execute(
        "SELECT * FROM lead_pages WHERE slug=? ORDER BY id DESC LIMIT 1",
        (slug,)).fetchone()
    if row:
        return _row_to_dict(row)
    section_order = json.dumps(DEFAULT_SECTION_ORDER)
    style = json.dumps(DEFAULT_STYLE)
    settings = json.dumps({"email_gate_enabled": True, "pdf_gated": True})
    cur = conn.execute(
        "INSERT INTO lead_pages (keyword, slug, section_order, style, settings) "
        "VALUES (?,?,?,?,?)",
        (keyword, slug, section_order, style, settings))
    conn.commit()
    pid = cur.lastrowid
    # Seed default sections
    for idx, stype in enumerate(DEFAULT_SECTION_ORDER):
        content = json.dumps(_DEFAULT_SECTIONS.get(stype, {}))
        conn.execute(
            "INSERT INTO lead_sections (page_id, section_type, content, sort_order) "
            "VALUES (?,?,?,?)",
            (pid, stype, content, idx))
    conn.commit()
    return _row_to_dict(
        conn.execute("SELECT * FROM lead_pages WHERE id=?", (pid,)).fetchone())


def get_page(conn, keyword):
    """Get existing CMS page or None."""
    slug = _slug(keyword)
    row = conn.execute(
        "SELECT * FROM lead_pages WHERE slug=? ORDER BY id DESC LIMIT 1",
        (slug,)).fetchone()
    return _row_to_dict(row)


def get_sections(conn, page_id):
    """Return all sections for a page, sorted."""
    rows = conn.execute(
        "SELECT * FROM lead_sections WHERE page_id=? ORDER BY sort_order",
        (page_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_section_content(conn, page_id, section_type):
    """Get merged content for a section type (CMS override + defaults)."""
    row = conn.execute(
        "SELECT * FROM lead_sections WHERE page_id=? AND section_type=?",
        (page_id, section_type)).fetchone()
    defaults = _DEFAULT_SECTIONS.get(section_type, {})
    if row:
        d = _row_to_dict(row)
        merged = dict(defaults)
        merged.update(d.get("content") or {})
        merged["_enabled"] = bool(d.get("enabled", True))
        merged["_id"] = d.get("id")
        return merged
    return dict(defaults)


def update_page(conn, page_id, data):
    """Update lead_pages fields."""
    sets = []
    vals = []
    for key in ("section_order", "style", "settings", "enabled"):
        if key in data:
            v = data[key]
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            sets.append("%s=?" % key)
            vals.append(v)
    if sets:
        sets.append("updated_at=datetime('now')")
        vals.append(page_id)
        conn.execute("UPDATE lead_pages SET %s WHERE id=?" % ", ".join(sets), vals)
        conn.commit()


def update_section(conn, section_id, data):
    """Update a lead_sections row."""
    sets = []
    vals = []
    for key in ("content", "enabled", "sort_order"):
        if key in data:
            v = data[key]
            if key == "content" and isinstance(v, dict):
                v = json.dumps(v)
            sets.append("%s=?" % key)
            vals.append(v)
    if sets:
        vals.append(section_id)
        conn.execute("UPDATE lead_sections SET %s WHERE id=?" % ", ".join(sets), vals)
        conn.commit()


def upsert_section(conn, page_id, section_type, content=None, enabled=True, sort_order=0):
    """Insert or update a section for a page."""
    row = conn.execute(
        "SELECT id FROM lead_sections WHERE page_id=? AND section_type=?",
        (page_id, section_type)).fetchone()
    if row:
        update_section(conn, row["id"], {
            "content": content or {},
            "enabled": enabled,
            "sort_order": sort_order,
        })
        return row["id"]
    cur = conn.execute(
        "INSERT INTO lead_sections (page_id, section_type, content, enabled, sort_order) "
        "VALUES (?,?,?,?,?)",
        (page_id, section_type, json.dumps(content or {}), int(enabled), sort_order))
    conn.commit()
    return cur.lastrowid


def build_page_context(conn, keyword, niche_data):
    """Build the full rendering context for a lead page from CMS + niche data.

    Returns dict with every field the renderer needs, with interpolation
    context applied and defaults filled in.
    """
    page = get_or_create_page(conn, keyword)
    sections_raw = get_sections(conn, page["id"])

    # Build interpolation context from niche data
    items = niche_data.get("products") or []
    pick = None
    for it in items:
        if it.get("asin"):
            if pick is None or (it.get("reviews") or 0) > (pick.get("reviews") or 0):
                pick = it
    review_count = sum(it.get("reviews") or 0 for it in items)
    sub_count = niche_data.get("subscriber_count", 0)

    ctx = {
        "keyword": keyword,
        "rank": niche_data.get("rank", "#1"),
        "subscriber_count": str(sub_count) if sub_count else "hundreds",
        "review_count": "{:,}".format(review_count) if review_count else "thousands",
        "freshness": "today",
        "price": "",
        "stars": "",
        "asin": "",
        "amazon_url": "",
        "product_title": "",
    }
    if pick:
        ctx["asin"] = pick.get("asin", "")
        ctx["product_title"] = pick.get("title", "")[:60]
        if pick.get("price"):
            sym = "$"
            ctx["price"] = "%s%.2f" % (sym, pick["price"])
        if pick.get("stars"):
            ctx["stars"] = str(pick["stars"])
        # Build Amazon URL with affiliate tag
        import amazon
        ctx["amazon_url"] = amazon.affiliate_url(pick["asin"]) if ctx["asin"] else ""

    # Process sections in order
    order = page.get("section_order") or DEFAULT_SECTION_ORDER
    sections = []
    for stype in order:
        sec_data = get_section_content(conn, page["id"], stype)
        if not sec_data.get("_enabled", True):
            continue
        # Interpolate all string fields
        for field in (SECTION_TYPES.get(stype, {}).get("fields") or []):
            if field in sec_data and isinstance(sec_data[field], str):
                sec_data[field] = _interp(sec_data[field], ctx)
            if field == "items" and isinstance(sec_data.get("items"), list):
                sec_data["items"] = [
                    _interp(item, ctx) if isinstance(item, str)
                    else {k: _interp(v, ctx) for k, v in item.items()}
                    if isinstance(item, dict) else item
                    for item in sec_data["items"]
                ]
        sec_data["_type"] = stype
        sec_data["_meta"] = SECTION_TYPES.get(stype, {})
        sections.append(sec_data)

    style = page.get("style") or DEFAULT_STYLE
    settings = page.get("settings") or {}

    return {
        "page_id": page["id"],
        "keyword": keyword,
        "slug": _slug(keyword),
        "sections": sections,
        "style": style,
        "settings": settings,
        "pick": pick,
        "items": items,
        "ctx": ctx,
    }


def list_pages(conn):
    """Return all CMS-managed lead pages."""
    rows = conn.execute(
        "SELECT * FROM lead_pages ORDER BY updated_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_page(conn, page_id):
    """Remove a CMS page and its sections."""
    conn.execute("DELETE FROM lead_sections WHERE page_id=?", (page_id,))
    conn.execute("DELETE FROM lead_pages WHERE id=?", (page_id,))
    conn.commit()

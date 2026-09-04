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
# Style keys: mode(light|dark), bg, card_bg, accent, accent2, text, muted,
# cta_gradient, font_family, border_radius, layout, hero_style.
DEFAULT_STYLE = {
    "preset": "sunset",
    "mode": "light",
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


# ── one-click style templates ──────────────────────────────────────────────────
# Each preset is a complete style dict — picking one restyles the whole page
# (colors, dark/light mode, shape, layout) with a single click.
STYLE_PRESETS = {
    "sunset": {
        "label": "Sunset Pop", "desc": "Warm, energetic, persuasive",
        "swatches": ["#fff7ec", "#ff6b2c", "#7c5cff"],
        "style": {
            "preset": "sunset", "mode": "light",
            "bg": "#fff7ec", "card_bg": "#ffffff",
            "accent": "#ff6b2c", "accent2": "#7c5cff",
            "text": "#2b2233", "muted": "#887b94",
            "cta_gradient": "linear-gradient(135deg, #ff6b2c, #ff873c)",
            "border_radius": "22px", "layout": "centered",
            "hero_style": "gradient",
        },
    },
    "clean": {
        "label": "Clean & Modern", "desc": "Minimal SaaS-style, high trust",
        "swatches": ["#f6f9fc", "#2563eb", "#0ea5e9"],
        "style": {
            "preset": "clean", "mode": "light",
            "bg": "#f6f9fc", "card_bg": "#ffffff",
            "accent": "#2563eb", "accent2": "#0ea5e9",
            "text": "#0f172a", "muted": "#64748b",
            "cta_gradient": "linear-gradient(135deg, #2563eb, #0ea5e9)",
            "border_radius": "14px", "layout": "centered",
            "hero_style": "minimal",
        },
    },
    "forest": {
        "label": "Forest Fresh", "desc": "Calm greens, money-word friendly",
        "swatches": ["#f6fbf7", "#16a34a", "#0d9488"],
        "style": {
            "preset": "forest", "mode": "light",
            "bg": "#f1f8f4", "card_bg": "#ffffff",
            "accent": "#16a34a", "accent2": "#0d9488",
            "text": "#0c2b1d", "muted": "#5b7a66",
            "cta_gradient": "linear-gradient(135deg, #16a34a, #0d9488)",
            "border_radius": "18px", "layout": "wide",
            "hero_style": "gradient",
        },
    },
    "ocean": {
        "label": "Ocean Calm", "desc": "Airy blues, split hero layout",
        "swatches": ["#eff8ff", "#0284c7", "#6366f1"],
        "style": {
            "preset": "ocean", "mode": "light",
            "bg": "#eef6ff", "card_bg": "#ffffff",
            "accent": "#0284c7", "accent2": "#6366f1",
            "text": "#0b2b45", "muted": "#5f7a94",
            "cta_gradient": "linear-gradient(135deg, #0284c7, #6366f1)",
            "border_radius": "24px", "layout": "split",
            "hero_style": "bold",
        },
    },
    "midnight": {
        "label": "Midnight Luxe", "desc": "Dark premium feel, bold gold CTA",
        "swatches": ["#0d1321", "#f5b942", "#7c5cff"],
        "style": {
            "preset": "midnight", "mode": "dark",
            "bg": "#0d1321", "card_bg": "#161f35",
            "accent": "#f5b942", "accent2": "#9d7bff",
            "text": "#f1f5fb", "muted": "#9aa7c0",
            "cta_gradient": "linear-gradient(135deg, #f5b942, #ff873c)",
            "border_radius": "20px", "layout": "centered",
            "hero_style": "gradient",
        },
    },
}


def preset_style(name):
    """Return a copy of a preset's style dict (or the default when unknown)."""
    p = STYLE_PRESETS.get(name or "")
    if not p:
        return dict(DEFAULT_STYLE)
    style = dict(p.get("style") or {})
    style["font_family"] = DEFAULT_STYLE["font_family"]
    return style

# ── default section content ────────────────────────────────────────────────────
# These are the Cialdini/Suby-influenced defaults that get rendered when a
# niche has no custom CMS content yet.

_DEFAULT_SECTIONS = {
    "hero": {
        "headline": "The {rank} {keyword} Pick — Vetted from Live Amazon Data",
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


# ── default page settings ──────────────────────────────────────────────────────
# Stored on lead_pages.settings; toggles are the "click and go" levers in the
# admin editor. Everything has a safe offline default here.
DEFAULT_SETTINGS = {
    "use_cms": True,
    "email_gate_enabled": True,     # email-gated PDF lead magnet
    "pdf_gated": True,              # PDF behind opt-in (False = direct download)
    "promo_enabled": False,         # announcement / promo banner on top
    "promo": {"text": "Free shipping on orders over $25", "code": "SAVE10"},
    "countdown_enabled": False,     # live countdown boxes under the header
    "countdown_minutes": 30,
    "countdown_headline": "⏳ Today's pricing refresh starts soon",
    "countdown_done": "Pricing just refreshed — see today's best rate below.",
    "sticky_cta": True,             # floating "see the top pick" bar on scroll
    "animation": True,              # gentle reveal-on-scroll
}


def merge_settings(settings):
    """Defaults first, then caller overrides — so old/partial rows never break."""
    merged = dict(DEFAULT_SETTINGS)
    merged.update(settings or {})
    if not isinstance(merged.get("promo"), dict):
        merged["promo"] = dict(DEFAULT_SETTINGS["promo"])
    return merged


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
    settings = json.dumps(DEFAULT_SETTINGS)
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
    """Get merged content for a section type (CMS override + defaults).

    If duplicate rows exist for the same (page, type), prefer the most recently
    inserted one (highest id) — this keeps a stale disabled duplicate from
    shadowing an enabled row and matches the row the editor last wrote."""
    row = conn.execute(
        "SELECT * FROM lead_sections WHERE page_id=? AND section_type=? "
        "ORDER BY id DESC LIMIT 1",
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
        "SELECT id FROM lead_sections WHERE page_id=? AND section_type=? "
        "ORDER BY id DESC LIMIT 1",
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


def apply_preset(conn, page_id, preset_name):
    """One-click restyle: overwrite the page style with a named preset."""
    style = preset_style(preset_name)
    update_page(conn, page_id, {"style": style})
    return style


def _ai_field(template, niche, hint, default):
    """AI-polished single-line copy for a field, falling back to the default
    whenever AI is unconfigured, unavailable or returns nothing."""
    try:
        import ai
        lines = ai.generate(template, niche, hint)
        if lines and lines[0].strip():
            return lines[0].strip()
    except Exception:
        pass
    return default


def generate_copy(conn, page_id, keyword):
    """One-click regeneration: reset every section's copy to the persuasion
    defaults (which interpolate the niche's live data). Page settings and the
    chosen style are kept, so toggles the operator turned on stay on. When an AI
    provider is configured, the text-heavy sections are polished with AI copy
    (falling back to the defaults on any failure); otherwise defaults are used."""
    try:
        import ai
        use_ai = ai.configured()
    except Exception:
        use_ai = False
    order = DEFAULT_SECTION_ORDER
    conn.execute("DELETE FROM lead_sections WHERE page_id=?", (page_id,))
    for idx, stype in enumerate(order):
        base = dict(_DEFAULT_SECTIONS.get(stype, {}))
        if use_ai:
            if stype == "hero":
                base["headline"] = _ai_field("headline", keyword, "",
                                             base.get("headline", ""))
                base["subheadline"] = _ai_field("subheadline", keyword, "",
                                                base.get("subheadline", ""))
            elif stype == "email_gate":
                base["headline"] = _ai_field("subheadline", keyword,
                                             "email lead magnet",
                                             base.get("headline", ""))
            elif stype == "cta_band":
                base["headline"] = _ai_field("headline", keyword, "call to action",
                                             base.get("headline", ""))
                base["subheadline"] = _ai_field("subheadline", keyword,
                                                "call to action",
                                                base.get("subheadline", ""))
            elif stype == "urgency":
                base["headline"] = _ai_field("headline", keyword, "urgency",
                                             base.get("headline", ""))
            elif stype == "guarantee":
                base["headline"] = _ai_field("headline", keyword, "guarantee",
                                             base.get("headline", ""))
            elif stype == "methodology":
                base["title"] = _ai_field("subheadline", keyword,
                                          "methodology title",
                                          base.get("title", ""))
        conn.execute(
            "INSERT INTO lead_sections (page_id, section_type, content, enabled, sort_order) "
            "VALUES (?,?,?,?,?)",
            (page_id, stype, json.dumps(base), 1, idx))
    conn.execute("UPDATE lead_pages SET section_order=?, updated_at=datetime('now') "
                 "WHERE id=?", (json.dumps(order), page_id))
    conn.commit()
    return len(order)


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
        "slug": _slug(keyword),
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
        # Interpolate all string + list-of-string fields
        for field in (SECTION_TYPES.get(stype, {}).get("fields") or []):
            if field not in sec_data:
                continue
            val = sec_data[field]
            if isinstance(val, str):
                sec_data[field] = _interp(val, ctx)
            elif isinstance(val, list) and val and isinstance(val[0], str):
                sec_data[field] = [_interp(x, ctx) for x in val]
        sec_data["_type"] = stype
        sec_data["_meta"] = SECTION_TYPES.get(stype, {})
        sections.append(sec_data)

    style = page.get("style") or DEFAULT_STYLE
    if not style.get("preset"):
        style = dict(style)
        style["preset"] = "sunset"
    settings = merge_settings(page.get("settings") or {})

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

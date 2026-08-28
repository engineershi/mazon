# -*- coding: utf-8 -*-
"""Mazon marketing engine: tools to push direct buyers to Amazon fast.

Everything is keyless and produces affiliate-tagged, copy-paste-ready output:

  * build_text_links  — clickable short text links for comments, DMs, bios
  * build_markdown     — Markdown link blocks for a niche's top products
  * build_email_draft  — a ready-to-send buyer email with all product links
  * build_qr_url       — a QR-friendly redirect URL a poster/scanner leads buyers to
  * build_redirect     — the in-app /go/<asin> endpoint that 302s to the tagged URL
  * pick_for_buyers    — heuristic to surface the single best "buy this" pick

Combine these with real traffic (social posts, forums, email) and every link
is an affiliate link ready to earn the moment a buyer clicks + converts.
"""
import re
import urllib.parse

import amazon


def clean_tag():
    return (amazon.AFFILIATE_TAG or "").strip() or "YOURTAG-20"


def _best_items(items, n=3):
    ranked = sorted(
        [it for it in (items or []) if it.get("asin")],
        key=lambda x: (x.get("reviews") or 0), reverse=True)
    return ranked[:n]


def pick_for_buyers(items):
    """Pick the single strongest product to push: most reviews, price tiebreak."""
    best = None
    for it in (items or []):
        if not it.get("asin"):
            continue
        if best is None:
            best = it
            continue
        if (it.get("reviews") or 0) > (best.get("reviews") or 0):
            best = it
        elif (it.get("reviews") or 0) == (best.get("reviews") or 0) and \
                (it.get("price") or 0) < (best.get("price") or 0):
            best = it
    return best


def build_text_links(items, label="View on Amazon"):
    """Plain text links: `best keto snacks - https://amzn.to/...`. Paste straight
    into a comment/DM/bio. Uses the /go redirect so it's short & clickable."""
    out = []
    for it in _best_items(items, 5):
        asin = it.get("asin")
        if not asin:
            continue
        out.append(f"- {label} ({it.get('title','')[:40]}): {redirect_url(asin)}")
    return "\n".join(out) if out else "(no products yet)"


def build_markdown(items, heading=None):
    """Markdown block with inline affiliate links — paste into blog/notion/gh."""
    lines = []
    if heading:
        lines.append(f"## {heading}")
    for it in _best_items(items, 5):
        title = (it.get("title") or "").strip()
        if not it.get("asin"):
            continue
        lines.append(f"- [{title}]({redirect_url(it.get('asin'))})"
                     f"{((' - $%0.2f' % it.get('price')) if it.get('price') else '')}")
    return "\n".join(lines)


def build_email_draft(items, subject="My top picks for you", opener="Hey! Here are my top picks I think you'll love:"):
    pick = pick_for_buyers(items)
    lines = [f"Subject: {subject}", "", opener, ""]
    if pick and pick.get("asin"):
        lines.append(f"🛒 Top pick: {pick.get('title')} — {redirect_url(pick.get('asin'))}")
        lines.append("")
    for it in _best_items(items, 6):
        if not it.get("asin"):
            continue
        lines.append(f"- {it.get('title')}: {redirect_url(it.get('asin'))}")
    lines += ["", "Happy shopping!", ""]
    return "\n".join(lines)


def build_post_template(items, caption="My top picks for this week 👇"):
    """Social post caption with each product on its own line."""
    lines = [caption, ""]
    for it in _best_items(items, 5):
        if not it.get("asin"):
            continue
        lines.append(f"• {it.get('title')} → {redirect_url(it.get('asin'))}")
    return "\n".join(lines)


def qr_url(asin):
    """URL to hand to a QR-code tool (or poster) that leads straight to a tagged
    product page — drives in-person/offline buyers."""
    u = redirect_url(asin)
    return f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(u)}"


# ------------------------------------------------------------------ redirect
# Short, memorable, trackable in-app links. /go/<ASIN> 302s to the affiliate
# URL. (A real t.co/bit.ly shortener can be dropped in later.)
def redirect_url(asin):
    return f"/go/{asin}"


def expand_go(asin):
    """The server handler for /go/<ASIN>: returns (target_url, marketplace)."""
    return amazon.affiliate_url(asin), amazon.MARKET


def status_blurb(scraper_cfg=None):
    return {
        "affiliate_tag": clean_tag(),
        "marketplace": amazon.MARKET,
        "tools": ["text-links", "markdown", "email-draft", "social-post",
                  "redirect /go/<asin>", "qr"],
    }

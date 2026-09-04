# -*- coding: utf-8 -*-
"""pstore lead lifecycle scoring: turn click + open data into hot/warm/cold
segments so the email sequence can act on engagement instead of treating every
subscriber the same.

This is the "nurture intelligently" layer (Sell Like Crazy Phase 6):
  * HOT      - opened an email AND clicked a tracked outbound link (strong intent)
  * WARM     - opened an email but never clicked (interested, not decisive)
  * COLD     - confirmed subscriber but never opened an email (needs re-engage)
  * CONVERTED- clicked a product ASIN (bought or at least veered to Amazon)

Segments are derived from the subscriptions/click/email-event records with a
pure scoring function (no network), so it is unit-testable offline. Everything
is privacy-safe: we only use hashed-IP click rows + email events already stored.

The `rows` accepted by build_report() are fetched by the caller (server.py) from
the sqlite DB; we keep this module free of sqlite/db imports so tests can feed
plain dicts.
"""

# Thresholds (days) for "fresh" engagement so old signals decay.
FRESH_OPEN_DAYS = 30
FRESH_CLICK_DAYS = 45
# A click on a real ASIN (outbound to Amazon) counts as conversion intent.
CONVERTED_SOURCES = ("email", "page", "landing-cta", "social", "niche", "home")


def score_one(email, **state):
    """Pure scoring for a single subscriber given pre-aggregated counts.

    state keys (all optional, ints/booleans):
      opens         - distinct opens recorded
      clicks        - distinct outbound clicks recorded
      clicked_asin  - bool: clicked a product ASIN (not just a page)
      unsubscribed  - bool
      confirmed     - bool
    Returns dict {segment, reason, hot, warm, cold, converted}.
    """
    unsub = bool(state.get("unsubscribed"))
    confirmed = bool(state.get("confirmed", True))
    opens = int(state.get("opens") or 0)
    clicks = int(state.get("clicks") or 0)
    clicked_asin = bool(state.get("clicked_asin"))

    if unsub or not confirmed:
        return {"segment": "inactive", "reason": "unsubscribed or unconfirmed",
                "hot": False, "warm": False, "cold": False, "converted": False}

    if clicked_asin:
        return {"segment": "converted", "reason": "clicked a product link",
                "hot": False, "warm": False, "cold": False, "converted": True}
    if clicks > 0 and opens > 0:
        return {"segment": "hot", "reason": "opened + clicked",
                "hot": True, "warm": False, "cold": False, "converted": False}
    if opens > 0:
        return {"segment": "warm", "reason": "opened, not clicked",
                "hot": False, "warm": True, "cold": False, "converted": False}
    return {"segment": "cold", "reason": "no engagement",
            "hot": False, "warm": False, "cold": True, "converted": False}


def build_report(rows):
    """rows: list of dicts, each with at least:
        email, opens (int), clicks (int), clicked_asin (int/bool),
        confirmed, unsubscribed
    Returns {segments: {hot:[...], warm:[...], cold:[...], converted:[...],
            inactive:[...]}, counts, total} preserving any extra fields.
    """
    out = {"hot": [], "warm": [], "cold": [], "converted": [], "inactive": []}
    for row in rows:
        base = dict(row)
        score = score_one(
            base.get("email"),
            opens=base.get("opens"), clicks=base.get("clicks"),
            clicked_asin=base.get("clicked_asin"),
            unsubscribed=base.get("unsubscribed"),
            confirmed=base.get("confirmed", 1))
        base["segment"] = score["segment"]
        base["segment_reason"] = score["reason"]
        out[score["segment"]].append(base)
    counts = {k: len(v) for k, v in out.items()}

    # Per-segment engagement aggregates so the admin can see where the
    # recipients actually are (open/click throughput, per-lead value).
    stats = {}
    for name, members in out.items():
        n = len(members)
        opens = sum(m.get("opens") or 0 for m in members)
        clicks = sum(m.get("clicks") or 0 for m in members)
        sent = sum(m.get("sent") or 0 for m in members)
        stats[name] = {
            "members": n,
            "opens": opens,
            "clicks": clicks,
            "sent": sent,
            "open_per_lead": round(opens / max(n, 1), 2),
            "click_per_lead": round(clicks / max(n, 1), 2),
            "open_rate": round(opens / max(sent, 1) * 100, 1),
            "click_rate": round(clicks / max(opens, 1) * 100, 1),
        }

    return {
        "segments": out,
        "counts": counts,
        "stats": stats,
        "total": len(rows),
        "hot_share": round(counts["hot"] / max(len(rows), 1) * 100, 1),
        "converted_share": round(counts["converted"] / max(len(rows), 1) * 100, 1),
    }


def next_action(segment):
    """The recommended follow-up email angle per segment (Sell Like Crazy Ph6 /
    Brunson Soap Opera). Returns a small dict a caller can use to tailor a send."""
    return {
        "hot": {
            "subject_hint": "Close the loop: your pick is waiting",
            "angle": "Godfather offer + urgency + guarantee (remove last doubt).",
            "sequence_hint": "Send the urge-to-act / OTO email now."},
        "warm": {
            "subject_hint": "One more look at what you opened",
            "angle": "Second social-proof + value email; restate proof (reviews).",
            "sequence_hint": "Send value/proof email #2."},
        "cold": {
            "subject_hint": "Did we lose you? (re-engage)",
            "angle": "Re-engagement / 'come back' — plain value, no hard sell.",
            "sequence_hint": "Send a re-engagement email; re-mark to 'smb.' if stale."},
        "converted": {
            "subject_hint": "Enjoy it? The next rung up the ladder",
            "angle": "Review ask + upsell to the next Value-Ladder tier.",
            "sequence_hint": "Review pipeline + backend/upsell offer."},
        "inactive": {
            "subject_hint": "",
            "angle": "None — do not email (unsubscribed/unconfirmed).",
            "sequence_hint": "Exclude from sends."},
    }.get(segment, {"subject_hint": "", "angle": "", "sequence_hint": ""})

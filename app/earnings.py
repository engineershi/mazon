# -*- coding: utf-8 -*-
"""Earnings & conversion layer: turn click data into a decision surface.

Amazon does not expose per-click order/earnings via the free scraping route, so
this module is an honest estimator, not a fake ledger:

  * It uses your *actual* recorded clicks (from /api/track).
  * You configure your Associates commission ``%`` (by category) *average order
    value* and an *expected order rate* (orders per click) — industry defaults
    are used but `earnings.configure()` / env vars let you set real numbers.
  * It also lets you log REAL orders/lifetime from the Amazon dashboard so the
    report shows "estimated potential" vs "recorded real" side by side.

Nothing here hits the network. Everything bottoms out in pure functions so tests
stub it trivially.
"""
import os

# ------------------------------------------------------------------ config
# Amazon Associates pays a % of eligible item price, varies by category, plus
# a fixed "bounty" for some program types. Defaults are deliberately
# conservative stand-ins for a typical US books/electronics mix; override them.
DEFAULT_COMMISSION_PCT = float(os.environ.get("EARN_COMMISSION_PCT", "4.0") or "4.0")
DEFAULT_AVG_ORDER = float(os.environ.get("EARN_AVG_ORDER", "40.0") or "40.0")
# orders per click is NOT knowable offline; give a sane default the user tunes.
DEFAULT_ORDER_RATE = float(os.environ.get("EARN_ORDER_RATE", "0.03") or "0.03")

# Runtime overrides set via the keys hub / admin, apply immediately.
_runtime = {}

SAMPLE_CATEGORIES = {  # Amazon Associates base-rate reference (US, FY23+)
    "electronics": 4.0, "home": 8.0, "furniture": 8.0, "appliances": 8.0,
    "tools": 8.0, "sports": 8.0, "books": 4.5, "video": 5.0, "toys": 8.0,
    "beauty": 10.0, "grocery": 5.0, "apparel": 4.0, "default": DEFAULT_COMMISSION_PCT,
}


def configure(commission_pct=None, avg_order=None, order_rate=None):
    """Set runtime overrides (None leaves the current value untouched)."""
    if commission_pct is not None:
        _runtime["commission_pct"] = float(commission_pct)
    if avg_order is not None:
        _runtime["avg_order"] = float(avg_order)
    if order_rate is not None:
        _runtime["order_rate"] = float(order_rate)


def commission_pct(category=""):
    base = SAMPLE_CATEGORIES.get((category or "").lower())
    if base is None:
        base = DEFAULT_COMMISSION_PCT
    return float(_runtime.get("commission_pct", base))


def avg_order(category=""):
    return float(_runtime.get("avg_order", DEFAULT_AVG_ORDER))


def order_rate(category=""):
    return float(_runtime.get("order_rate", DEFAULT_ORDER_RATE))


def per_click_value(category=""):
    """Estimated earnings for a single click (before any order is known)."""
    return avg_order(category) * (commission_pct(category) / 100.0) * order_rate(category)


def estimate(clicks, category=""):
    """Estimated earnings (and orders) for N clicks at current config."""
    clicks = max(int(clicks or 0), 0)
    aov = avg_order(category)
    pct = commission_pct(category)
    rate = order_rate(category)
    orders = clicks * rate
    groomed = clicks * rate * aov
    commission = groomed * (pct / 100.0)
    return {
        "clicks": clicks,
        "orders_est": orders,
        "gross_est": groomed,
        "commission_est": commission,
        "avg_order": aov,
        "commission_pct": pct,
        "order_rate": rate,
    }


def aggregate(rows, fn_category, fields=("clicks",)):
    """Aggregate click-like rows. `rows` must be dicts/Row with at least the
    fields named. `fn_category(row)` returns a category key (e.g. '' default).
    Returns per-category estimates plus a global total."""
    totals = {}
    for r in rows:
        cat = fn_category(r)
        cat = (cat or "") or "default"
        rec = totals.setdefault(cat, {
            "clicks": 0, "orders_est": 0.0, "gross_est": 0.0, "commission_est": 0.0})
        c = int(r["clicks"] or 0) if "clicks" in r else 1
        rec["clicks"] += c
    out = {}
    for cat, rec in totals.items():
        est = estimate(rec["clicks"], cat)
        out[cat] = est
    grand = {"clicks": sum(r["clicks"] for r in out.values()),
             "orders_est": sum(r["orders_est"] for r in out.values()),
             "gross_est": sum(r["gross_est"] for r in out.values()),
             "commission_est": sum(r["commission_est"] for r in out.values())}
    return {"by_category": out, "total": grand}


def monthly_summary(months_data):
    """Point-in-time summary of real orders/earnings the operator logged from
    the Associates dashboard, so the report contrasts real vs estimated."""
    total_orders = sum(d.get("orders", 0) for d in months_data)
    total_earn = sum(d.get("earnings", 0.0) for d in months_data)
    return {"months": months_data, "total_orders": total_orders,
            "total_earnings": total_earn}
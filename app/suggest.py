# -*- coding: utf-8 -*-
"""pstore demography-driven AUTO SUGGESTION engine.

Given the operator's market-demography profile (region, interest, behavior,
age, audience, income) this turns real buyer-language keywords from Amazon
autosuggest into ranked, build-ready niche suggestions. Every suggestion is
wired to the existing pipeline: the UI can one-click mine it -> save -> build
long-tail pages -> indexnow -> market.

Keyless by design (same posture as niche.py): Amazon autosuggest supplies the
seed demand signal, product search fills in real products, and a small additive
scoring model ranks candidates against the configured persona.
"""
import re

import amazon
import market_engine
import niche


# Small persona→keyword affordance maps so a sparse profile still produces
# sensible seeds. These never fabricate Amazon data; they only pick what to ask
# autosuggest (real buyer queries) and how to bespoke the copy angle.
_INTEREST_SEEDS = {
    "fashion": ["fashion", "outfit", "wardrobe", "accessories", "style"],
    "beauty": ["skincare", "makeup", "beauty", "cosmetics", "self care"],
    "fitness": ["fitness", "workout", "gym", "home exercise", "running"],
    "home": ["home", "kitchen", "diy", "organization", "decor"],
    "tech": ["tech", "gadgets", "desk setup", "bluetooth", "smart home"],
    "gaming": ["gaming", "pc setup", "controller", "streaming", "gamer"],
    "kids": ["kids", "baby", "toddler", "activity", "toys"],
    "pets": ["pets", "dogs", "cats", "pet care", "training"],
    "gardening": ["gardening", "plants", "outdoor", "garden tools", "growing"],
    "travel": ["travel", "bags", "backpack", "travel gear", "packing"],
}

_BEHAVIOR_SEEDS = {
    "budget": ["under 20", "budget", "cheap", "affordable", "save money"],
    "premium": ["premium", "luxury", "high end", "best quality", "professional"],
    "gift": ["gift", "for her", "for him", "gift set", "birthday"],
    "beginner": ["beginner", "starter kit", "for beginners", "easy", "simple"],
    "professional": ["professional", "pro", "for work", "office", "advanced"],
    "eco": ["eco friendly", "reusable", "sustainable", "natural", "organic"],
    "travel": ["portable", "travel", "on the go", "compact", "for vacation"],
}

_INTENT_WORDS = re.compile(
    r"\b(best|top|reviews|for|with|under|budget|gift|beginner|pro|starter|"
    r"premium|portable|wireless|set|how to)\b", re.I)


def seed_queries(demo):
    """Build the seed query list from a demography dict (interest, behavior,
    region, interests_extra). Returns a de-duplicated list of seed strings."""
    demo = demo or {}
    interest = str(demo.get("interest") or "").strip().lower()
    behavior = str(demo.get("behavior") or "").strip().lower()
    region = str(demo.get("region") or "").strip()
    extra = str(demo.get("interests_extra") or "").strip()

    seeds = []
    # primary interest -> known affordance map, else the raw interest itself
    known = _INTEREST_SEEDS.get(interest)
    if known:
        seeds += known
    elif interest:
        seeds.append(interest)
    # extra (comma-separated) interests
    for x in [s.strip() for s in re.split(r"[;,\n]+", extra) if s.strip()]:
        seeds.append(x)
    # behavior affordance
    bknown = _BEHAVIOR_SEEDS.get(behavior)
    if bknown:
        seeds += bknown
    elif behavior:
        seeds.append(behavior)
    # region as a modifier baked into the primary interest when both exist
    if interest and region and not known:
        seeds.append("%s %s" % (interest, region))

    out = []
    seen = set()
    for s in seeds:
        s = s.strip()
        low = s.lower()
        if not s or low in seen:
            continue
        seen.add(low)
        out.append(s)
    return out or [interest or behavior or "gift ideas"]


def _match_persona(kw, demo):
    """0..1 affinity of a keyword to the configured persona: rewards keywords
    that echo the interest, behavior or region of the profile."""
    low = kw.lower()
    hits = 0.0
    for key in ("interest", "behavior", "age", "audience"):
        val = str(demo.get(key) or "").lower()
        for w in re.split(r"[^a-z0-9]+", val):
            if len(w) >= 3 and w in low:
                hits += 0.22
    if demo.get("region"):
        reg = str(demo["region"]).lower()
        for w in re.split(r"[^a-z0-9]+", reg):
            if len(w) >= 4 and w in low:
                hits += 0.18
    return round(min(1.0, hits), 2)


def suggest_niches(demo, top=4, max_candidates=14, seed_items_missing_ok=True):
    """Rank build-ready niche suggestions for a demography profile.

    Returns {"profile": demo, "seeds": [...], "suggestions": [
        {"keyword", "score", "saturation", "magnet", "products", "count",
         "persona_match", "reason"}...
    ], "meta": {"seeds_used", "autosuggest_sources"}}.

    Suggestions are ranked by an additive score = demand (autosuggest richness)
    + persona match (profile affinity) - saturation (competition). No fabricated
    metrics: keyword/score/saturation all come from live Amazon signals."""
    seeds = seed_queries(demo)
    seen = set()
    candidates = []
    for seed in seeds:
        for idea in amazon.autosuggest(seed, limit=10):
            kw = idea.strip().lower()
            if not kw or len(kw) < 4 or kw in seen:
                continue
            seen.add(kw)
            candidates.append(kw)
            if len(candidates) >= max_candidates:
                break
        if len(candidates) >= max_candidates:
            break

    # If autosuggest gave nothing (network/keyless cold start), still offer the
    # seeds themselves as candidate keywords.
    if not candidates:
        candidates = [s.strip().lower() for s in seeds]

    suggestions = []
    for kw in candidates:
        items, source = amazon.search(kw, top=6)
        demand = niche._score_demand(amazon.autosuggest(kw, limit=8))
        saturation = niche._score_saturation(items)
        persona = _match_persona(kw, demo)
        # additive ranking: demand + persona + intent bonus - saturation penalty
        intent_bonus = 0.6 if _INTENT_WORDS.search(kw) else 0.0
        score = round(demand + persona * 2.0 + intent_bonus
                      - (saturation or 0) * 0.25, 1)
        reason = _reason(kw, demo, persona)
        suggestions.append({
            "keyword": kw,
            "score": score,
            "demand": demand,
            "saturation": saturation,
            "persona_match": persona,
            "magnet": niche._magnet(items) if items else None,
            "products": items,
            "count": len(items),
            "source": source,
            "reason": reason,
        })
    suggestions.sort(key=lambda s: -s["score"])
    return {
        "profile": demo,
        "seeds": seeds,
        "suggestions": suggestions[:top],
        "meta": {
            "seeds_used": len(seeds),
            "autosuggest_sources": len(seen),
            "candidates": len(candidates),
        },
    }


def _reason(kw, demo, persona):
    """One-line, honest rationale for a suggestion (why it matches the persona)."""
    parts = []
    if persona >= 0.5:
        parts.append("matches your profile")
    elif _INTENT_WORDS.search(kw):
        parts.append("typed like a serious shopper")
    else:
        parts.append("real Amazon search language")
    return "; ".join(parts) if parts else "candidate niche"


def build_route(kw):
    """Server-side convenience used by the UI to start a one-click build of a
    suggestion: re-mine exactly that keyword through the normal pipeline."""
    niches, meta = niche.mine_niche(kw, top=8, max_niches=1)
    return {"niche": niches[0], "meta": meta}
# -*- coding: utf-8 -*-
"""pstore niche mining: turn a broad seed into a scored product niche.

Uses only KEYLESS signals so a beginner can mine niches with no Amazon API:

  * Amazon autosuggestions (real buyer-language keywords) -> expand the seed
  * Product search (scraper/Amazon parse) -> actual products + review/rating
  * A simple saturation heuristic: avg reviews + top-niche competitor count

Output: a list of candidate niches, each with a demand score, a saturation
reading, and a shortlist of affiliate products ready to display.
"""
import re

import amazon


def _score_demand(ideas):
    """Cheap demand proxy: the richer the autosuggest keyword tree, the more
    people search the space. 0..10."""
    if not ideas:
        return 0
    # weight by how many variants exist and how long they are
    has_modifier = sum(1 for i in ideas if re.search(r'\b(est|best|for|with|under)\b', i))
    variety = min(len(ideas), 10)
    return round(min(10.0, 2.0 + variety * 0.6 + has_modifier * 0.4), 1)


def _score_saturation(items):
    """Higher average reviews = more competitive (saturated) niche. 0..10,
    where 10 means very crowded."""
    if not items:
        return None  # unknown
    revs = [i.get("reviews") or 0 for i in items if i.get("reviews")]
    if not revs:
        return None
    avg = sum(revs) / len(revs)
    # rough log curve: ~100 reviews -> 2, ~1000 -> 5, ~10000 -> 8
    score = 2.0 * (avg ** 0.25) / (10 ** 0.25)
    return round(min(10.0, score), 1)


def _magnet(items):
    """Pick the single best entry product (most reviews, lowest price tiebreak)."""
    best = None
    for it in items:
        if best is None:
            best = it
            continue
        br = best.get("reviews") or 0
        ir = it.get("reviews") or 0
        if ir > br or (ir == br and (it.get("price") or 0) < (best.get("price") or 0)):
            best = it
    return best


def mine_niche(seed, top=8, max_niches=5):
    """Expand a seed into (niches, meta). meta has the signals used so the UI
    can be transparent about where the numbers came from."""
    ideas = amazon.autosuggest(seed, limit=12)
    niches = []
    candidates = []

    # Primary niche = the seed itself, searched for real products.
    items, source = amazon.search(seed, top=top)
    primary = {"keyword": seed, "products": items, "source": source,
               "score": _score_demand(ideas),
               "saturation": _score_saturation(items),
               "magnet": _magnet(items) if items else None}
    niches.append(primary)
    candidates.append(seed)

    # Expand into narrower keyword variants already seen in autosuggest.
    seen = {seed.lower()}
    for idea in ideas:
        kw = idea.strip().lower()
        if not kw or kw == seed.lower() or len(kw) < 4:
            continue
        if kw in seen:
            continue
        seen.add(kw)
        candidates.append(kw)
        if len(candidates) >= max_niches:
            break

    for kw in candidates[1:]:
        items, source = amazon.search(kw, top=top)
        niches.append({"keyword": kw, "products": items, "source": source,
                       "score": _score_demand(amazon.autosuggest(kw, limit=8)) if not items else _score_demand(ideas),
                       "saturation": _score_saturation(items),
                       "magnet": _magnet(items) if items else None})

    meta = {"seed": seed, "market": amazon.MARKET, "autosuggest_count": len(ideas),
            "signals": ["amazon-autosuggest", "product-search"]}
    return niches, meta

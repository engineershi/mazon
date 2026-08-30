# -*- coding: utf-8 -*-
"""pstore AI module: OpenAI-compatible chat completions over urllib (stdlib).

Primes an optional AI (`AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL`) to write
attention-grabbing headlines, subheadlines and full ebook chapters about a
trending niche. When the key/model is unset or the request fails, we fall
back to deterministic templates so the site (and every test) still works
offline. Everything routes through ai._urlopen so tests can stub it.
"""
import json
import os
import urllib.request

API_KEY = os.environ.get("AI_API_KEY", "")
BASE_URL = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")

SUPPORTED_TEMPLATES = {
    "headline": "curious, concrete and click-worthy",
    "subheadline": "supportive, specific, no hype",
    "chapter": "instructive and skimmable, friendly tone",
}


def configured():
    return bool(API_KEY and MODEL)


# Test hook (same pattern as amazon._urlopen / mailer._send).
_urlopen = None


def _request(payload, timeout=25):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + "/chat/completions", data=data, method="POST", headers={
            "Authorization": "Bearer " + API_KEY,
            "Content-Type": "application/json",
        })
    if _urlopen is not None:
        resp = _urlopen(req)
        return json.loads(resp) if isinstance(resp, (str, bytes)) else resp
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _complete(system, user, timeout=25):
    payload = {"model": MODEL, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], "temperature": 0.8, "max_tokens": 700}
    out = _request(payload, timeout)
    return (out.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


def _clean(text):
    lines = [ln.strip().strip('"') for ln in (text or "").splitlines()]
    return [ln for ln in lines if ln]


def generate(template, niche, hint=""):
    """One AI call returning multi-line prose; empty list on unconfigured/fail."""
    style = SUPPORTED_TEMPLATES.get(template, "useful")
    if not configured():
        return []
    system = ("You write marketing copy for an Amazon affiliate site called pstore. "
              f"For a '{template}', write only the requested content ({style}). "
              "Plain text, no markdown, no intro lines, no bullets.")
    user = "Niche: %s\n%s" % (niche, ("Extra: " + hint) if hint else "")
    try:
        return _clean(_complete(system, user))
    except Exception:
        return []


def headline_and_subheadline(niche, hint=""):
    """Mind-blowing headline + subheadline for the ebook about `niche`.

    Falls back to a crafted template pair when AI is not configured, so the
    feature degrades gracefully. Returns {"headline": str, "subheadline": str}.
    """
    ai = generate("headline", niche, hint)
    sub = generate("subheadline", niche, hint)
    if ai:
        return {"headline": ai[0],
                "subheadline": (sub + ["Here's what matters, exactly what to look for."])[0]}
    nice = niche.strip() or "your pick"
    return {
        "headline": "The %s Playbook: What to Buy in 2026 Without the Guesswork" % nice.title(),
        "subheadline": "The exact criteria, the proof to trust, and the picks worth your money — boiled down to a quick read.",
    }


def ebook(niche, hint=""):
    """Ebook for a trending niche. Returns {"cover": [tagline...], "chapters": [...]}.

    AI path when configured; otherwise a 4-chapter template structure. Every
    chapter is prose where blank lines separate paragraphs.
    """
    ai_cover = generate("chapter", niche, hint)
    if ai_cover:
        cover = ["How to Buy %s: The 2026 Picks Guide" % (niche.strip().title() or "Your Pick"),
                 ai_cover[0][:160]]
        chapters = ai_cover if len(ai_cover) >= 3 else _template_chapters(niche)[:3] + ai_cover[:1]
    else:
        cover = [
            "How to Buy %s: The 2026 Picks Guide" % (niche.strip().title() or "Your Pick"),
            "What to look for, what to skip, and which options keep paying off.",
        ]
        chapters = _template_chapters(niche) or ["A short, skimmable guide to buying %s for less hassle and more value at checkout." % (niche or "the right pick")]

    # All chapters are paragraphs split on blank lines; strip empties.
    chapters = ["\n\n".join([p.strip() for p in ch.split("\n\n") if p.strip()]) for ch in chapters]
    return {"cover": cover, "chapters": [c for c in chapters if c]}


def _template_chapters(niche):
    nice = niche or "your pick"
    return [
        ("Why %s is worth your attention this year. Prices move, options "
         "multiply, and most guides repeat the same three names. This one looks "
         "at the criteria actual buyers rave about — build quality, materials, "
         "and real-world reviews — and ignores the marketing.\n\nBy the end "
         "you'll know exactly what to check before you spend." % nice),
        ("The four checks that filter out 90% of the noise. First, review count "
         "matters more than the stars: 1,000+ reviews at 4.5 beats 40 reviews at "
         "5.0. Second, look for what buyers mention across listings — the same "
         "praise or the same complaint is a signal. Third, check the price "
         "history trend; steady beats flash-sale spikes. Fourth, prefer "
         "listings sold and shipped by Amazon for returns that just work."),
        ("How to stack your shortlist. Pick three candidates that pass the four "
         "checks, compare them directly on price and reviews, and let the one "
         "with the best reviews-per-price win. If two are tied, choose the "
         "better-reviewed — it outsells for a reason.\n\nKeep your list under "
         "five items; every extra choice shifts your odds toward a quick buy "
         "you regret."),
        ("The short version for your wallet. Trust volume of reviews over hype, "
         "target fair prices over discounts, and always verify returns before "
         "checkout. That's the whole secret — the pstore picks listed on our "
         "pages are simply the results of running these exact checks."),
    ]
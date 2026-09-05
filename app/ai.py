# -*- coding: utf-8 -*-
"""pstore AI module: OpenAI-compatible chat completions over urllib (stdlib).

Supports several providers so the site can run on free tiers:
  openai   -> AI_API_KEY     / https://api.openai.com/v1         (gpt-4o-mini)
  opencode -> OPENCODE_API_KEY/ https://opencode.ai/zen/v1       (free Zen models)
  mistral  -> MISTRAL_API_KEY / https://api.mistral.ai/v1        (free Experiment tier)
  nvidia   -> NVIDIA_API_KEY  / https://integrate.api.nvidia.com/v1 (free NIM models)

Provider selection (ai.active_provider()):
  1. $AI_PROVIDER name (if its key is present)
  2. a provider configured at runtime via the /admin/ebooks panel (in-memory)
  3. first provider in DEFAULT_PRIORITY that has a key configured (env or legacy AI_*)

When no provider is configured or a request fails, we fall back to deterministic
templates so the site (and every test) still works offline. Everything routes
through ai._urlopen so tests can stub it. Env vars only ever supply keys/models;
we never persist or log secrets.
"""
import json
import os
import time
import urllib.request

# Legacy/OpenAI globals (kept so older code + tests that mutate ai.API_KEY keep working).
API_KEY = os.environ.get("AI_API_KEY", "")
BASE_URL = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")

PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "key_env": "AI_API_KEY",
        "base_env": "AI_BASE_URL",
        "model_env": "AI_MODEL",
        "base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "models": [
            "gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1-nano",
            "o4-mini", "o3-mini", "gpt-5-mini",
        ],
        "key_hint": "sk-... (platform.openai.com/api-keys)",
        "home": "https://platform.openai.com/api-keys",
        "free": False,
    },
    "opencode": {
        "label": "OpenCode Zen (free models)",
        "key_env": "OPENCODE_API_KEY",
        "base_env": "OPENCODE_BASE_URL",
        "model_env": "OPENCODE_MODEL",
        "base": "https://opencode.ai/zen/v1",
        "model": "kimi-k2.5-free",
        "models": [
            "kimi-k2.5-free", "kimi-k2.5", "kimi-k2.6", "kimi-k3",
            "mimo-v2.5-free", "mimo-v2-flash-free", "mimo-v2-pro-free",
            "hy3-free", "ling-3.0-flash-fin-free",
            "nemotron-3-ultra-free", "nemotron-3.5-lightning-free",
            "glm-5.1", "glm-5.2", "qwen3.6-plus-free", "minimax-m2.5-free",
        ],
        "key_hint": "open code key (opencode.ai/zen) — free models like kimi-k2.5-free",
        "home": "https://opencode.ai/zen",
        "free": True,
    },
    "mistral": {
        "label": "Mistral AI (free tier)",
        "key_env": "MISTRAL_API_KEY",
        "base_env": "MISTRAL_BASE_URL",
        "model_env": "MISTRAL_MODEL",
        "base": "https://api.mistral.ai/v1",
        "model": "open-mistral-7b",
        "models": [
            "open-mistral-7b", "open-mixtral-8x7b",
            "mistral-small-latest", "mistral-medium-latest", "mistral-large-latest",
        ],
        "key_hint": "console.mistral.ai key — free Experiment plan",
        "home": "https://console.mistral.ai",
        "free": True,
    },
    "nvidia": {
        "label": "NVIDIA NIM (free)",
        "key_env": "NVIDIA_API_KEY",
        "base_env": "NVIDIA_BASE_URL",
        "model_env": "NVIDIA_MODEL",
        "base": "https://integrate.api.nvidia.com/v1",
        "model": "meta/llama-3.3-70b-instruct",
        "models": [
            "meta/llama-3.3-70b-instruct", "meta/llama-3.1-405b-instruct",
            "mistralai/mistral-nemo-12b-v1", "google/gemma-2-27b-it",
            "deepseek-ai/deepseek-v3", "nvidia/llama-3.1-nemotron-70b-instruct",
        ],
        "key_hint": "nvapi-... (build.nvidia.com) — free NIM models",
        "home": "https://build.nvidia.com",
        "free": True,
    },
}

DEFAULT_PRIORITY = ("openai", "opencode", "mistral", "nvidia")

# Runtime overrides set from the admin AI panel (never written to disk/DB).
_RUNTIME = {}  # provider -> {"key": ..., "model": ..., "base": ...}

SUPPORTED_TEMPLATES = {
    "headline": "curious, concrete and click-worthy",
    "subheadline": "supportive, specific, no hype",
    "chapter": "instructive and skimmable, friendly tone",
}


def _runtime(provider):
    return _RUNTIME.get(provider) or {}


def key_for(provider):
    rt = _runtime(provider)
    if rt.get("key"):
        return rt["key"]
    meta = PROVIDERS[provider]
    if provider == "openai":
        return API_KEY
    return os.environ.get(meta["key_env"], "")


def base_for(provider):
    rt = _runtime(provider)
    if rt.get("base"):
        return rt["base"].rstrip("/")
    meta = PROVIDERS[provider]
    return (os.environ.get(meta["base_env"], "") or meta["base"]).rstrip("/")


def model_for(provider):
    rt = _runtime(provider)
    if rt.get("model"):
        return rt["model"]
    meta = PROVIDERS[provider]
    if provider == "openai":
        return MODEL
    return os.environ.get(meta["model_env"], "") or meta["model"]


def models_for(provider):
    """Selectable model ids for a provider: curated free list first, then the
    default. Keeps the picker useful even when the provider /models API is
    unreachable (free tiers throttle it hard)."""
    meta = PROVIDERS.get(provider)
    if not meta:
        return []
    models = list(meta.get("models") or [])
    default = meta.get("model")
    if default and default not in models:
        models.insert(0, default)
    return models


def _has_key(provider):
    return bool(key_for(provider))


def active_provider():
    want = os.environ.get("AI_PROVIDER", "").strip().lower()
    if want in PROVIDERS:
        return want if _has_key(want) else None
    for name, entry in _RUNTIME.items():
        if name in PROVIDERS and entry.get("key"):
            return name
    for name in DEFAULT_PRIORITY:
        if _has_key(name):
            return name
    return None


def configured():
    name = active_provider()
    return bool(name if (name and key_for(name) and model_for(name)) else None)


def providers():
    """Status for every provider (names, models, config source). No secrets."""
    active = active_provider()
    out = []
    for name, meta in PROVIDERS.items():
        rt = _runtime(name)
        if rt.get("key"):
            source = "runtime"
        elif name == "openai":
            source = "env" if API_KEY else ""
        elif os.environ.get(meta["key_env"], ""):
            source = "env"
        else:
            source = ""
        out.append({
            "name": name,
            "label": meta["label"],
            "key_env": meta["key_env"],
            "model_env": meta["model_env"],
            "default_model": meta["model"],
            "models": models_for(name),
            "base_url": meta["base"],
            "home": meta["home"],
            "key_hint": meta["key_hint"],
            "free": meta["free"],
            "configured": bool(_has_key(name) and model_for(name)),
            "source": source,
            "model": model_for(name) if _has_key(name) else "",
            "active": name == active,
        })
    return out


# Test hook (same pattern as amazon._urlopen / mailer._send).
_urlopen = None


def _open(req, timeout):
    if _urlopen is not None:
        resp = _urlopen(req)
        return json.loads(resp) if isinstance(resp, (str, bytes)) else resp
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _request(provider, payload, timeout=25):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_for(provider) + "/chat/completions", data=data, method="POST", headers={
            "Authorization": "Bearer " + key_for(provider),
            "Content-Type": "application/json",
        })
    return _open(req, timeout)


def _complete(system, user, timeout=25):
    provider = active_provider()
    if not provider:
        return ""
    payload = {"model": model_for(provider), "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], "temperature": 0.8, "max_tokens": 700}
    out = _request(provider, payload, timeout)
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


def test(provider, key, model="", base=""):
    """Fire one tiny chat request to prove a key works. Returns a result dict."""
    provider = (provider or "openai").strip().lower() or "openai"
    if provider not in PROVIDERS:
        return {"ok": False, "provider": provider, "error": "unknown provider '%s'" % provider}
    key = (key or "").strip()
    if not key:
        return {"ok": False, "provider": provider,
                "error": "no API key given — paste one to test it"}
    model = (model or "").strip() or PROVIDERS[provider]["model"]
    base = (base or "").strip() or PROVIDERS[provider]["base"]
    payload = {"model": model,
               "messages": [{"role": "user", "content": "Reply with exactly: pstore-ok"}],
               "max_tokens": 10, "temperature": 0}
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"), method="POST", headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            })
        out = _open(req, 30)
        reply = ((out.get("choices") or [{}])[0].get("message", {})
                 .get("content") or "").strip()[:60]
        return {"ok": True, "provider": provider, "model": model,
                "reply": reply or "(empty reply)",
                "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "provider": provider, "model": model,
                "error": str(e)[:220],
                "latency_ms": int((time.monotonic() - t0) * 1000)}


def list_models(provider, key, base=""):
    """Available model ids for a provider (used to fill the admin dropdown)."""
    provider = (provider or "openai").strip().lower() or "openai"
    if provider not in PROVIDERS:
        return []
    key = (key or "").strip()
    if not key:
        return []
    base = (base or "").strip() or PROVIDERS[provider]["base"]
    try:
        req = urllib.request.Request(
            base.rstrip("/") + "/models",
            headers={"Authorization": "Bearer " + key})
        out = _open(req, 20)
        ids = [m.get("id") for m in out.get("data", []) if m.get("id")]
        return sorted(ids)
    except Exception:
        return []


def configure_runtime(provider, key, model="", base=""):
    """Activate a provider for this running process (admin panel). No secrets
    are persisted anywhere — redeploy falls back to envs/memory."""
    provider = (provider or "openai").strip().lower() or "openai"
    if provider not in PROVIDERS:
        return {"ok": False, "error": "unknown provider '%s'" % provider}
    key = (key or "").strip()
    if not key:
        return {"ok": False, "error": "no API key given for %s" % provider}
    entry = {"key": key, "model": (model or "").strip() or PROVIDERS[provider]["model"]}
    if base and base.strip():
        entry["base"] = base.strip()
    _RUNTIME[provider] = entry
    return {"ok": True, "provider": provider, "model": entry["model"],
            "active": provider == active_provider()}


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
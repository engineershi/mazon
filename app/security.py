# -*- coding: utf-8 -*-
"""pstore security helpers (stdlib only).

Rate limiting (per client), a concurrency cap so heavy load fails fast instead
of exhausting threads, request-size caps, and HMAC-signed one-time tokens used
for OAuth state + CSRF defense. None of this is a substitute for TLS/ops policy,
but it raises the practical bar against brute force, flooding and CSRF.
"""
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time

# Boot-time secrets — rotate on restart; never expose via API/logs.
_OAUTH_SECRET = secrets.token_hex(32)
HASH_SECRET = secrets.token_hex(32)

MAX_BODY = 64 * 1024        # reject bodies larger than this (413)
MAX_URL = 4096              # reject request targets longer than this (414)

# One lightweight semaphore bounds real concurrency so DDoS-style request
# floods degrade to quick 503s instead of thread exhaustion.
CONCURRENCY = threading.BoundedSemaphore(64)


class RateLimiter:
    """Sliding-window limit, keyed by (namespace, key). Thread-safe."""

    def __init__(self, limit, window_sec):
        self.limit = limit
        self.window = window_sec
        self._hits = {}
        self._lock = threading.Lock()

    def hit(self, key):
        now = time.monotonic()
        with self._lock:
            bucket = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(bucket) < self.limit:
                bucket.append(now)
                self._hits[key] = bucket
                return True
            self._hits[key] = bucket
            return False

    def clear(self, key):
        with self._lock:
            self._hits.pop(key, None)


# Per-client budgets (a client = peer socket, or forwarded chain when present).
LOGIN_LIMITER = RateLimiter(limit=5, window_sec=15 * 60)     # 5 attempts / 15 min
API_LIMITER = RateLimiter(limit=240, window_sec=60)          # /api flood guard
HTTP_LIMITER = RateLimiter(limit=720, window_sec=60)         # global per-client cap
SUBSCRIBE_LIMITER = RateLimiter(limit=6, window_sec=10 * 60)  # opt-in abuse guard
TRACK_LIMITER = RateLimiter(limit=180, window_sec=60)        # click beacons
PAGEVIEW_LIMITER = RateLimiter(limit=120, window_sec=60)     # pageview/event beacons


def client_key(headers, peer_ip):
    """One stable-ish key per client: prefer the proxy's forwarded chain, but
    pin to the peer address so a spoofed X-Forwarded-For can't reset a ban."""
    xff = (headers.get("X-Forwarded-For") or "").strip()
    chain = [x.strip() for x in xff.split(",") if x.strip()]
    try:
        first = str(ipaddress.ip_address(chain[0]))  # reject junk -> ValueError
    except Exception:
        first = ""
    base = first or (peer_ip or "unknown")
    return "%s|%s" % (base, peer_ip or "unknown")


def make_token(scope, ttl_sec):
    """Signed one-time token: scope:expiry:nonce:sig. Verifying populates the
    same fields; signature proves this server issued it."""
    exp = int(time.time()) + ttl_sec
    nonce = secrets.token_hex(16)
    raw = "%s:%d:%s" % (scope, exp, nonce)
    sig = hmac.new(HASH_SECRET.encode("utf-8"), raw.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return "%s:%s" % (raw, sig)


def verify_token(token):
    """Returns the scope on success, None when missing/expired/forged."""
    if not token:
        return None
    try:
        head, sig = token.rsplit(":", 1)
        scope, exp, nonce = head.rsplit(":", 2)
        exp = int(exp)
    except Exception:
        return None
    expected = hmac.new(HASH_SECRET.encode("utf-8"), head.encode("utf-8"),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if exp < time.time():
        return None
    return scope


def ip_token(ip):
    """One-way, salted fingerprint of a client IP for analytics — records who
    clicked without ever storing a raw address."""
    if not ip:
        return ""
    return hashlib.sha256((ip + "|" + HASH_SECRET).encode("utf-8")).hexdigest()[:16]
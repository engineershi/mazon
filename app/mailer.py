# -*- coding: utf-8 -*-
"""pstore email sender: sends the 5-email buyer sequence over SMTP (stdlib).

Config via env (never commit secrets):
  SMTP_HOST        e.g. smtp.gmail.com
  SMTP_PORT        587 (STARTTLS) or 465 (SSL)
  SMTP_USER        full account, e.g. your@gmail.com
  SMTP_PASSWORD    app password / API token, NOT the account login password
  SMTP_FROM        optional display address; defaults to SMTP_USER
  SMTP_STARTTLS    1 (default) to upgrade with STARTTLS, 0 for implicit SSL
  PSTORE_URL       site origin used to build per-subscriber unsubscribe links

When SMTP is not configured the admin page says so and sends are refused —
everything else (opt-in capture, unsubscribe, analytics) keeps working.
"""
import os
import smtplib
import urllib.parse
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import market_engine
import security

STORE_NAME = os.environ.get("PSTORE_NAME", "pstore").strip() or "pstore"
REPLY_TO = (os.environ.get("SMTP_REPLY_TO", "") or "").strip()
EMAIL_SEND_DELAY = float(os.environ.get("SMTP_SEND_DELAY", "0") or "0")  # seconds between sends

SMTP_HOST = os.environ.get("SMTP_HOST", "")
try:
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
except (TypeError, ValueError):
    SMTP_PORT = 587
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "") or (SMTP_USER or "noreply@localhost")
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "1") == "1"
MAX_EMAILS_PER_RUN = int(os.environ.get("SMTP_MAX_PER_RUN", "50") or "50")
SEQUENCE_LENGTH = 5


def configured():
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def unsubscribe_url(email):
    token = security.make_token("unsub:" + email.lower(), 30 * 24 * 3600)
    base = os.environ.get("PSTORE_URL", "").rstrip("/")
    return "%s/unsubscribe?e=%s&t=%s" % (base, urllib.parse.quote(email),
                                         urllib.parse.quote(token))


def _footer(email):
    return ("\n\n— %s\n\nYou're getting this because you opted in on a %s page.\n"
            "Change your mind any time: %s"
            % (STORE_NAME, STORE_NAME, unsubscribe_url(email)))


def _guess_first_name(email, stored="", fallback="there"):
    """Best first-name to greet the reader with, from most to least specific:
    a stored name we captured, else one derived from the email local-part
    (e.g. jane.doe@example.com -> "Jane"), else the plain fallback."""
    if stored and stored.strip():
        return stored.strip()
    if email and "@" in email:
        local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ").strip()
        if local:
            return " ".join(w[:1].upper() + w[1:] for w in local.split() if w)[:80]
    return fallback


def render_body(mail, to_name="there", site_name=STORE_NAME, email="", tracked_link=""):
    """Turn one mail dict from market_engine.build_email_sequence into a
    sendable plain-text body (drop the redundant Subject: line, fill the
    {{placeholders}}, append a permission-reminder + unsubscribe footer).

    {{first_name}} uses a captured name, else a name derived from the reader's
    email local-part, so every send stays personal even without a stored name.
    {{your_name}} is the store/sender's signature (configurable via PSTORE_NAME).

    `tracked_link` (optional) rewrites the raw affiliate URLs inside the body to
    a click-tracked link so email outbound clicks are attributed."""
    body = mail.get("body") or ""
    if body.startswith("Subject:"):
        body = body.split("\n\n", 1)[-1]
    greeted = _guess_first_name(email, to_name, fallback="there")
    body = body.replace("{{first_name}}", greeted).replace("{first_name}", greeted) \
               .replace("{{your_name}}", site_name or STORE_NAME) \
               .replace("{your_name}", site_name or STORE_NAME)
    if tracked_link:
        body = _wrap_links(body, tracked_link)
    if email:
        body += _footer(email)
    return body


# Test hook: tests assign _send(subject, body, to, attachments=None) -> True/False.
# Keeping the network path behind one function means sending is fully stub-able offline.
_send = None


def track_token(external, scope="e", ttl=30 * 24 * 3600):
    """Signed token whose scope carries the tracked-link payload (default 30-day
    click window). `external` is a `|`-joined string (niche slug, asin, sub id,
    email index) the recipient handler decodes back off the scope after verify."""
    return security.make_token("%s:%s" % (scope, external), ttl)


def decode_track_token(token, scope="e"):
    """Verify a tracked-link/open token and return the stored `external` payload
    as a tuple, or None when forged/expired. Handles URL-encoded tokens."""
    import urllib.parse as _up
    sc = security.verify_token(_up.unquote(token))
    if not sc or not sc.startswith(scope + ":"):
        return None
    return tuple(sc[len(scope) + 1:].split("|"))


def tracked_url(keyword, asin, sid=None, idx=0):
    """Click-tracked affiliate link for an email. Wraps the niche + ASIN + who +
    which email index so the redirect records a click attributed to the email."""
    token = track_token("%s|%s|%s|%s" % (keyword, asin, sid or "", idx or 0))
    base = os.environ.get("PSTORE_URL", "").rstrip("/")
    return "%s/e/%s" % (base, urllib.parse.quote(token, safe=""))


def open_pixel_url(keyword, asin, sid=None, idx=0):
    """1x1 open-tracking pixel URL for an email."""
    token = track_token("%s|%s|%s|%s" % (keyword, asin, sid or "", idx or 0), scope="o")
    base = os.environ.get("PSTORE_URL", "").rstrip("/")
    return "%s/e/o/%s" % (base, urllib.parse.quote(token, safe=""))


def _wrap_links(body, link_url, pid=""):
    """Rewrite the plain affiliate links inside a sequence body to the tracked
    URL so email clicks are attributed. Falls back to the raw link when the
    tracked URL is unavailable."""
    def _link_replace(m):
        return link_url or m.group(0)
    import re as _re
    urls = _re.findall(r"https?://[^\s)\]]+", body)
    for u in urls:
        if link_url:
            body = body.replace(u, link_url)
    return body


def _build_message(subject, body, to, from_addr, attachments=None, pixel_url=""):
    msg = MIMEMultipart()
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((STORE_NAME, from_addr))
    msg["To"] = to
    if REPLY_TO:
        msg["Reply-To"] = REPLY_TO
        msg["Return-Path"] = REPLY_TO
    msg["List-Unsubscribe"] = "<mailto:%s?subject=unsubscribe>" % from_addr
    if pixel_url:
        body = "%s\n\n<img src=\"%s\" width=\"1\" height=\"1\" alt=\"\" border=\"0\">" % (body, pixel_url)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for name, data in (attachments or []):
        part = MIMEApplication(data, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=name)
        msg.attach(part)
    return msg


def _smtp_send(subject, body, to, from_addr=None, attachments=None, pixel_url=""):
    from_addr = from_addr or SMTP_FROM
    msg = _build_message(subject, body, to, from_addr, attachments, pixel_url)
    try:
        if SMTP_STARTTLS:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
            server.quit()
        else:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
            server.quit()
        return True
    except Exception:
        return False


def send(subject, body, to, attachments=None, pixel_url=""):
    if not configured():
        return False
    if _send is not None:
        return bool(_send(subject, body, to, attachments, pixel_url))
    return _smtp_send(subject, body, to, attachments=attachments, pixel_url=pixel_url)


# ------------------------------------------------------------------ sequence

def next_email(keyword, items, index):
    """Return the mail dict at 1-based `index` in the 5-email sequence, or None."""
    seq = market_engine.build_email_sequence(keyword, items)
    if not seq or index < 1 or index > len(seq):
        return None
    return seq[index - 1]
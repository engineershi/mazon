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

STORE_NAME = "pstore"

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


def render_body(mail, to_name="there", site_name=STORE_NAME, email=""):
    """Turn one mail dict from market_engine.build_email_sequence into a
    sendable plain-text body (drop the redundant Subject: line, fill the
    {{placeholders}}, append a permission-reminder + unsubscribe footer)."""
    body = mail.get("body") or ""
    if body.startswith("Subject:"):
        body = body.split("\n\n", 1)[-1]
    body = body.replace("{{first_name}}", to_name or "there") \
               .replace("{{your_name}}", site_name)
    if email:
        body += _footer(email)
    return body


# Test hook: tests assign _send(subject, body, to, attachments=None) -> True/False.
# Keeping the network path behind one function means sending is fully stub-able offline.
_send = None


def _build_message(subject, body, to, from_addr, attachments=None):
    msg = MIMEMultipart()
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((STORE_NAME, from_addr))
    msg["To"] = to
    msg["List-Unsubscribe"] = "<mailto:%s?subject=unsubscribe>" % from_addr
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for name, data in (attachments or []):
        part = MIMEApplication(data, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment",
                        filename=("=?utf-8?b?%s?=" % __import__("base64").b64encode(
                            name.encode("utf-8")).decode("ascii")))
        msg.attach(part)
    return msg


def _smtp_send(subject, body, to, from_addr=None, attachments=None):
    from_addr = from_addr or SMTP_FROM
    msg = _build_message(subject, body, to, from_addr, attachments)
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


def send(subject, body, to, attachments=None):
    if not configured():
        return False
    if _send is not None:
        return bool(_send(subject, body, to, attachments))
    return _smtp_send(subject, body, to, attachments=attachments)


# ------------------------------------------------------------------ sequence

def next_email(keyword, items, index):
    """Return the mail dict at 1-based `index` in the 5-email sequence, or None."""
    seq = market_engine.build_email_sequence(keyword, items)
    if not seq or index < 1 or index > len(seq):
        return None
    return seq[index - 1]
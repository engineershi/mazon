# -*- coding: utf-8 -*-
"""pstore OAuth2 login helpers for Google + Facebook (stdlib urllib only).

Configure via env (never commit secrets):
  OAUTH_GOOGLE_CLIENT_ID / OAUTH_GOOGLE_CLIENT_SECRET
  OAUTH_FACEBOOK_APP_ID / OAUTH_FACEBOOK_APP_SECRET
and PSTORE_URL (drives redirect_uri). The callback grants a session only when
the provider's verified email matches the admin email configured on the server.
"""
import json
import os
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("PSTORE_URL", "").rstrip("/")

GOOGLE_CLIENT_ID = os.environ.get("OAUTH_GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("OAUTH_GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT = BASE_URL + "/admin/oauth/google/callback"
FACEBOOK_APP_ID = os.environ.get("OAUTH_FACEBOOK_APP_ID", "")
FACEBOOK_APP_SECRET = os.environ.get("OAUTH_FACEBOOK_APP_SECRET", "")
FACEBOOK_REDIRECT = BASE_URL + "/admin/oauth/fb/callback"

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://www.googleapis.com/oauth2/v3/userinfo"
FACEBOOK_AUTH = "https://www.facebook.com/v21.0/dialog/oauth"
FACEBOOK_TOKEN = "https://graph.facebook.com/v21.0/oauth/access_token"
FACEBOOK_ME = "https://graph.facebook.com/v21.0/me"

# Module-level hook so tests can fake provider network calls.
_urlopen = None


def _open(req, timeout=15):
    if _urlopen is not None:
        return _urlopen(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def providers_configured():
    out = []
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        out.append(("google", "Google", "Continue with Google"))
    if FACEBOOK_APP_ID and FACEBOOK_APP_SECRET:
        out.append(("facebook", "Facebook", "Continue with Facebook"))
    return out


def authorize_url(provider, state):
    if provider == "google":
        return ("%s?client_id=%s&redirect_uri=%s&response_type=code"
                "&scope=openid%%20email%%20profile&access_type=online"
                "&prompt=select_account&state=%s"
                % (GOOGLE_AUTH, urllib.parse.quote(GOOGLE_CLIENT_ID),
                   urllib.parse.quote(GOOGLE_REDIRECT, safe=""),
                   urllib.parse.quote(state, safe="")))
    if provider == "facebook":
        return ("%s?client_id=%s&redirect_uri=%s&scope=email&state=%s"
                % (FACEBOOK_AUTH, urllib.parse.quote(FACEBOOK_APP_ID),
                   urllib.parse.quote(FACEBOOK_REDIRECT, safe=""),
                   urllib.parse.quote(state, safe="")))
    return None


def exchange(provider, code):
    """Exchange the authorization code for the provider user's email."""
    if provider == "google":
        form = urllib.parse.urlencode({
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": GOOGLE_REDIRECT,
        }).encode("utf-8")
        req = urllib.request.Request(GOOGLE_TOKEN, data=form,
                                     headers={"Content-Type":
                                              "application/x-www-form-urlencoded"})
        token = json.loads(_open(req).read().decode("utf-8", "replace"))
        access = token.get("access_token")
        if not access:
            raise ValueError("OAuth token exchange failed")
        req = urllib.request.Request(
            GOOGLE_USERINFO, headers={"Authorization": "Bearer %s" % access})
        info = json.loads(_open(req).read().decode("utf-8", "replace"))
        return (info.get("email") or "").lower(), info.get("name") or ""
    if provider == "facebook":
        url = ("%s?client_id=%s&client_secret=%s&redirect_uri=%s&code=%s"
               % (FACEBOOK_TOKEN, urllib.parse.quote(FACEBOOK_APP_ID),
                  urllib.parse.quote(FACEBOOK_APP_SECRET),
                  urllib.parse.quote(FACEBOOK_REDIRECT, safe=""),
                  urllib.parse.quote(code, safe="")))
        token = json.loads(_open(urllib.request.Request(url)).read().decode("utf-8", "replace"))
        access = token.get("access_token")
        if not access:
            raise ValueError("OAuth token exchange failed")
        url = ("%s?fields=id,name,email&access_token=%s"
               % (FACEBOOK_ME, urllib.parse.quote(access, safe="")))
        info = json.loads(_open(urllib.request.Request(url)).read().decode("utf-8", "replace"))
        return (info.get("email") or "").lower(), info.get("name") or ""
    raise ValueError("Unknown OAuth provider: %s" % provider)
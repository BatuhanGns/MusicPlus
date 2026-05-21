"""
Kimlik doğrulama (OAuth2 / PKCE) route'ları.
- GET  /login
- GET  /callback
- GET  /logout  (ve /api/logout)
"""

import os
import logging
from flask import Blueprint, session, request, redirect, url_for

import config
from extensions import spotify, sheets

logger = logging.getLogger(__name__)
bp = Blueprint("auth", __name__)


LOGIN_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Giriş Yap – Music+</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=DM+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<style>
  :root{{--bg:#0a0a0a;--surface:#111;--border:#222;--green:#1db954;--text:#e8e8e8;--muted:#555;}}
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:var(--bg);color:var(--text);font-family:'DM Mono',monospace;min-height:100vh;display:flex;align-items:center;justify-content:center;}}
  body::before{{content:'';position:fixed;inset:0;background-image:linear-gradient(var(--border) 1px,transparent 1px),linear-gradient(90deg,var(--border) 1px,transparent 1px);background-size:48px 48px;opacity:.3;pointer-events:none;}}
  .card{{position:relative;z-index:1;background:var(--surface);border:1px solid var(--border);padding:56px 48px;max-width:420px;width:100%;text-align:center;}}
  h1{{font-family:'Syne',sans-serif;font-size:36px;font-weight:800;letter-spacing:-1px;margin-bottom:8px;}}
  h1 span{{color:var(--green);}}
  p{{font-size:12px;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-bottom:40px;}}
  a.btn{{display:block;background:var(--green);color:#000;font-family:'Syne',sans-serif;font-size:14px;font-weight:700;letter-spacing:1px;text-transform:uppercase;text-decoration:none;padding:18px 32px;transition:opacity .2s;}}
  a.btn:hover{{opacity:.85;}}
  .note{{margin-top:20px;font-size:11px;color:var(--muted);line-height:1.7;}}
</style>
</head>
<body>
<div class="card">
  <h1>MUSIC<span style="color:var(--green)">+</span></h1>
  <p>Kişisel Spotify İstatistiklerin</p>
  <a class="btn" href="{auth_url}">Spotify ile Giriş Yap</a>
  <div class="note">Bu uygulama yalnızca dinleme verilerini okur.<br>Hiçbir verin üçüncü taraflarla paylaşılmaz.</div>
</div>
</body>
</html>"""


def _get_redirect_uri():
    base = os.environ.get("REDIRECT_URI") or ("https://" + request.host + "/callback")
    redirect_uri = base.rstrip("/")
    if not redirect_uri.endswith("/callback"):
        redirect_uri += "/callback"
    return redirect_uri


@bp.route("/login")
def login_page():
    redirect_uri = _get_redirect_uri()
    auth_url = spotify.get_auth_url(redirect_uri)
    return LOGIN_HTML_TEMPLATE.format(auth_url=auth_url)


@bp.route("/callback")
def callback():
    code = request.args.get("code")
    error = request.args.get("error")
    if error or not code:
        return redirect("/login")

    redirect_uri = _get_redirect_uri()
    try:
        spotify.exchange_code(code, redirect_uri)
        session.permanent = True
        me = spotify._req("GET", "/me")
        session["user_id"] = me.get("id", "")
        session["display_name"] = me.get("display_name", me.get("id", "Kullanıcı"))
        session["refresh_token"] = spotify.refresh_token

        uid = session["user_id"]
        name = session["display_name"]
        token = spotify.refresh_token

        if uid:
            if not sheets._find_sheet(uid):
                sheets._ensure_user_sheet(uid)
                sheets.set_user_permission(uid, name, False, token)
            else:
                sheets.save_refresh_token(uid, token)

        return redirect("/")
    except Exception as e:
        logger.error(f"❌ OAuth callback hatası: {e}")
        return redirect("/login")


@bp.route("/api/logout")
@bp.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

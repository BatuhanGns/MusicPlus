"""
Dashboard ana sayfa route'u.
- GET  /
- GET  /dashboard
"""

import logging
from flask import Blueprint, session, render_template, redirect

from extensions import load_user_data, sync_job
import config

logger = logging.getLogger(__name__)
bp = Blueprint("dashboard", __name__)


@bp.route("/")
@bp.route("/dashboard")
def dashboard():
    uid     = session.get("user_id")
    r_token = session.get("refresh_token")

    # Kullanıcı giriş yapmamışsa login'e yönlendir
    if not uid or not r_token:
        return redirect("/login")

    # Bellek cache'inde bu kullanıcının refresh_token'ı yoksa ekle
    if uid not in config._refresh_tokens:
        config._refresh_tokens[uid] = r_token
        logger.info(f"🔄 Refresh token session'dan belleğe yüklendi: {uid}")

    # İlk yüklemede veriyi çek
    if uid not in config._user_cache:
        try:
            load_user_data(uid)
            sync_job(uid)
        except Exception as e:
            logger.warning(f"⚠️ Auto-sync hatası: {e}")

    return render_template("dashboard.html")

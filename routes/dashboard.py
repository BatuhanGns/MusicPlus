"""
Dashboard ana sayfa route'u.
- GET  /
- GET  /dashboard
"""

import logging
from flask import Blueprint, session, render_template, redirect

from extensions import spotify, load_user_data, sync_job
import config

logger = logging.getLogger(__name__)
bp = Blueprint("dashboard", __name__)


@bp.route("/")
@bp.route("/dashboard")
def dashboard():
    uid = session.get("user_id")
    refresh_token = session.get("refresh_token")

    if not uid or not refresh_token:
        return redirect("/login")

    if not spotify.refresh_token and refresh_token:
        spotify.refresh_token = refresh_token
        logger.info(f"🔄 Token session'dan geri yüklendi: {uid}")

    if uid not in config._user_cache:
        try:
            load_user_data(uid)
            sync_job(uid)
        except Exception as e:
            logger.warning(f"⚠️ Auto-sync hatası: {e}")

    return render_template("dashboard.html")

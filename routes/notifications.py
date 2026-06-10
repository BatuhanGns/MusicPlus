"""
Bildirim ayarları API endpoint'leri.
- GET  /api/bildirim-ayarlari
- POST /api/bildirim-ayarlari
"""

import logging
from flask import Blueprint, jsonify, request

from extensions import get_current_user_id, sheets

logger = logging.getLogger(__name__)
bp = Blueprint("notifications", __name__)


@bp.route("/api/bildirim-ayarlari", methods=["GET"])
def get_bildirim_ayarlari():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    try:
        ayarlar = sheets.get_notification_settings(uid)
        return jsonify(ayarlar)
    except Exception as e:
        logger.error(f"Bildirim ayarları GET hatası ({uid}): {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/bildirim-ayarlari", methods=["POST"])
def save_bildirim_ayarlari():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401

    data = request.get_json(silent=True) or {}

    # Validasyon
    email       = data.get("email", "").strip()
    odeme_gunu  = data.get("spotify_odeme_gunu", "").strip()
    streak_bil  = bool(data.get("streak_bildirimi", False))
    ozet_bil    = bool(data.get("ozet_bildirimi", False))
    ozet_sik    = data.get("ozet_sikligi", "weekly")

    if email and "@" not in email:
        return jsonify({"error": "Geçersiz e-posta adresi"}), 400

    if odeme_gunu:
        try:
            gun = int(odeme_gunu)
            if not (1 <= gun <= 31):
                raise ValueError
        except ValueError:
            return jsonify({"error": "Ödeme günü 1-31 arasında olmalı"}), 400

    if ozet_sik not in ("weekly", "monthly"):
        ozet_sik = "weekly"

    try:
        sheets.save_notification_settings(uid, {
            "email":              email,
            "spotify_odeme_gunu": odeme_gunu,
            "streak_bildirimi":   streak_bil,
            "ozet_bildirimi":     ozet_bil,
            "ozet_sikligi":       ozet_sik,
        })
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Bildirim ayarları POST hatası ({uid}): {e}")
        return jsonify({"error": str(e)}), 500

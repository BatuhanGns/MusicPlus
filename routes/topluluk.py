"""
Topluluk istatistikleri ve gizlilik/izin yönetimi API'leri.
- GET  /api/istatistikler        → Topluluk istatistikleri
- POST /api/izin                 → İzin güncelleme
- GET  /api/izin                 → İzin durumu sorgulama
"""

import logging
from flask import Blueprint, request, jsonify

import config
from extensions import get_current_user_id, get_current_user_name, get_cached_data, load_user_data, sheets
from utils.helpers import compute_stats

logger = logging.getLogger(__name__)
bp = Blueprint("topluluk", __name__)


@bp.route("/api/istatistikler")
def api_istatistikler():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        permitted = sheets.get_all_permitted_users()
        if not permitted:
            return jsonify({"error": "Henüz kimse izin vermemiş"})
        headers, rows = sheets.get_combined_data(permitted)
        stats = compute_stats(headers, rows)
        if not stats:
            return jsonify({"error": "Veri yok"})
        stats["katilimci_sayisi"] = len(permitted)
        stats["son_sync"] = config._last_sync
        return jsonify(stats)
    except Exception as e:
        logger.error(f"❌ İstatistikler API hatası: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/izin", methods=["POST"])
def api_izin():
    try:
        uid = get_current_user_id()
        name = get_current_user_name()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        data = request.get_json()
        allowed = bool(data.get("allowed", False))
        sheets.set_user_permission(uid, name, allowed)
        return jsonify({"status": "ok", "allowed": allowed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/izin")
def api_izin_get():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"allowed": False})
        return jsonify({"allowed": sheets.get_user_permission(uid)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

"""
Topluluk istatistikleri ve gizlilik/izin yönetimi API'leri.
- GET  /api/istatistikler?aralik=1hafta|1ay|1yil|tumzamanlar
- POST /api/izin
- GET  /api/izin
"""

import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify

import config
from extensions import get_current_user_id, get_current_user_name, get_cached_data, load_user_data, sheets
from utils.helpers import compute_stats

logger = logging.getLogger(__name__)
bp = Blueprint("topluluk", __name__)


def _filter_rows_by_aralik(headers, rows, aralik):
    """stats.py ile aynı filtre mantığı."""
    if aralik == "tumzamanlar" or not aralik:
        return rows
    try:
        idx_iso = headers.index("_played_at_iso")
    except ValueError:
        return rows
    now = datetime.now(timezone.utc)
    if aralik == "1hafta":
        since = now - timedelta(weeks=1)
    elif aralik == "1ay":
        since = now - timedelta(days=30)
    elif aralik == "1yil":
        since = now - timedelta(days=365)
    else:
        return rows
    since_str = since.strftime("%Y-%m-%dT%H:%M")
    return [
        row for row in rows
        if len(row) > idx_iso
        and row[idx_iso].strip() not in ("", "—")
        and row[idx_iso].strip()[:16] >= since_str
    ]


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
        aralik = request.args.get("aralik", "tumzamanlar")
        filtered = _filter_rows_by_aralik(headers, rows, aralik)
        stats = compute_stats(headers, filtered)
        if not stats:
            return jsonify({"error": "Veri yok"})
        stats["katilimci_sayisi"] = len(permitted)
        stats["son_sync"] = config._last_sync
        stats["aralik"] = aralik
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Istatistikler API hatasi: {e}")
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

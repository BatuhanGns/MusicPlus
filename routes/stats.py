"""
Kullanıcı istatistik API'leri.
- GET  /api/dashboard?aralik=1hafta|1ay|1yil|tumzamanlar  -> Kişisel dashboard verisi
- GET  /api/now-playing      -> Şu an çalan şarkı
- GET  /api/playlists        -> Kullanıcı playlist'leri
- GET  /api/gamification     -> XP / Seviye / Seri durumu
"""

import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request

import config
from extensions import get_current_user_id, get_cached_data, load_user_data, spotify, sheets
from utils.helpers import compute_stats
from utils.gamification import compute_gamification, compute_xp_from_stats

logger = logging.getLogger(__name__)
bp = Blueprint("stats", __name__)


def _filter_rows_by_aralik(headers, rows, aralik):
    """
    Satırları seçilen zaman aralığına göre filtreler.
    aralik: '1hafta' | '1ay' | '1yil' | 'tumzamanlar'
    Filtreleme _played_at_iso sütununa göre yapılır.
    """
    if aralik == "tumzamanlar" or not aralik:
        return rows

    try:
        idx_iso = headers.index("_played_at_iso")
    except ValueError:
        return rows  # Sütun yoksa filtresiz dön

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

    filtered = []
    for row in rows:
        if len(row) <= idx_iso:
            continue
        iso = row[idx_iso].strip()
        if iso and iso != "—" and iso[:16] >= since_str:
            filtered.append(row)

    return filtered


@bp.route("/api/dashboard")
def api_dashboard():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401

        headers, rows = get_cached_data(uid)
        if not rows:
            load_user_data(uid)
            headers, rows = get_cached_data(uid)

        aralik        = request.args.get("aralik", "tumzamanlar")
        filtered_rows = _filter_rows_by_aralik(headers, rows, aralik)

        stats = compute_stats(headers, filtered_rows)
        if not stats:
            return jsonify({"error": "Veri yok"})

        stats["son_sync"] = config._last_sync
        stats["aralik"]   = aralik
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Dashboard API hatasi: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/now-playing")
def api_now_playing():
    try:
        data = spotify.get_now_playing()
        return jsonify(data)
    except Exception as e:
        logger.error(f"Now playing hatasi: {e}")
        return jsonify({"playing": False}), 200


@bp.route("/api/playlists")
def api_playlists():
    try:
        playlists = spotify.get_playlists()
        return jsonify({"playlists": playlists})
    except Exception as e:
        logger.error(f"Playlist hatasi: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/gamification")
def api_gamification():
    """
    Kullanıcının XP / Seviye / Seri durumunu döner.
    Gamification her zaman TÜM zamanlar verisi üzerinden hesaplanır.
    """
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401

        headers, rows = get_cached_data(uid)
        if not rows:
            load_user_data(uid)
            headers, rows = get_cached_data(uid)

        # XP + level: compute_stats() çıktısından hızlı hesapla (Sheets'i ekstra taramaz)
        _stats    = compute_stats(headers, rows) or {}
        xp_result = compute_xp_from_stats(_stats)

        # Streak: ham veri üzerinden hesapla
        full = compute_gamification(headers, rows)

        result = {
            "xp":           xp_result["xp"],
            "level":        xp_result["level"],
            "xp_breakdown": xp_result["xp_breakdown"],
            "streak":       full["streak"],
        }
        return jsonify(result)
    except Exception as e:
        logger.error(f"Gamification API hatasi: {e}")
        return jsonify({"error": str(e)}), 500

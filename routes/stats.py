"""
Kullanıcı istatistik API'leri.
- GET  /api/dashboard        → Kişisel dashboard verisi
- GET  /api/now-playing      → Şu an çalan şarkı
- GET  /api/playlists        → Kullanıcı playlist'leri
"""

import logging
from flask import Blueprint, jsonify

import config
from extensions import get_current_user_id, get_cached_data, load_user_data, spotify
from utils.helpers import compute_stats

logger = logging.getLogger(__name__)
bp = Blueprint("stats", __name__)


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
        stats = compute_stats(headers, rows)
        if not stats:
            return jsonify({"error": "Veri yok"})
        stats["son_sync"] = config._last_sync
        return jsonify(stats)
    except Exception as e:
        logger.error(f"❌ Dashboard API hatası: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/now-playing")
def api_now_playing():
    try:
        data = spotify.get_now_playing()
        return jsonify(data)
    except Exception as e:
        logger.error(f"❌ Now playing hatası: {e}")
        return jsonify({"playing": False}), 200


@bp.route("/api/playlists")
def api_playlists():
    try:
        playlists = spotify.get_playlists()
        return jsonify({"playlists": playlists})
    except Exception as e:
        logger.error(f"❌ Playlist hatası: {e}")
        return jsonify({"error": str(e)}), 500

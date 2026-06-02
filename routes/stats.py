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


def _filter_rows_by_aralik(headers, rows, aralik, baslangic=None, bitis=None):
    """
    Satırları seçilen zaman aralığına göre filtreler.
    aralik: 'buhafta' | 'buay' | 'buyil' | 'ozel' | 'tumzamanlar'
    Filtreleme _played_at_iso sütununa göre yapılır.
    ozel modunda baslangic ve bitis (YYYY-MM-DD) parametreleri kullanılır.
    """
    if aralik == "tumzamanlar" or not aralik:
        return rows

    try:
        idx_iso = headers.index("_played_at_iso")
    except ValueError:
        return rows  # Sütun yoksa filtresiz dön

    now = datetime.now(timezone.utc)

    if aralik == "buhafta":
        # Pazartesi başlangıcı (haftanın ilk günü)
        days_since_monday = now.weekday()  # 0=Pazartesi
        since = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        until = None
    elif aralik == "buay":
        # Bu ayın 1'i
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        until = None
    elif aralik == "buyil":
        # Bu yılın 1 Ocak'ı
        since = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        until = None
    elif aralik == "ozel" and baslangic:
        try:
            since = datetime.strptime(baslangic, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            until = (datetime.strptime(bitis, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                     + timedelta(days=1)) if bitis else None
        except Exception:
            return rows
    else:
        return rows

    since_str = since.strftime("%Y-%m-%dT%H:%M")
    until_str = until.strftime("%Y-%m-%dT%H:%M") if until else None

    filtered = []
    for row in rows:
        if len(row) <= idx_iso:
            continue
        iso = row[idx_iso].strip()
        if not iso or iso == "—":
            continue
        iso16 = iso[:16]
        if iso16 >= since_str and (until_str is None or iso16 < until_str):
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

        aralik     = request.args.get("aralik", "buyil")
        baslangic  = request.args.get("baslangic", None)
        bitis      = request.args.get("bitis", None)
        filtered_rows = _filter_rows_by_aralik(headers, rows, aralik, baslangic, bitis)

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

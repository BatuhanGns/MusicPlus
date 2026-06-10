"""
Sistem, sağlık ve yardımcı API'ler.
- GET  /api/system-stats
- GET  /api/export-csv
- GET  /api/export-spotify-json
- GET  /api/export-json
- GET  /api/sync   (ve /sync)
- GET  /api/health (ve /health)
"""

import io
import csv
import json
import time
import psutil
import logging
from flask import Blueprint, Response, request, jsonify, stream_with_context

import config
from extensions import get_current_user_id, get_cached_data, sheets, sync_job

logger = logging.getLogger(__name__)
bp = Blueprint("system", __name__)


@bp.route("/api/system-stats")
def api_system_stats():
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        ram_percent = mem.percent
        ram_used_mb = mem.used / (1024 * 1024)
        ram_total_mb = mem.total / (1024 * 1024)
        net = psutil.net_io_counters()
        net_sent_mb = net.bytes_sent / (1024 * 1024)
        net_recv_mb = net.bytes_recv / (1024 * 1024)
        uptime_sec = int(time.time() - config.SERVER_START_TIME)
        uptime_hours = uptime_sec // 3600
        uptime_mins = (uptime_sec % 3600) // 60

        now_ts = time.time()
        if now_ts - config._ai_total_cache["ts"] > 60:
            try:
                config._ai_total_cache["value"] = sheets.get_total_used_from_sheets()
                config._ai_total_cache["ts"] = now_ts
            except Exception:
                pass
        ai_remaining = max(0, config.AI_MAX_REQUESTS - config._ai_total_cache["value"])

        return jsonify(
            {
                "status": "ok",
                "cpu_percent": cpu_percent,
                "ram_percent": ram_percent,
                "ram_used_mb": round(ram_used_mb, 1),
                "ram_total_mb": round(ram_total_mb, 1),
                "net_sent_mb": round(net_sent_mb, 2),
                "net_recv_mb": round(net_recv_mb, 2),
                "uptime": f"{uptime_hours}s {uptime_mins}dk",
                "ai_remaining": ai_remaining,
                "ai_total": config.AI_MAX_REQUESTS,
            }
        )
    except Exception as e:
        logger.error(f"❌ Sistem stats hatası: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/export-csv")
def export_csv():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    headers, rows = get_cached_data(uid)

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        yield output.getvalue()
        for row in rows:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(row)
            yield output.getvalue()

    return Response(
        stream_with_context(generate()),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=spotify_verilerim.csv"},
    )


@bp.route("/api/export-spotify-json")
def export_spotify_json():
    """
    Spotify'ın kendi extended history formatını taklit eden JSON export.
    Format: [{endTime, artistName, trackName, msPlayed}, ...]
    """
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401

    headers, rows = get_cached_data(uid)
    if not rows:
        return jsonify([])

    # Sütun indexleri
    def idx(col):
        try:
            return headers.index(col)
        except ValueError:
            return -1

    i_iso    = idx("_played_at_iso")
    i_artist = idx("Sanatçı")
    i_track  = idx("Şarkı Adı")
    i_ms     = idx("Süre (ms)")

    result = []
    for row in rows:
        end_time   = (row[i_iso] or "").strip()    if i_iso    != -1 and len(row) > i_iso    else ""
        artist     = (row[i_artist] or "").strip() if i_artist != -1 and len(row) > i_artist else ""
        track      = (row[i_track] or "").strip()  if i_track  != -1 and len(row) > i_track  else ""
        ms_raw     = (row[i_ms] or "").strip()     if i_ms     != -1 and len(row) > i_ms     else "0"
        try:
            ms = int(ms_raw)
        except ValueError:
            ms = 0

        # Spotify formatına çevir: "YYYY-MM-DD HH:MM"
        formatted_end = end_time[:16].replace("T", " ") if end_time else ""

        result.append({
            "endTime":    formatted_end,
            "artistName": artist,
            "trackName":  track,
            "msPlayed":   ms,
        })

    json_bytes = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        json_bytes,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=spotify_verilerim_spotify.json"},
    )


@bp.route("/api/export-json")
def export_json():
    """
    MusicPlus tam veri formatında JSON export.
    Format: [{EndTime, trackID, trackName, artistName, artistID, AlbumName, msPlayed, PlayedISO}, ...]
    """
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401

    headers, rows = get_cached_data(uid)
    if not rows:
        return jsonify([])

    def idx(col):
        try:
            return headers.index(col)
        except ValueError:
            return -1

    i_tarih   = idx("Dinlenme Tarihi")
    i_sid     = idx("Şarkı ID")
    i_track   = idx("Şarkı Adı")
    i_artist  = idx("Sanatçı")
    i_aid     = idx("Sanatçı ID")
    i_album   = idx("Albüm") if idx("Albüm") != -1 else idx("Album")
    i_ms      = idx("Süre (ms)")
    i_iso     = idx("_played_at_iso")

    result = []
    for row in rows:
        def safe(i):
            return (row[i] or "").strip() if i != -1 and len(row) > i else ""

        ms_raw = safe(i_ms)
        try:
            ms = int(ms_raw)
        except ValueError:
            ms = 0

        result.append({
            "EndTime":    safe(i_tarih),
            "trackID":    safe(i_sid),
            "trackName":  safe(i_track),
            "artistName": safe(i_artist),
            "artistID":   safe(i_aid),
            "AlbumName":  safe(i_album),
            "msPlayed":   ms,
            "PlayedISO":  safe(i_iso),
        })

    json_bytes = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        json_bytes,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=spotify_verilerim.json"},
    )


@bp.route("/api/sync", methods=["GET", "POST"])
@bp.route("/sync", methods=["GET", "POST"])
def manual_sync():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"status": "error", "message": "Giriş yapılmamış"}), 401
    sync_job(uid)
    return jsonify({"status": "ok", "message": "Manuel sync tamamlandı", "son_sync": config._last_sync, "refreshed": True})



@bp.route("/api/migrate-schema", methods=["GET", "POST"])
def migrate_schema():
    """
    Kullanıcının Sheets sayfasını yeni formata getirir.
    Eski: [Tarih, ŞarkıID, ŞarkıAdı, Sanatçı, SanatçıID, Albüm, Süre(ms), Süre(sn), ISO]
    Yeni: [Tarih, ŞarkıID, ŞarkıAdı, Sanatçı, SanatçıID, Albüm, Süre(ms), ISO, Tür]
    Arkaplanda çalışır; fetch_genres=true ise sanatçı türlerini de doldurur.
    """
    import threading

    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401

    fetch_genres = request.args.get("fetch_genres", "false").lower() == "true"

    def _worker():
        try:
            genre_map = {}
            if fetch_genres:
                # GenreCache'ten mevcut türleri al
                genre_map = sheets.get_cached_genres()
                # Kullanıcı sayfasındaki sanatçı ID'lerini kontrol et
                artist_id_map = sheets.get_artist_ids_from_user_sheet(uid)
                missing = [aid for aid in artist_id_map.values()
                           if aid and aid not in genre_map]
                if missing:
                    from extensions import spotify as sp
                    new_genres = sp.get_artists_genres(list(set(missing)))
                    if new_genres:
                        sheets.save_genres_batch(new_genres)
                        genre_map.update(new_genres)

            result = sheets.migrate_user_sheet(uid, genre_map=genre_map if fetch_genres else None)
            logger.info(f"✅ Schema migration ({uid}): {result}")

            # Cache'i yenile
            from extensions import load_user_data
            load_user_data(uid)
        except Exception as e:
            logger.error(f"❌ Migration worker hatası ({uid}): {e}")

    t = threading.Thread(target=_worker, daemon=True, name=f"migrate-{uid}")
    t.start()

    return jsonify({
        "status": "started",
        "message": "Migration arkaplanda başlatıldı. Birkaç dakika içinde tamamlanır.",
        "fetch_genres": fetch_genres,
    })


@bp.route("/api/sync-status")
def sync_status():
    """Hafif endpoint — frontend polling için. Son sync zamanını döndürür."""
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"son_sync": config._last_sync})


@bp.route("/api/health")
@bp.route("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "cached_rows": len(config._cached_rows),
            "son_sync": config._last_sync,
        }
    )

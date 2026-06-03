"""
Sistem, sağlık ve yardımcı API'ler.
- GET  /api/system-stats
- GET  /api/export-csv
- GET  /api/sync   (ve /sync)
- GET  /api/health (ve /health)
"""

import io
import csv
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

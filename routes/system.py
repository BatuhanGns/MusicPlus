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
from utils.helpers import get_uptimerobot_data

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
                "uptimerobot": get_uptimerobot_data(),
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


@bp.route("/api/sync")
@bp.route("/sync")
def manual_sync():
    sync_job()
    return jsonify({"status": "ok", "message": "Manuel sync tamamlandı", "son_sync": config._last_sync})


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

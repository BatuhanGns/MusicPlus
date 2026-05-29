"""
Spotify StreamingHistory.json içeri aktarma.
Desteklenen format (2026+):
  [{"endTime": "2026-05-28 14:23", "artistName": "...", "trackName": "...", "msPlayed": 337000}, ...]
"""

import json
import threading
import time
import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session

from extensions import sheets
from clients.spotify_client import SpotifyClient

logger = logging.getLogger(__name__)

bp = Blueprint("import_history", __name__)

# {user_id: {total, current, done, error, cancelled, imported}}
_progress: dict = {}


# ── Yardımcılar ──────────────────────────────────────────────────────────────

def _ts_to_iso(end_time: str) -> str:
    """'2026-05-28 14:23'  →  '2026-05-28T14:23:00.000Z'"""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            dt = datetime.strptime(end_time, fmt).replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            continue
    return end_time


def _search_album(client: SpotifyClient, track_name: str, artist_name: str) -> str:
    """Spotify search ile albüm adı bul (limit=1, yeni API max 10 uyumlu)."""
    try:
        q    = f"track:{track_name} artist:{artist_name}"
        data = client._req("GET", "/search",
                           params={"q": q, "type": "track", "limit": 1})
        items = data.get("tracks", {}).get("items", [])
        if items:
            return items[0].get("album", {}).get("name", "—")
    except Exception as e:
        logger.warning(f"Albüm arama hatası '{track_name}': {e}")
    return "—"


def _parse_tracks(raw: list, skip_short: bool) -> list:
    """Ham JSON listesini Sheets formatına çevirir."""
    result = []
    for item in raw:
        ms = item.get("msPlayed", 0)
        if skip_short and ms < 30_000:
            continue
        # Podcast / episode kayıtlarını atla
        if not item.get("trackName") or not item.get("artistName"):
            continue
        result.append({
            "played_at":   _ts_to_iso(item.get("endTime", "")),
            "track_id":    "",          # StreamingHistory.json'da bulunmuyor
            "track_name":  item["trackName"],
            "artist_name": item["artistName"],
            "album_name":  "—",        # sonradan doldurulacak veya "—" kalacak
            "duration_ms": ms,
            "duration_sec": round(ms / 1000),
        })
    return result


# ── Background Worker ─────────────────────────────────────────────────────────

def _import_worker(uid: str, refresh_token: str, tracks: list):
    """
    Her şarkı için Spotify search yaparak albüm adını bulur,
    bulunanları toplu olarak Sheets'e yazar.
    İlerleme _progress[uid] dict'inde tutulur.
    """
    total = len(tracks)
    _progress[uid].update({"total": total, "current": 0, "done": False, "error": None})

    try:
        client    = SpotifyClient(refresh_token=refresh_token)
        processed = []

        for i, t in enumerate(tracks):
            if _progress[uid].get("cancelled"):
                logger.info(f"Import iptal edildi ({uid})")
                break

            t["album_name"] = _search_album(client, t["track_name"], t["artist_name"])
            processed.append(t)
            _progress[uid]["current"] = i + 1
            time.sleep(0.12)   # Spotify rate limit marjı

        if processed:
            imported, _ = sheets.append_tracks(uid, processed)
            _progress[uid]["imported"] = imported

        _progress[uid]["done"] = True
        logger.info(f"✅ Import tamamlandı ({uid}): {_progress[uid].get('imported',0)} yeni kayıt")

    except Exception as e:
        logger.error(f"❌ Import worker hatası ({uid}): {e}")
        _progress[uid]["error"] = str(e)
        _progress[uid]["done"]  = True


# ── Endpoint'ler ──────────────────────────────────────────────────────────────

@bp.route("/api/import-history", methods=["POST"])
def import_history():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401

    # Devam eden import varsa engelle
    prog = _progress.get(uid, {})
    if prog and not prog.get("done"):
        return jsonify({"error": "Zaten bir import devam ediyor"}), 409

    body = request.get_json(silent=True) or {}
    raw_tracks    = body.get("tracks", [])
    skip_short    = body.get("skip_short", True)
    fetch_albums  = body.get("fetch_albums", False)

    if not raw_tracks:
        return jsonify({"error": "Hiç şarkı bulunamadı"}), 400

    tracks = _parse_tracks(raw_tracks, skip_short)

    if not tracks:
        return jsonify({"error": "Filtre sonrası eklenecek şarkı kalmadı"}), 400

    if fetch_albums:
        # Albüm arama: arka planda çalıştır
        refresh_token = session.get("refresh_token", "")
        if not refresh_token:
            return jsonify({"error": "Oturum süresi dolmuş, yeniden giriş yapın"}), 401

        _progress[uid] = {"total": len(tracks), "current": 0,
                          "done": False, "error": None, "imported": 0, "cancelled": False}

        t = threading.Thread(
            target=_import_worker,
            args=(uid, refresh_token, tracks),
            daemon=True,
            name=f"import-{uid}",
        )
        t.start()
        return jsonify({"status": "started", "total": len(tracks)})

    else:
        # Albüm arama yok → direkt yaz (hızlı)
        new_count, _ = sheets.append_tracks(uid, tracks)
        return jsonify({"status": "done", "imported": new_count, "total": len(tracks)})


@bp.route("/api/import-progress")
def import_progress():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401

    p = _progress.get(uid)
    if not p:
        return jsonify({"status": "idle"})

    current  = p.get("current", 0)
    total    = p.get("total", 1)
    done     = p.get("done", False)
    # Tahmini süre: ~0.15s/şarkı (API isteği + 0.12s uyku)
    eta_sec  = round((total - current) * 0.15)

    return jsonify({
        "status":      "done" if done else "running",
        "total":       total,
        "current":     current,
        "imported":    p.get("imported", 0),
        "error":       p.get("error"),
        "eta_seconds": eta_sec,
        "cancelled":   p.get("cancelled", False),
    })


@bp.route("/api/import-cancel", methods=["POST"])
def import_cancel():
    uid = session.get("user_id")
    if uid and uid in _progress:
        _progress[uid]["cancelled"] = True
    return jsonify({"status": "cancelled"})

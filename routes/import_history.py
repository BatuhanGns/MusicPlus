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


def _search_track_info(client: SpotifyClient, track_name: str, artist_name: str) -> dict:
    """Spotify search ile şarkı bilgilerini bul: albüm adı, track_id, artist_ids."""
    try:
        q    = f"track:{track_name} artist:{artist_name}"
        data = client._req("GET", "/search",
                           params={"q": q, "type": "track", "limit": 1})
        items = data.get("tracks", {}).get("items", [])
        if items:
            item = items[0]
            artists = item.get("artists", [])
            return {
                "album_name": item.get("album", {}).get("name", "—"),
                "track_id":   item.get("id", ""),
                "artist_ids": ",".join(a["id"] for a in artists if a.get("id")),
            }
    except Exception as e:
        logger.warning(f"Şarkı arama hatası '{track_name}': {e}")
    return {"album_name": "—", "track_id": "", "artist_ids": ""}


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
            "artist_ids":  "",          # search sonrası doldurulacak
            "album_name":  "—",        # search sonrası doldurulacak
            "duration_ms": ms,
            "duration_sec": round(ms / 1000),
            "genre":       "",          # fetch_genres=True ise doldurulacak
        })
    return result


# ── Background Worker ─────────────────────────────────────────────────────────

def _import_worker(uid: str, refresh_token: str, tracks: list, fetch_genres: bool = False):
    """
    Her şarkı için Spotify search yaparak albüm adı, track_id ve artist_ids bulur.
    fetch_genres=True ise ayrıca sanatçı türlerini de çeker ve Tür sütununa yazar.
    İlerleme _progress[uid] dict'inde tutulur.
    """
    total = len(tracks)
    _progress[uid].update({
        "total": total, "current": 0, "done": False,
        "error": None, "phase": "search"
    })

    try:
        client    = SpotifyClient(refresh_token=refresh_token)
        processed = []

        # Faz 1: Her şarkı için search (albüm + track_id + artist_ids)
        for i, t in enumerate(tracks):
            if _progress[uid].get("cancelled"):
                logger.info(f"Import iptal edildi ({uid})")
                break

            info = _search_track_info(client, t["track_name"], t["artist_name"])
            t["album_name"] = info["album_name"]
            t["track_id"]   = info["track_id"]
            t["artist_ids"] = info["artist_ids"]
            processed.append(t)
            _progress[uid]["current"] = i + 1
            time.sleep(0.12)   # Spotify rate limit marjı

        # Faz 2: Genre çekme (isteğe bağlı)
        if fetch_genres and processed and not _progress[uid].get("cancelled"):
            _progress[uid]["phase"] = "genres"

            # Tüm benzersiz artist_id'leri topla
            all_ids = list({
                aid.strip()
                for t in processed
                for aid in t.get("artist_ids", "").split(",")
                if aid.strip()
            })

            # GenreCache'ten mevcut türleri al, eksikleri API'den çek
            genre_map = sheets.get_cached_genres()
            missing   = [aid for aid in all_ids if aid not in genre_map]

            if missing:
                new_genres = client.get_artists_genres(missing)
                if new_genres:
                    sheets.save_genres_batch(new_genres)
                    genre_map.update(new_genres)

            # Her şarkıya primary genre ata (ilk sanatçının ilk türü)
            for t in processed:
                for aid in [a.strip() for a in t.get("artist_ids", "").split(",") if a.strip()]:
                    genres = genre_map.get(aid, [])
                    if genres:
                        t["genre"] = ", ".join(genres[:3])
                        break

        if processed:
            imported, _ = sheets.append_tracks(uid, processed)
            _progress[uid]["imported"] = imported

        _progress[uid]["done"]  = True
        _progress[uid]["phase"] = "done"
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
    fetch_genres  = body.get("fetch_genres", False)

    # Genre çekmek için albüm araması da gerekli (track_id + artist_ids lazım)
    if fetch_genres:
        fetch_albums = True

    if not raw_tracks:
        return jsonify({"error": "Hiç şarkı bulunamadı"}), 400

    tracks = _parse_tracks(raw_tracks, skip_short)

    if not tracks:
        return jsonify({"error": "Filtre sonrası eklenecek şarkı kalmadı"}), 400

    if fetch_albums:
        # Arka planda çalıştır (albüm + isteğe bağlı genre arama)
        refresh_token = session.get("refresh_token", "")
        if not refresh_token:
            return jsonify({"error": "Oturum süresi dolmuş, yeniden giriş yapın"}), 401

        _progress[uid] = {
            "total": len(tracks), "current": 0,
            "done": False, "error": None, "imported": 0,
            "cancelled": False, "phase": "search",
            "fetch_genres": fetch_genres,
        }

        t = threading.Thread(
            target=_import_worker,
            args=(uid, refresh_token, tracks, fetch_genres),
            daemon=True,
            name=f"import-{uid}",
        )
        t.start()
        return jsonify({"status": "started", "total": len(tracks), "fetch_genres": fetch_genres})

    else:
        # Hızlı yol: Spotify araması yok, direkt yaz
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

    phase = p.get("phase", "search")
    phase_label = {
        "search": "Albüm ve şarkı bilgileri aranıyor",
        "genres": "Tür bilgileri çekiliyor",
        "done":   "Tamamlandı",
    }.get(phase, "İşleniyor")

    return jsonify({
        "status":      "done" if done else "running",
        "total":       total,
        "current":     current,
        "imported":    p.get("imported", 0),
        "error":       p.get("error"),
        "eta_seconds": eta_sec,
        "cancelled":   p.get("cancelled", False),
        "phase":       phase,
        "phase_label": phase_label,
    })


@bp.route("/api/import-cancel", methods=["POST"])
def import_cancel():
    uid = session.get("user_id")
    if uid and uid in _progress:
        _progress[uid]["cancelled"] = True
    return jsonify({"status": "cancelled"})

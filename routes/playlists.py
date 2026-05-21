"""
Playlist yönetimi API'leri.
- POST /api/playlist/create-top-tracks
- POST /api/playlist/create-top-artists
- POST /api/playlist/<id>/shuffle
- POST /api/playlist/<id>/follow-artists
- POST /api/playlist/<id>/unfollow-artists
- POST /api/playlist/<id>/like-all
- POST /api/playlist/<id>/unlike-all
- POST /api/playlist/<id>/remove-liked
- POST /api/playlist/<id>/remove-unliked
"""

import logging
from collections import Counter, defaultdict
from flask import Blueprint, jsonify, request

import config
from extensions import get_current_user_id, get_cached_data, spotify
from utils.helpers import _extract_track_id

logger = logging.getLogger(__name__)
bp = Blueprint("playlists", __name__)


@bp.route("/api/playlist/create-top-tracks", methods=["POST"])
def api_create_top_tracks_playlist():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki = headers.index("Şarkı Adı")
        idx_sarki_id = headers.index("Şarkı ID")

        sarki_id_map = {}
        sarki_counts = Counter()
        for row in rows:
            if len(row) > max(idx_sarki, idx_sarki_id):
                sarki = row[idx_sarki].strip()
                sid = row[idx_sarki_id].strip()
                if sarki and sid:
                    sarki_counts[sarki] += 1
                    sarki_id_map[sarki] = sid

        top_sarkilar_ids = []
        for s, _ in sarki_counts.most_common(50):
            if s not in sarki_id_map:
                continue
            tid = _extract_track_id(sarki_id_map[s])
            if tid:
                top_sarkilar_ids.append(f"spotify:track:{tid}")
            else:
                logger.warning(f"⚠️ Geçersiz şarkı ID'si atlandı: {sarki_id_map[s]!r}")

        logger.info(f"📋 Top şarkılar ID listesi (ilk 3): {top_sarkilar_ids[:3]}")
        if not top_sarkilar_ids:
            return jsonify({"error": "Playlist oluşturmak için yeterli şarkı verisi bulunamadı."}), 400

        pl = spotify._req("POST", "/me/playlists", json={
            "name": "En Çok Dinlediklerim",
            "public": False,
            "description": "Music+ Tarafından Oluşturulmuştur"
        })
        playlist_id = pl["id"]
        for i in range(0, len(top_sarkilar_ids), 100):
            chunk = top_sarkilar_ids[i:i + 100]
            if chunk:
                spotify._req("POST", f"/playlists/{playlist_id}/items", json={"uris": chunk})

        return jsonify({"status": "ok", "playlist_id": playlist_id, "track_count": len(top_sarkilar_ids)})
    except Exception as e:
        logger.error(f"❌ Playlist oluşturma hatası: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/playlist/create-top-artists", methods=["POST"])
def api_create_top_artists_playlist():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sanatci = headers.index("Sanatçı")
        idx_sarki = headers.index("Şarkı Adı")
        idx_sarki_id = headers.index("Şarkı ID")

        sanatci_counts = Counter()
        sanatci_sarkilar = defaultdict(dict)

        for row in rows:
            if len(row) > max(idx_sanatci, idx_sarki, idx_sarki_id):
                s = row[idx_sanatci].strip()
                t = row[idx_sarki].strip()
                raw_tid = row[idx_sarki_id].strip()
                if s:
                    sanatci_counts[s] += 1
                if s and t and raw_tid:
                    clean_tid = _extract_track_id(raw_tid)
                    if clean_tid:
                        sanatci_sarkilar[s][t] = clean_tid

        top_sanatcilar = [s for s, _ in sanatci_counts.most_common(20) if s]
        seen_ids = set()
        track_uris = []
        for s in top_sanatcilar:
            for tid in list(sanatci_sarkilar[s].values())[:5]:
                if tid not in seen_ids:
                    seen_ids.add(tid)
                    track_uris.append(f"spotify:track:{tid}")

        track_uris = track_uris[:50]
        logger.info(f"📋 Sanatçı playlist URI örnekleri (ilk 3): {track_uris[:3]}")

        if not track_uris:
            return jsonify({"error": "Playlist oluşturmak için yeterli şarkı verisi bulunamadı."}), 400

        pl = spotify._req("POST", "/me/playlists", json={
            "name": "En Çok Dinlediğim Sanatçılar",
            "public": False,
            "description": "Music+ Tarafından Oluşturulmuştur"
        })
        playlist_id = pl["id"]
        for i in range(0, len(track_uris), 100):
            chunk = track_uris[i:i + 100]
            if chunk:
                spotify._req("POST", f"/playlists/{playlist_id}/items", json={"uris": chunk})

        return jsonify({"status": "ok", "playlist_id": playlist_id, "track_count": len(track_uris)})
    except Exception as e:
        logger.error(f"❌ Sanatçı playlist hatası: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/playlist/<playlist_id>/shuffle", methods=["POST"])
def api_playlist_shuffle(playlist_id):
    try:
        spotify.shuffle_playlist(playlist_id)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/playlist/<playlist_id>/follow-artists", methods=["POST"])
def api_follow_artists(playlist_id):
    try:
        count = spotify.follow_all_artists_in_playlist(playlist_id)
        return jsonify({"status": "ok", "followed": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/playlist/<playlist_id>/unfollow-artists", methods=["POST"])
def api_unfollow_artists(playlist_id):
    try:
        count = spotify.unfollow_all_artists_in_playlist(playlist_id)
        return jsonify({"status": "ok", "unfollowed": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/playlist/<playlist_id>/like-all", methods=["POST"])
def api_like_all(playlist_id):
    try:
        count = spotify.like_all_tracks_in_playlist(playlist_id)
        return jsonify({"status": "ok", "liked": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/playlist/<playlist_id>/unlike-all", methods=["POST"])
def api_unlike_all(playlist_id):
    try:
        count = spotify.unlike_all_tracks_in_playlist(playlist_id)
        return jsonify({"status": "ok", "unliked": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/playlist/<playlist_id>/remove-liked", methods=["POST"])
def api_remove_liked(playlist_id):
    try:
        count = spotify.remove_liked_tracks_from_playlist(playlist_id)
        return jsonify({"status": "ok", "removed": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/playlist/<playlist_id>/remove-unliked", methods=["POST"])
def api_remove_unliked(playlist_id):
    try:
        count = spotify.remove_unliked_tracks_from_playlist(playlist_id)
        return jsonify({"status": "ok", "removed": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

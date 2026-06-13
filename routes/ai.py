"""
AI (Gemini) modülü API'leri.
- POST /api/ai/chat
- GET  /api/ai/history
- DELETE /api/ai/history
- GET  /api/ai/search-track
- POST /api/ai/create-playlist
- POST /api/ai/add-to-playlist
- POST /api/ai/edit-playlist
- GET  /api/ai/limits
"""

import json as _json
import logging
import threading
from flask import Blueprint, Response, request, jsonify, stream_with_context

import config
from extensions import get_current_user_id, get_current_user_name, get_cached_data, spotify, gemini, sheets
from utils.helpers import compute_stats

logger = logging.getLogger(__name__)
bp = Blueprint("ai", __name__)


# Kullanıcı başına AI geçmişi kilitleri (thread-safe eş zamanlı istek koruması)
_ai_history_locks: dict = {}
_ai_history_locks_meta = threading.Lock()

def _get_ai_lock(uid: str) -> threading.Lock:
    with _ai_history_locks_meta:
        if uid not in _ai_history_locks:
            _ai_history_locks[uid] = threading.Lock()
        return _ai_history_locks[uid]


@bp.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401

    body = request.get_json(silent=True) or {}
    user_message = (body.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Mesaj boş"}), 400

    # Thread-safe geçmiş kopyası al
    lock = _get_ai_lock(uid)
    with lock:
        history = list(config._ai_history.get(uid, []))  # Shallow copy — race condition önlenir
        history.append({"role": "user", "content": user_message})

    # Spotify context topla
    now_playing = None
    recent_tracks = None
    try:
        now_playing = spotify.get_now_playing()
    except Exception:
        pass
    try:
        recent_tracks = spotify.get_recently_played(10)
    except Exception:
        pass

    # Tam istatistik context'i
    full_stats_context = ""
    try:
        headers, rows = get_cached_data(uid)
        if rows:
            stats = compute_stats(headers, rows)
            if stats:
                top_sanatcilar_str = "\n".join(
                    f"  {i+1}. {s['sanatci']} — {s['count']} dinlenme"
                    for i, s in enumerate(stats["top_sanatcilar"])
                )
                top_sarkilar_str = "\n".join(
                    f"  {i+1}. {s['sarki']} — {s['sanatci']} ({s['count']} kez)"
                    for i, s in enumerate(stats["top_sarkilar"])
                )
                aylar_str = "\n".join(
                    f"  {a['ay']}: {a['kayit_sayisi']} dinlenme, {a['toplam']}"
                    for a in stats["aylar"]
                )
                full_stats_context = (
                    f"\n\nKULLANICININ TAM İSTATİSTİKLERİ:\n"
                    f"Toplam kayıt: {stats['toplam_kayit']}, Farklı şarkı: {stats['farkli_sarki']}, Farklı sanatçı: {stats['farkli_sanatci']}\n"
                    f"İlk kayıt: {stats['ilk_kayit_tarihi']}\n\n"
                    f"En çok dinlenen sanatçılar (Top 10):\n{top_sanatcilar_str}\n\n"
                    f"En çok dinlenen şarkılar (Top 10):\n{top_sarkilar_str}\n\n"
                    f"Aylık dinleme geçmişi:\n{aylar_str}"
                )
    except Exception:
        pass

    spotify_context = gemini.build_spotify_context(now_playing, recent_tracks) + full_stats_context

    # Playlist context'i
    playlist_context = ""
    try:
        playlists = spotify.get_playlists()
        if playlists:
            pl_lines = "\n".join(
                f"  - \"{p['name']}\" → ID: {p['id']} ({p['track_count']} şarkı)"
                for p in playlists
            )
            playlist_context = f"\n\nKULLANICININ SPOTİFY PLAYLİSTLERİ (düzenleme için MUTLAKA bu ID'leri kullan):\n{pl_lines}"
    except Exception:
        pass

    spotify_context += playlist_context

    def generate():
        full_response = ""
        request_successful = False
        used_model = ""

        try:
            for raw in gemini.stream_chat(history, spotify_context):
                chunk = _json.loads(raw)
                if chunk.get("type") == "text":
                    full_response += chunk["text"]
                elif chunk.get("type") == "done":
                    request_successful = True
                    used_model = chunk.get("model", "")
                yield f"data: {raw}\n\n"
        except Exception as e:
            logger.error(f"❌ AI stream hatası: {e}")
            yield f"data: {_json.dumps({'type': 'error', 'text': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            if full_response:
                history.append({"role": "assistant", "content": full_response})
            trimmed = history[-config.AI_MAX_HISTORY:]
            # Thread-safe yaz
            with _get_ai_lock(uid):
                config._ai_history[uid] = trimmed

            if request_successful:
                config.ai_requests_used += 1
                config._ai_total_cache["ts"] = 0
                try:
                    display_name = get_current_user_name()
                    model_label = used_model if used_model else "gemma-4"
                    sheets.log_ai_request(uid, display_name, model_label)
                except Exception as log_err:
                    logger.warning(f"⚠️ Limits log hatası: {log_err}")

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@bp.route("/api/ai/history", methods=["GET"])
def api_ai_get_history():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    return jsonify({"history": config._ai_history.get(uid, [])})


@bp.route("/api/ai/history", methods=["DELETE"])
def api_ai_clear_history():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    config._ai_history.pop(uid, None)
    return jsonify({"status": "ok"})


@bp.route("/api/ai/search-track")
def api_ai_search_track():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Sorgu boş"}), 400
    try:
        tid = spotify._search_track(q)
        if tid:
            return jsonify({"id": tid, "uri": f"spotify:track:{tid}"})
        return jsonify({"id": None, "error": "Bulunamadı"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/ai/create-playlist", methods=["POST"])
def api_ai_create_playlist():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "AI Playlist").strip()
    track_names = body.get("tracks") or []
    try:
        playlist_id = spotify.create_playlist_from_track_names(
            name, track_names, description="Music+ Tarafından Oluşturulmuştur"
        )
        return jsonify({"status": "ok", "playlist_id": playlist_id, "track_count": len(track_names)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/ai/add-to-playlist", methods=["POST"])
def api_ai_add_to_playlist():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    body = request.get_json(silent=True) or {}
    playlist_id = (body.get("playlist_id") or "").strip()
    track_names = body.get("tracks") or []
    if not playlist_id:
        return jsonify({"error": "playlist_id gerekli"}), 400
    try:
        uris = []
        for t in track_names:
            tid = spotify._search_track(t)
            if tid:
                uris.append(f"spotify:track:{tid}")
        if uris:
            for i in range(0, len(uris), 100):
                spotify._req("POST", f"/playlists/{playlist_id}/items", json={"uris": uris[i:i + 100]})
        return jsonify({"status": "ok", "added": len(uris)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/ai/edit-playlist", methods=["POST"])
def api_ai_edit_playlist():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    body = request.get_json(silent=True) or {}
    playlist_id = (body.get("playlist_id") or "").strip()
    track_names = body.get("tracks") or []
    new_name = body.get("new_name")
    if not playlist_id:
        return jsonify({"error": "playlist_id gerekli"}), 400
    try:
        if new_name:
            spotify._req("PUT", f"/playlists/{playlist_id}", json={
                "name": new_name,
                "description": "Music+ Tarafından Düzenlenmiştir"
            })
        else:
            spotify._req("PUT", f"/playlists/{playlist_id}", json={
                "description": "Music+ Tarafından Düzenlenmiştir"
            })

        uris = []
        for t in track_names:
            tid = spotify._search_track(t)
            if tid:
                uris.append(f"spotify:track:{tid}")
        if uris:
            for i in range(0, len(uris), 100):
                spotify._req("POST", f"/playlists/{playlist_id}/items", json={"uris": uris[i:i + 100]})
        return jsonify({"status": "ok", "added": len(uris)})
    except Exception as e:
        logger.error(f"❌ Playlist düzenleme hatası: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/ai/limits")
def api_ai_limits():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401

    summary = sheets.get_limits_summary()

    user_totals = {}
    for row in summary:
        u = row.get("user_id", "")
        dn = row.get("display_name", u)
        m = row.get("model", "")
        today = int(row.get("today_used", 0)) if str(row.get("today_used", "0")).isdigit() else 0
        total = int(row.get("total_used", 0)) if str(row.get("total_used", "0")).isdigit() else 0
        if total == 0 and str(row.get("requests_used", "0")).isdigit():
            total = int(row.get("requests_used", 0))
            today = total
        lst = row.get("last_used", "")
        if u not in user_totals:
            user_totals[u] = {"user_id": u, "display_name": dn, "today": 0, "total": 0, "models": [], "last_used": lst}
        user_totals[u]["today"] += today
        user_totals[u]["total"] += total
        user_totals[u]["models"].append({"model": m, "today": today, "total": total, "last_used": lst})
        if lst > user_totals[u]["last_used"]:
            user_totals[u]["last_used"] = lst

    grand_total = sum(u["total"] for u in user_totals.values())

    return jsonify({
        "total_used": grand_total,
        "total_limit": config.AI_MAX_REQUESTS,
        "remaining": max(0, config.AI_MAX_REQUESTS - grand_total),
        "users": list(user_totals.values()),
    })


@bp.route("/api/ai/recommend-details", methods=["POST"])
def api_ai_recommend_details():
    """
    AI'in önerdiği şarkı listesi için Spotify'dan kapak + metadata çeker.
    Body: {"tracks": [{"track": "...", "artist": "..."}, ...]}
    Response: {"tracks": [{"track":..., "artist":..., "cover_url":..., "spotify_url":...}, ...]}
    """
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401

    body = request.get_json(silent=True) or {}
    tracks = body.get("tracks", [])
    if not tracks:
        return jsonify({"tracks": []})

    import requests as _req

    results = []
    try:
        token = spotify._get_access_token()
    except Exception:
        token = None

    for item in tracks[:10]:
        track_name  = (item.get("track") or "").strip()
        artist_name = (item.get("artist") or "").strip()
        if not track_name:
            continue

        result = {
            "track":       track_name,
            "artist":      artist_name,
            "cover_url":   None,
            "spotify_url": None,
            "track_id":    None,
        }

        if token:
            try:
                q = f"{track_name} {artist_name}".strip()
                resp = _req.get(
                    "https://api.spotify.com/v1/search",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"q": q, "type": "track", "limit": 1},
                    timeout=4,
                )
                if resp.status_code == 200:
                    items = resp.json().get("tracks", {}).get("items", [])
                    if items:
                        t = items[0]
                        images = t.get("album", {}).get("images", [])
                        result["cover_url"]   = images[0]["url"] if images else None
                        result["spotify_url"] = t.get("external_urls", {}).get("spotify")
                        result["track_id"]    = t.get("id")
                        # API'den gelen gerçek adları kullan
                        result["track"]  = t.get("name", track_name)
                        result["artist"] = ", ".join(a["name"] for a in t.get("artists", [])) or artist_name
            except Exception as e:
                logger.warning(f"Öneri detay hatası '{track_name}': {e}")

        results.append(result)

    return jsonify({"tracks": results})

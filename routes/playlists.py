"""
Playlist yönetimi API'leri.
- POST /api/playlist/create-top-tracks
- POST /api/playlist/create-top-artists
- POST /api/playlist/create          → gelişmiş (type, count, max_duration_min)
- POST /api/playlist/create-auto     → AI destekli otomatik güncellenen playlist
- POST /api/playlist/create-auto-collab → AI destekli otomatik güncellenen ortak playlist
- POST /api/playlist/create-collab   → ortak playlist (tek seferlik)
- POST /api/playlist/<id>/shuffle
- POST /api/playlist/<id>/follow-artists
- POST /api/playlist/<id>/unfollow-artists
- POST /api/playlist/<id>/like-all
- POST /api/playlist/<id>/unlike-all
- POST /api/playlist/<id>/remove-liked
- POST /api/playlist/<id>/remove-unliked
"""

import logging
import traceback
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request

import config
from extensions import get_current_user_id, get_cached_data, spotify, sheets
from utils.helpers import _extract_track_id

logger = logging.getLogger(__name__)
bp = Blueprint("playlists", __name__)


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _build_history_summary(headers, rows, max_rows=800) -> str:
    """AI'ye gönderilecek dinleme geçmişi özetini oluşturur."""
    idx_sarki   = next((i for i, h in enumerate(headers) if (h or "").strip() in ("Şarkı Adı", "Sarki Adi", "Track Name")), -1)
    idx_sanatci = next((i for i, h in enumerate(headers) if (h or "").strip() in ("Sanatçı", "Sanatci", "Artist")), -1)
    idx_sure    = next((i for i, h in enumerate(headers) if "Süre" in (h or "") or "Sure" in (h or "")), -1)
    idx_iso     = next((i for i, h in enumerate(headers) if (h or "").strip() in ("_played_at_iso", "played_at")), -1)

    if idx_sarki == -1 or idx_sanatci == -1:
        raise ValueError(f"Gerekli sütunlar bulunamadı. Mevcut başlıklar: {headers}")

    now = datetime.now(timezone.utc)
    bu_hafta = now - timedelta(days=7)

    sarki_counts  = Counter()
    sarki_sure    = {}
    hafta_counts  = Counter()
    sarki_sanatci = {}

    for row in rows[:max_rows]:
        if len(row) <= max(idx_sarki, idx_sanatci):
            continue
        try:
            sarki   = str(row[idx_sarki]   or "").strip()
            sanatci = str(row[idx_sanatci] or "").strip()
            sure_raw = row[idx_sure] if idx_sure != -1 and len(row) > idx_sure else None
            sure    = int(sure_raw) if sure_raw and str(sure_raw).isdigit() else 210000
            iso_raw = row[idx_iso] if idx_iso != -1 and len(row) > idx_iso else None
            iso     = str(iso_raw or "").strip()
        except Exception as row_err:
            logger.warning(f"_build_history_summary satır atlandı: {row_err} | row={row}")
            continue

        if sarki:
            sarki_counts[sarki]  += 1
            sarki_sure[sarki]     = sure
            sarki_sanatci[sarki]  = sanatci

        if sarki and iso and iso[:10] >= bu_hafta.strftime("%Y-%m-%d"):
            hafta_counts[sarki] += 1

    top_tum   = sarki_counts.most_common(30)
    top_hafta = hafta_counts.most_common(20)

    lines = ["=== TÜM ZAMANLAR TOP 30 ==="]
    for s, c in top_tum:
        san = sarki_sanatci.get(s, "?")
        dk  = sarki_sure.get(s, 0) // 60000
        lines.append(f"{s} - {san} ({c}x dinlendi, ~{dk}dk)")

    lines.append("\n=== BU HAFTA TOP 20 ===")
    for s, c in top_hafta:
        san = sarki_sanatci.get(s, "?")
        lines.append(f"{s} - {san} ({c}x)")

    return "\n".join(lines)


def _ai_playlist_generate(history_summary: str, max_dur_min: int, playlist_name: str) -> list[str]:
    """
    Gemini'ye dinleme geçmişini gönderir, şarkı listesi alır.
    Döndürür: ["Şarkı Adı - Sanatçı", ...]
    """
    from google import genai
    from google.genai import types

    api_key = config.GEMINI_API_KEY
    if not api_key:
        raise ValueError("GEMINI_API_KEY eksik")

    # Ortalama şarkı süresi ~3.5 dk varsayımıyla şarkı sayısını hesapla
    track_count = max(5, min(50, (max_dur_min * 60) // 210))

    prompt = f"""You are a playlist generator. Output ONLY a JSON array. No explanation, no markdown, no text before or after.

Task: Create a Spotify playlist based on this listening history.
Playlist name: "{playlist_name}"
Number of tracks: exactly {track_count} songs (targeting ~{max_dur_min} minutes total)

Rules:
- Output ONLY a valid JSON array, nothing else
- Format: ["Track Name - Artist", "Track Name 2 - Artist 2", ...]
- Every song must be real and findable on Spotify
- Base selections on the listening history below
- If "This Week Top 20" is empty, use All-Time Top 30 only

Listening history:
{history_summary}

JSON array:"""

    import re as _re

    def _extract_json_list(raw: str) -> list | None:
        """Ham metinden JSON listesi çıkarır."""
        text = raw.strip()
        if not text:
            return None
        # Önce thinking bloğunu at (<think>...</think>)
        text = _re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
        # ```json ... ``` veya ``` ... ``` bloğunu al
        m = _re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
        # Hâlâ [ ile başlamıyorsa liste bloğunu bul
        if not text.startswith("["):
            m = _re.search(r"\[[\s\S]*?\]", text)
            if m:
                text = m.group(0)
        try:
            result = json.loads(text)
            if isinstance(result, list) and result:
                return result
        except Exception:
            pass
        return None

    def _get_response_text(resp) -> str:
        """resp.text None geldiğinde candidates üzerinden metni okur."""
        try:
            if resp.text:
                return resp.text
        except Exception:
            pass
        try:
            for candidate in (resp.candidates or []):
                for part in (candidate.content.parts or []):
                    if hasattr(part, "text") and part.text:
                        return part.text
        except Exception:
            pass
        return ""

    client = genai.Client(api_key=api_key)
    models = ["gemma-4-31b-it", "gemma-4-26b-a4b-it"]

    for model in models:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=1500,
                ),
            )
            text = _get_response_text(resp)
            if not text:
                logger.warning(f"AI playlist model {model} boş yanıt, sonraki deneniyor")
                continue
            logger.debug(f"AI playlist model {model} ham yanıt (ilk 200): {text[:200]}")
            tracks = _extract_json_list(text)
            if tracks:
                return tracks
            logger.warning(f"AI playlist model {model} JSON listesi çıkarılamadı: {text[:200]}")
        except Exception as e:
            logger.warning(f"AI playlist model {model} hata: {e}")
            continue

    raise ValueError("AI'dan geçerli liste alınamadı")


def _ai_collab_playlist_generate(summaries: dict, max_dur_min: int, playlist_name: str) -> list[str]:
    """
    Birden fazla kullanıcının geçmişini alır, ortak zevkleri bulur.
    summaries: {user_id: history_summary_str}
    """
    from google import genai
    from google.genai import types

    api_key = config.GEMINI_API_KEY
    if not api_key:
        raise ValueError("GEMINI_API_KEY eksik")

    kullanici_bloklar = "\n\n".join(
        f"=== KULLANICI {i+1} ===\n{s}"
        for i, s in enumerate(summaries.values())
    )

    track_count = max(5, min(50, (max_dur_min * 60) // 210))

    prompt = f"""You are a playlist generator. Output ONLY a JSON array. No explanation, no markdown, no text before or after.

Task: Create a collaborative Spotify playlist for {len(summaries)} users based on their listening histories.
Playlist name: "{playlist_name}"
Number of tracks: exactly {track_count} songs (targeting ~{max_dur_min} minutes total)

Rules:
- Output ONLY a valid JSON array, nothing else
- Format: ["Track Name - Artist", "Track Name 2 - Artist 2", ...]
- Every song must be real and findable on Spotify
- Find common ground between all users' tastes

Listening histories:
{kullanici_bloklar}

JSON array:"""

    import re as _re

    def _extract_json_list_c(raw: str) -> list | None:
        text = raw.strip()
        if not text:
            return None
        text = _re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
        m = _re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
        if not text.startswith("["):
            m = _re.search(r"\[[\s\S]*?\]", text)
            if m:
                text = m.group(0)
        try:
            result = json.loads(text)
            if isinstance(result, list) and result:
                return result
        except Exception:
            pass
        return None

    def _get_resp_text_c(resp) -> str:
        try:
            if resp.text:
                return resp.text
        except Exception:
            pass
        try:
            for candidate in (resp.candidates or []):
                for part in (candidate.content.parts or []):
                    if hasattr(part, "text") and part.text:
                        return part.text
        except Exception:
            pass
        return ""

    client = genai.Client(api_key=api_key)
    models = ["gemma-4-31b-it", "gemma-4-26b-a4b-it"]

    for model in models:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=2000,
                ),
            )
            text = _get_resp_text_c(resp)
            if not text:
                logger.warning(f"AI collab model {model} boş yanıt, sonraki deneniyor")
                continue
            logger.debug(f"AI collab model {model} ham yanıt (ilk 200): {text[:200]}")
            tracks = _extract_json_list_c(text)
            if tracks:
                return tracks
            logger.warning(f"AI collab model {model} JSON listesi çıkarılamadı: {text[:200]}")
        except Exception as e:
            logger.warning(f"AI collab model {model} hata: {e}")
            continue

    raise ValueError("AI'dan geçerli liste alınamadı")


def _resolve_tracks_to_uris(track_list: list[str], max_dur_ms: int | None) -> tuple[list, int]:
    """
    ["Şarkı - Sanatçı", ...] listesini Spotify URI'lerine çevirir.
    Döndürür: (uri_listesi, toplam_ms)
    """
    uris     = []
    total_ms = 0

    for item in track_list:
        if max_dur_ms and total_ms >= max_dur_ms:
            break
        try:
            if " - " in item:
                track_name, artist_name = item.rsplit(" - ", 1)
            else:
                track_name, artist_name = item, ""

            q = f"track:{track_name.strip()}"
            if artist_name:
                q += f" artist:{artist_name.strip()}"

            result = spotify._req("GET", f"/search?q={q}&type=track&limit=1&market=TR")
            items  = result.get("tracks", {}).get("items", [])
            if not items:
                continue

            t        = items[0]
            dur_ms   = t.get("duration_ms", 210000)
            track_id = t["id"]

            if max_dur_ms and (total_ms + dur_ms) > max_dur_ms:
                continue

            uris.append(f"spotify:track:{track_id}")
            total_ms += dur_ms

        except Exception as e:
            logger.warning(f"Track resolve hata ({item}): {e}")
            continue

    return uris, total_ms


def _create_or_update_spotify_playlist(
    playlist_id: str | None,
    name: str,
    description: str,
    track_uris: list,
    public: bool = False
) -> str:
    """
    playlist_id varsa günceller (tüm şarkıları değiştirir),
    yoksa yeni oluşturur. Playlist ID döndürür.
    """
    if playlist_id:
        # Mevcut şarkıları temizle
        try:
            existing = spotify._req("GET", f"/playlists/{playlist_id}/tracks?limit=100")
            existing_uris = [
                {"uri": t["track"]["uri"]}
                for t in existing.get("items", [])
                if t.get("track")
            ]
            if existing_uris:
                spotify._req("DELETE", f"/playlists/{playlist_id}/tracks",
                             json={"tracks": existing_uris})
        except Exception as e:
            logger.warning(f"Playlist temizleme hata: {e}")

        # Açıklamayı güncelle
        try:
            spotify._req("PUT", f"/playlists/{playlist_id}",
                         json={"name": name, "description": description})
        except Exception as e:
            logger.warning(f"Playlist meta güncelleme hata: {e}")
    else:
        # Yeni playlist oluştur
        pl = spotify._req("POST", "/me/playlists", json={
            "name": name,
            "public": public,
            "description": description,
        })
        playlist_id = pl["id"]

    # Şarkıları ekle (100'er chunk)
    for i in range(0, len(track_uris), 100):
        chunk = track_uris[i:i + 100]
        if chunk:
            spotify._req("POST", f"/playlists/{playlist_id}/items", json={"uris": chunk})

    return playlist_id


# ── Otomatik Güncellenen Playlist (Kişisel) ───────────────────────────────────

@bp.route("/api/playlist/create-auto", methods=["POST"])
def api_create_auto_playlist():
    """
    AI destekli otomatik güncellenen kişisel playlist oluşturur.
    Body: { name, max_duration_min, playlist_id (opsiyonel, güncelleme için) }
    """
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401

        body        = request.get_json() or {}
        name        = str(body.get("name") or "Music+ AI Playlist").strip() or "Music+ AI Playlist"
        max_dur_min = int(body.get("max_duration_min") or 60)
        playlist_id = str(body.get("playlist_id") or "").strip() or None
        max_dur_ms  = max_dur_min * 60 * 1000

        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Yeterli dinleme verisi yok"}), 400

        summary    = _build_history_summary(headers, rows)
        track_list = _ai_playlist_generate(summary, max_dur_min, name)
        uris, total_ms = _resolve_tracks_to_uris(track_list, max_dur_ms)

        if not uris:
            return jsonify({"error": "Spotify'da şarkılar bulunamadı"}), 400

        guncellendi_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
        desc = f"Music+ AI tarafından oluşturuldu | Son güncelleme: {guncellendi_str} | {total_ms//60000} dk"

        pid = _create_or_update_spotify_playlist(playlist_id, name, desc, uris)

        # Sheets'e kaydet (haftalık güncelleme için)
        _save_auto_playlist(uid, pid, name, max_dur_min, "personal")

        return jsonify({
            "status":      "ok",
            "playlist_id": pid,
            "track_count": len(uris),
            "total_min":   total_ms // 60000,
        })

    except Exception as e:
        tb_str = traceback.format_exc()
        print(f"AUTO PLAYLIST 500 HATA:\n{tb_str}", flush=True)
        logger.error(f"Auto playlist hata: {e}")
        return jsonify({"error": str(e), "traceback": tb_str}), 500


# ── Otomatik Güncellenen Ortak Playlist ──────────────────────────────────────

@bp.route("/api/playlist/create-auto-collab", methods=["POST"])
def api_create_auto_collab_playlist():
    """
    AI destekli otomatik güncellenen ortak playlist.
    Body: { name, max_duration_min, user_ids: [...], playlist_id (opsiyonel) }
    """
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401

        body        = request.get_json() or {}
        name        = str(body.get("name") or "Music+ Ortak AI Playlist").strip() or "Music+ Ortak AI Playlist"
        max_dur_min = int(body.get("max_duration_min") or 60)
        other_ids   = body.get("user_ids") or []
        playlist_id = str(body.get("playlist_id") or "").strip() or None
        max_dur_ms  = max_dur_min * 60 * 1000

        all_user_ids = list({uid} | set(other_ids))
        if len(all_user_ids) < 2:
            return jsonify({"error": "En az 2 kullanıcı gerekli"}), 400

        # İzin kontrolü
        permitted = sheets.get_all_permitted_users()
        for other in other_ids:
            if other not in permitted and other != uid:
                return jsonify({"error": f"{other} kullanıcısı istatistiklerini paylaşmıyor"}), 403

        summaries = {}
        for user_id in all_user_ids:
            h, r = sheets.get_user_data(user_id)
            if r:
                summaries[user_id] = _build_history_summary(h, r)

        if len(summaries) < 2:
            return jsonify({"error": "Yeterli kullanıcı verisi yok"}), 400

        track_list     = _ai_collab_playlist_generate(summaries, max_dur_min, name)
        uris, total_ms = _resolve_tracks_to_uris(track_list, max_dur_ms)

        if not uris:
            return jsonify({"error": "Spotify'da şarkılar bulunamadı"}), 400

        guncellendi_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
        desc = f"Music+ AI ortak playlist | Son güncelleme: {guncellendi_str} | {total_ms//60000} dk"

        pid = _create_or_update_spotify_playlist(playlist_id, name, desc, uris, public=True)

        # Sheets'e kaydet
        _save_auto_playlist(uid, pid, name, max_dur_min, "collab",
                            extra={"user_ids": all_user_ids})

        return jsonify({
            "status":      "ok",
            "playlist_id": pid,
            "track_count": len(uris),
            "total_min":   total_ms // 60000,
        })

    except Exception as e:
        logger.error(f"Auto collab playlist hata: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Tek Seferlik Ortak Playlist ───────────────────────────────────────────────

@bp.route("/api/playlist/create-collab", methods=["POST"])
def api_create_collab_playlist():
    """
    Ortak çalma listesi (tek seferlik, otomatik güncellenmez).
    Body: { name, max_duration_min, user_ids: [...] }
    """
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401

        body        = request.get_json() or {}
        name        = str(body.get("name") or "Ortak Playlist").strip() or "Ortak Playlist"
        max_dur_min = int(body.get("max_duration_min") or 60)
        other_ids   = body.get("user_ids") or []
        max_dur_ms  = max_dur_min * 60 * 1000

        all_user_ids = list({uid} | set(other_ids))
        if len(all_user_ids) < 2:
            return jsonify({"error": "En az 2 kullanıcı gerekli"}), 400

        permitted = sheets.get_all_permitted_users()
        for other in other_ids:
            if other not in permitted and other != uid:
                return jsonify({"error": f"{other} kullanıcısı istatistiklerini paylaşmıyor"}), 403

        summaries = {}
        for user_id in all_user_ids:
            h, r = sheets.get_user_data(user_id)
            if r:
                summaries[user_id] = _build_history_summary(h, r)

        if len(summaries) < 2:
            return jsonify({"error": "Yeterli kullanıcı verisi yok"}), 400

        track_list     = _ai_collab_playlist_generate(summaries, max_dur_min, name)
        uris, total_ms = _resolve_tracks_to_uris(track_list, max_dur_ms)

        if not uris:
            return jsonify({"error": "Spotify'da şarkılar bulunamadı"}), 400

        desc = f"Music+ ortak playlist | {total_ms//60000} dk | {len(all_user_ids)} kullanıcı"
        pid  = _create_or_update_spotify_playlist(None, name, desc, uris, public=True)

        return jsonify({
            "status":      "ok",
            "playlist_id": pid,
            "track_count": len(uris),
            "total_min":   total_ms // 60000,
        })

    except Exception as e:
        logger.error(f"Collab playlist hata: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Otomatik Playlist Sheets Kaydı ───────────────────────────────────────────

def _save_auto_playlist(uid: str, playlist_id: str, name: str,
                        max_dur_min: int, ptype: str, extra: dict = None):
    """
    Otomatik güncellenecek playlist bilgisini Sheets'e yazar.
    AutoPlaylists sekmesi: user_id | playlist_id | name | max_dur_min | type | extra_json | last_updated
    """
    try:
        ws = sheets._find_sheet("AutoPlaylists")
        if not ws:
            ws = sheets.sh.add_worksheet(title="AutoPlaylists", rows=500, cols=7)
            ws.append_row(
                ["user_id", "playlist_id", "name", "max_dur_min", "type", "extra_json", "last_updated"],
                value_input_option="RAW"
            )

        now_str    = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
        extra_json = json.dumps(extra or {}, ensure_ascii=False)
        records    = ws.get_all_values()

        for i, row in enumerate(records[1:], start=2):
            if len(row) >= 2 and row[0] == uid and row[1] == playlist_id:
                ws.update(f"A{i}:G{i}", [[uid, playlist_id, name, max_dur_min, ptype, extra_json, now_str]])
                return

        ws.append_row([uid, playlist_id, name, max_dur_min, ptype, extra_json, now_str],
                      value_input_option="RAW")

    except Exception as e:
        logger.warning(f"AutoPlaylist kayıt hata ({uid}): {e}")


def run_auto_playlist_updates():
    """
    Haftalık otomatik playlist güncelleme job'ı.
    app.py scheduler'ına Pazar 20:00 UTC olarak eklenecek.
    """
    try:
        ws = sheets._find_sheet("AutoPlaylists")
        if not ws:
            return

        rows = ws.get_all_values()
        if len(rows) < 2:
            return

        logger.info(f"🎵 Otomatik playlist güncelleme başladı ({len(rows)-1} playlist)")

        for row in rows[1:]:
            if not row or not row[0]:
                continue
            uid         = row[0]
            playlist_id = row[1] if len(row) > 1 else ""
            name        = (row[2] or "Music+ AI Playlist").strip() if len(row) > 2 else "Music+ AI Playlist"
            if not name:
                name = "Music+ AI Playlist"
            _dur_raw    = str(row[3]).strip() if len(row) > 3 and row[3] is not None else ""
            max_dur_min = int(_dur_raw) if _dur_raw.isdigit() else 60
            ptype       = (row[4] or "personal").strip() if len(row) > 4 else "personal"
            extra_json  = (row[5] or "{}").strip() if len(row) > 5 else "{}"

            try:
                extra = json.loads(extra_json) if extra_json else {}
            except Exception:
                extra = {}

            try:
                if ptype == "personal":
                    h, r = sheets.get_user_data(uid)
                    if not r:
                        continue
                    summary    = _build_history_summary(h, r)
                    track_list = _ai_playlist_generate(summary, max_dur_min, name)

                elif ptype == "collab":
                    user_ids = extra.get("user_ids", [uid])
                    summaries = {}
                    for u in user_ids:
                        h, r = sheets.get_user_data(u)
                        if r:
                            summaries[u] = _build_history_summary(h, r)
                    if len(summaries) < 2:
                        continue
                    track_list = _ai_collab_playlist_generate(summaries, max_dur_min, name)
                else:
                    continue

                uris, total_ms = _resolve_tracks_to_uris(track_list, max_dur_min * 60 * 1000)
                if not uris:
                    continue

                guncellendi_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
                desc = f"Music+ AI tarafından güncellendi | {guncellendi_str} | {total_ms//60000} dk"

                # spotify client'ı o kullanıcının token'ıyla çalıştır
                from extensions import get_spotify_for_user
                sp = get_spotify_for_user(uid)
                if not sp:
                    continue

                _create_or_update_spotify_playlist_with_client(sp, playlist_id, name, desc, uris)

                # last_updated güncelle
                _save_auto_playlist(uid, playlist_id, name, max_dur_min, ptype, extra)

                logger.info(f"✅ Auto playlist güncellendi: {name} ({uid})")

            except Exception as e:
                logger.error(f"Auto playlist güncelleme hata ({uid}, {playlist_id}): {e}")

    except Exception as e:
        logger.error(f"run_auto_playlist_updates genel hata: {e}")


def _create_or_update_spotify_playlist_with_client(sp, playlist_id, name, desc, track_uris):
    """Verilen spotify client instance ile playlist günceller."""
    if playlist_id:
        try:
            existing = sp._req("GET", f"/playlists/{playlist_id}/tracks?limit=100")
            existing_uris = [
                {"uri": t["track"]["uri"]}
                for t in existing.get("items", [])
                if t.get("track")
            ]
            if existing_uris:
                sp._req("DELETE", f"/playlists/{playlist_id}/tracks",
                        json={"tracks": existing_uris})
        except Exception as e:
            logger.warning(f"Playlist temizleme hata: {e}")
        try:
            sp._req("PUT", f"/playlists/{playlist_id}", json={"name": name, "description": desc})
        except Exception:
            pass
    else:
        pl = sp._req("POST", "/me/playlists", json={
            "name": name, "public": False, "description": desc
        })
        playlist_id = pl["id"]

    for i in range(0, len(track_uris), 100):
        chunk = track_uris[i:i + 100]
        if chunk:
            sp._req("POST", f"/playlists/{playlist_id}/items", json={"uris": chunk})

    return playlist_id


# ── Mevcut Endpointler ────────────────────────────────────────────────────────

@bp.route("/api/playlist/create-top-tracks", methods=["POST"])
def api_create_top_tracks_playlist():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki    = headers.index("Şarkı Adı")
        idx_sarki_id = headers.index("Şarkı ID")

        sarki_id_map = {}
        sarki_counts = Counter()
        for row in rows:
            if len(row) > max(idx_sarki, idx_sarki_id):
                sarki = (row[idx_sarki]    or "").strip()
                sid   = (row[idx_sarki_id] or "").strip()
                if sarki and sid:
                    sarki_counts[sarki] += 1
                    sarki_id_map[sarki]  = sid

        top_sarkilar_ids = []
        for s, _ in sarki_counts.most_common(50):
            if s not in sarki_id_map:
                continue
            tid = _extract_track_id(sarki_id_map[s])
            if tid:
                top_sarkilar_ids.append(f"spotify:track:{tid}")

        if not top_sarkilar_ids:
            return jsonify({"error": "Yeterli şarkı bulunamadı"}), 400

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

        idx_sanatci  = headers.index("Sanatçı")
        idx_sarki    = headers.index("Şarkı Adı")
        idx_sarki_id = headers.index("Şarkı ID")

        sanatci_counts = Counter()
        sanatci_sarki  = defaultdict(dict)

        for row in rows:
            if len(row) > max(idx_sanatci, idx_sarki, idx_sarki_id):
                s       = (row[idx_sanatci]  or "").strip()
                t       = (row[idx_sarki]    or "").strip()
                raw_tid = (row[idx_sarki_id] or "").strip()
                if s:
                    sanatci_counts[s] += 1
                if s and t and raw_tid:
                    clean_tid = _extract_track_id(raw_tid)
                    if clean_tid:
                        sanatci_sarki[s][t] = clean_tid

        top_sanatcilar = [s for s, _ in sanatci_counts.most_common(20)]
        seen_ids       = set()
        track_uris     = []
        for s in top_sanatcilar:
            for tid in list(sanatci_sarki[s].values())[:5]:
                if tid not in seen_ids:
                    seen_ids.add(tid)
                    track_uris.append(f"spotify:track:{tid}")

        track_uris = track_uris[:50]
        if not track_uris:
            return jsonify({"error": "Yeterli şarkı bulunamadı"}), 400

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


@bp.route("/api/playlist/create", methods=["POST"])
def api_create_playlist():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giris yapilmamis"}), 401
        body          = request.get_json() or {}
        ptype         = body.get("type", "top-tracks")
        count         = int(body.get("count", 50))
        max_dur_min   = body.get("max_duration_min")
        max_dur_ms    = int(max_dur_min) * 60 * 1000 if max_dur_min else None

        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"}), 400

        idx_sarki    = headers.index("Şarkı Adı") if "Şarkı Adı" in headers else headers.index("Sarki Adi")
        idx_sarki_id = headers.index("Şarkı ID")  if "Şarkı ID"  in headers else headers.index("Sarki ID")
        idx_sanatci  = next((i for i, h in enumerate(headers) if h.strip() in ("Sanatci", "Sanatçı")), -1)
        idx_sure     = next((i for i, h in enumerate(headers) if h.strip() in ("Süre (ms)", "Sure (ms)")), -1)

        sarki_counts   = Counter()
        sarki_id_map   = {}
        sarki_sure_map = {}
        sanatci_counts = Counter()
        sanatci_sarki  = defaultdict(dict)

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sarki_id):
                continue
            sarki = (row[idx_sarki]    or "").strip()
            sid   = (row[idx_sarki_id] or "").strip()
            sure  = int(row[idx_sure]) if idx_sure != -1 and len(row) > idx_sure and (row[idx_sure] or "").isdigit() else 0
            san   = (row[idx_sanatci]  or "").strip() if idx_sanatci != -1 and len(row) > idx_sanatci else ""
            if sarki and sid:
                sarki_counts[sarki]   += 1
                sarki_id_map[sarki]    = sid
                sarki_sure_map[sarki]  = sure
            if san:
                sanatci_counts[san] += 1
                if sarki and sid:
                    tid = _extract_track_id(sid)
                    if tid:
                        sanatci_sarki[san][sarki] = tid

        track_uris = []
        total_ms   = 0
        pl_name    = ""

        if ptype == "top-tracks":
            pl_name = f"En Cok Dinlediklerim (Top {count})"
            for s, _ in sarki_counts.most_common(count * 3):
                if len(track_uris) >= count:
                    break
                tid = _extract_track_id(sarki_id_map.get(s, ""))
                if not tid:
                    continue
                dur = sarki_sure_map.get(s, 210000)
                if max_dur_ms and (total_ms + dur) > max_dur_ms:
                    continue
                track_uris.append(f"spotify:track:{tid}")
                total_ms += dur

        elif ptype == "top-artists":
            pl_name = f"En Cok Dinledigim Sanatcilar (Top {count})"
            top_san = [s for s, _ in sanatci_counts.most_common(count)]
            seen    = set()
            for san in top_san:
                for sarki_name, tid in list(sanatci_sarki[san].items())[:5]:
                    if tid in seen:
                        continue
                    dur = sarki_sure_map.get(sarki_name, 210000)
                    if max_dur_ms and (total_ms + dur) > max_dur_ms:
                        continue
                    seen.add(tid)
                    track_uris.append(f"spotify:track:{tid}")
                    total_ms += dur

        if not track_uris:
            return jsonify({"error": "Yeterli sarki bulunamadi"}), 400

        dur_str = f"{total_ms//60000} dk" if total_ms else "?"
        pl = spotify._req("POST", "/me/playlists", json={
            "name": pl_name,
            "public": False,
            "description": f"Music+ tarafindan olusturuldu | Toplam sure: {dur_str}"
        })
        playlist_id = pl["id"]
        for i in range(0, len(track_uris), 100):
            chunk = track_uris[i:i + 100]
            if chunk:
                spotify._req("POST", f"/playlists/{playlist_id}/items", json={"uris": chunk})

        return jsonify({
            "status":      "ok",
            "playlist_id": playlist_id,
            "track_count": len(track_uris),
            "total_min":   total_ms // 60000,
        })
    except Exception as e:
        logger.error(f"Playlist olusturma hatasi: {e}")
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


@bp.route("/api/player/play", methods=["POST"])
def api_player_play():
    try:
        spotify.play()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/player/pause", methods=["POST"])
def api_player_pause():
    try:
        spotify.pause()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/player/next", methods=["POST"])
def api_player_next():
    try:
        spotify.next_track()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/player/previous", methods=["POST"])
def api_player_previous():
    try:
        spotify.previous_track()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

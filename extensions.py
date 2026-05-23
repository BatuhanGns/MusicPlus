"""
Flask application extensions ve shared client instance'ları.
Circular import'u önlemek için app.py'den ayrı tutulur.
"""

import logging
from datetime import datetime, timezone

import config
from clients.spotify_client import SpotifyClient
from clients.sheets_client import SheetsClient
from clients.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

# ── Client Instance'ları ─────────────────────────────────────────────────────
spotify = SpotifyClient()
sheets = SheetsClient()
gemini = GeminiClient()


# ── Kullanıcı Veri Yönetimi ─────────────────────────────────────────────────

def get_current_user_id():
    """Session'dan aktif kullanıcı ID'sini döndürür."""
    from flask import session
    return session.get("user_id")


def get_current_user_name():
    """Session'dan aktif kullanıcı adını döndürür."""
    from flask import session
    return session.get("display_name", "Kullanıcı")


def load_user_data(user_id: str):
    headers, rows = sheets.get_user_data(user_id)
    config._user_cache[user_id] = {"headers": headers, "rows": rows}
    return headers, rows


def get_cached_data(user_id: str):
    if user_id not in config._user_cache:
        return load_user_data(user_id)
    return config._user_cache[user_id]["headers"], config._user_cache[user_id]["rows"]


def load_tumveri():
    uid = get_current_user_id()
    if uid:
        return load_user_data(uid)
    return [], []


# ── Sync İşlemleri ───────────────────────────────────────────────────────────



def _apply_sync_rewards(uid: str, new_tracks: list):
    """
    Sync'te gelen yeni track listesinden coin ve XP hesaplar,
    pet carpanini uygular ve pet verisine ekler.

    Sadece YENİ gelen veri kullanilir — eski bakiyeye dokunulmaz.
    """
    try:
        from routes.pets import _load_pet_data, _save_pet_data
        from utils.pets import calc_active_bonuses, calc_pet_level, level_bonus

        data        = _load_pet_data(uid)
        inventory   = data.get("inventory", [])
        active_pets = [p for p in inventory if p.get("active")]
        bonuses     = calc_active_bonuses(active_pets)
        coin_mult   = bonuses.get("coin_multiplier", 1.0)
        xp_mult     = bonuses.get("xp_multiplier",   1.0)

        # Simdi hangi sanatci/sarki/album daha once goruldu?
        # Bunu bilmek icin tum zamanlar verisini kullan
        headers, all_rows = get_cached_data(uid)
        seen_tracks  = set()
        seen_artists = set()
        seen_albums  = set()
        if headers and all_rows:
            try:
                ti = headers.index("Şarkı Adı")
                ai = headers.index("Sanatçı")
                li = next((i for i, h in enumerate(headers)
                           if h.strip() in ("Albüm","Album","albüm","album")), -1)
                # Yeni track'ler haric onceki tum verileri say
                new_set = {(t["track_name"], t["artist_name"]) for t in new_tracks}
                for row in all_rows:
                    if len(row) <= max(ti, ai):
                        continue
                    key = (row[ti].strip(), row[ai].strip())
                    if key in new_set:
                        continue  # Bu sync'te gelen, simdi sayma
                    seen_tracks.add(row[ti].strip())
                    seen_artists.add(row[ai].strip())
                    if li != -1 and len(row) > li:
                        seen_albums.add(row[li].strip())
            except Exception:
                pass

        # Yeni track'ler icin coin ve XP hesapla
        raw_coin = 0.0
        raw_xp   = 0

        for t in new_tracks:
            dur_sec     = int(t.get("duration_sec") or 0)
            track_name  = (t.get("track_name")  or "").strip()
            artist_name = (t.get("artist_name") or "").strip()
            album_name  = (t.get("album_name")  or "").strip()

            # Dinleme suresi
            raw_coin += (dur_sec / 60) * 0.2
            raw_xp   += dur_sec // 60

            # Yeni sanatci bonusu
            if artist_name and artist_name not in seen_artists:
                raw_coin += 20
                raw_xp   += 50
                seen_artists.add(artist_name)

            # Yeni sarki bonusu
            if track_name and track_name not in seen_tracks:
                raw_coin += 10
                raw_xp   += 25
                seen_tracks.add(track_name)

            # Yeni album bonusu
            if album_name and album_name not in seen_albums:
                raw_coin += 10
                raw_xp   += 25
                seen_albums.add(album_name)

        # Carpan uygula
        earned_coin = int(raw_coin * coin_mult)
        earned_xp   = int(raw_xp   * xp_mult)

        # Bakiyeye ekle
        data["coins"] = data.get("coins", 0) + earned_coin
        data["xp"]    = data.get("xp",    0) + earned_xp

        # Pet level'larini guncelle
        for p in inventory:
            p["level_info"] = calc_pet_level(p.get("xp", 0))
            p["lv_bonus"]   = level_bonus(p["level_info"]["level"])

        # Display icin user_name al
        try:
            from extensions import get_current_user_name
            uname = get_current_user_name()
        except Exception:
            uname = uid

        _save_pet_data(uid, uname, data)
        logger.info(
            f"💰 Sync rewards: uid={uid} +{earned_coin} coin (x{coin_mult}) "
            f"+{earned_xp} XP (x{xp_mult}) tracks={len(new_tracks)}"
        )
    except Exception as e:
        logger.warning(f"⚠️ _apply_sync_rewards hatasi: {e}")

def sync_job(user_id: str = None, refresh_token: str = None):
    global _last_sync
    uid = user_id or get_current_user_id()
    if not uid:
        logger.warning("⚠️ Sync: user_id yok, atlanıyor")
        return

    if refresh_token:
        spotify.refresh_token = refresh_token
    elif not spotify.refresh_token:
        spotify.refresh_token = config.SPOTIFY_REFRESH_TOKEN

    if not spotify.refresh_token:
        logger.warning(f"⚠️ Sync: {uid} için refresh_token yok, atlanıyor")
        return

    logger.info(f"🎵 Sync başladı: {uid}")
    try:
        tracks = spotify.get_recently_played()
        new_tracks = []
        if tracks:
            new_count, new_tracks = sheets.append_tracks(uid, tracks)
            logger.info(f"✅ {new_count} yeni kayıt eklendi ({uid})")
        else:
            logger.info("Yeni dinleme yok.")
        sheets.update_last_sync(uid)
        config._last_sync = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M") + " UTC"
        load_user_data(uid)

        # Sadece yeni gelen track'lerden coin ve XP hesapla, pet carpani uygula
        if new_tracks:
            _apply_sync_rewards(uid, new_tracks)

    except Exception as e:
        logger.error(f"❌ Sync hatası ({uid}): {e}")


def scheduled_sync_all():
    try:
        users = sheets.get_all_users_with_tokens()
        if not users:
            logger.info("⏰ Scheduled sync: kayıtlı kullanıcı yok")
            return
        for u in users:
            uid = u["user_id"]
            token = u["refresh_token"]
            if not token:
                logger.warning(f"⚠️ Scheduled sync: {uid} için token yok, atlanıyor")
                continue
            try:
                sync_job(uid, refresh_token=token)
            except Exception as e:
                logger.error(f"❌ Scheduled sync hatası ({uid}): {e}")
    except Exception as e:
        logger.error(f"❌ Scheduled sync genel hata: {e}")

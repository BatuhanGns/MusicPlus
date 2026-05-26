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
    Sync'te gelen SADECE YENİ track'lerden coin/XP hesaplar.
    Eski veriye bakmaz — sadece bu sync'te eklenen kayitlari isler.
    """
    if not new_tracks:
        return
    try:
        from routes.pets import _load_pet_data, _save_pet_data
        from utils.pets import calc_active_bonuses, calc_pet_level, level_bonus

        data        = _load_pet_data(uid)
        inventory   = data.get("inventory", [])
        active_pets = [p for p in inventory if p.get("active")]
        bonuses     = calc_active_bonuses(active_pets)
        coin_mult   = bonuses.get("coin_multiplier", 1.0)
        xp_mult     = bonuses.get("xp_multiplier",   1.0)

        # Her yeni kayit = 1 coin * carpan, 1 XP * carpan
        earned_coin = int(len(new_tracks) * coin_mult)
        earned_xp   = int(len(new_tracks) * xp_mult)

        # Kullanici bakiyesine ekle
        data["coins"] = data.get("coins", 0) + earned_coin
        data["xp"]    = data.get("xp",    0) + earned_xp

        # Pet XP artir (aktif petler kazanilan XP'nin %10'unu alir)
        pet_xp_share = max(1, earned_xp // 10) if earned_xp > 0 else 0
        for p in inventory:
            if p.get("active"):
                p["xp"] = p.get("xp", 0) + pet_xp_share
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

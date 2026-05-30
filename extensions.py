"""
Flask application extensions ve shared client instance'ları.
"""

import logging
from datetime import datetime, timezone

import config
from clients.spotify_client import SpotifyClient
from clients.sheets_client import SheetsClient
from clients.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

spotify = SpotifyClient()
sheets  = SheetsClient()
gemini  = GeminiClient()


# ── Kullanıcı Veri Yönetimi ──────────────────────────────────────────────────

def get_current_user_id():
    from flask import session
    return session.get("user_id")

def get_current_user_name():
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


# ── Sync ─────────────────────────────────────────────────────────────────────

def _apply_sync_rewards(uid: str, new_tracks: list):
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

        earned_coin = int(len(new_tracks) * coin_mult)
        earned_xp   = int(len(new_tracks) * xp_mult)

        data["coins"] = data.get("coins", 0) + earned_coin
        data["xp"]    = data.get("xp",    0) + earned_xp

        pet_xp_share = max(1, earned_xp // 10) if earned_xp > 0 else 0
        for p in inventory:
            if p.get("active"):
                p["xp"] = p.get("xp", 0) + pet_xp_share
            p["level_info"] = calc_pet_level(p.get("xp", 0))
            p["lv_bonus"]   = level_bonus(p["level_info"]["level"])

        try:
            uname = get_current_user_name()
        except Exception:
            uname = uid

        _save_pet_data(uid, uname, data)
        logger.info(
            f"💰 Sync rewards: uid={uid} +{earned_coin} coin +{earned_xp} XP "
            f"tracks={len(new_tracks)}"
        )
    except Exception as e:
        logger.warning(f"⚠️ _apply_sync_rewards hatası: {e}")


def sync_job(user_id: str = None, refresh_token: str = None):
    """
    DÜZELTMELER:
    1. uid boş string kontrolü eklendi ("" → None).
    2. finally bloğu: token rotasyonu gerçekleştiyse YENİ token
       MUTLAKA Sheets'e kaydedilir. Bu sayede invalid_grant hatası
       bir daha oluşmaz.
    3. invalid_grant yakalandığında açık uyarı logu atılır.
    """
    # Boş string kontrolü — Sheets'te bazen "" gelebiliyor
    uid = (user_id or "").strip() or None
    if not uid:
        try:
            uid = get_current_user_id()
        except Exception:
            pass
    if not uid:
        logger.warning("⚠️ Sync: user_id yok veya boş, atlanıyor")
        return

    token = (refresh_token or "").strip() or config.SPOTIFY_REFRESH_TOKEN
    if not token:
        logger.warning(f"⚠️ Sync: {uid} için refresh_token yok, atlanıyor")
        return

    # Her sync için izole client — singleton race condition yok
    client = SpotifyClient(refresh_token=token)
    logger.info(f"🎵 Sync başladı: {uid}")

    try:
        after_ms = sheets.get_last_played_at_ms(uid)
        tracks   = client.get_recently_played(limit=50, after_ms=after_ms)

        new_tracks = []
        if tracks:
            new_count, new_tracks = sheets.append_tracks(uid, tracks)
            logger.info(f"✅ {new_count} yeni kayıt eklendi ({uid})")
        else:
            logger.info(f"Yeni dinleme yok ({uid})")

        sheets.update_last_sync(uid)
        config._last_sync = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M") + " UTC"
        load_user_data(uid)

        if new_tracks:
            _apply_sync_rewards(uid, new_tracks)

    except Exception as e:
        err = str(e)
        logger.error(f"❌ Sync hatası ({uid}): {err}")
        if "invalid_grant" in err:
            logger.warning(
                f"🔑 {uid} için refresh token geçersiz (revoked). "
                f"Kullanıcının yeniden giriş yapması gerekiyor."
            )

    finally:
        # ─────────────────────────────────────────────────────────────────
        # KRİTİK: Spotify PKCE token rotasyonu — her token yenilemede
        # yeni refresh token üretilir. Bu token MUTLAKA Sheets'e
        # kaydedilmeli, aksi hâlde bir sonraki sync'te invalid_grant alınır.
        # ─────────────────────────────────────────────────────────────────
        if client.refresh_token and client.refresh_token != token:
            try:
                sheets.save_refresh_token(uid, client.refresh_token)
                logger.info(f"🔑 Token rotasyonu kaydedildi ({uid})")
            except Exception as save_err:
                logger.error(f"❌ Token kaydetme hatası ({uid}): {save_err}")


def scheduled_sync_all():
    try:
        users = sheets.get_all_users_with_tokens()
        if not users:
            logger.info("⏰ Scheduled sync: kayıtlı kullanıcı yok")
            return
        for u in users:
            uid   = (u.get("user_id") or "").strip()
            token = (u.get("refresh_token") or "").strip()
            if not uid:
                logger.warning("⚠️ Scheduled sync: boş user_id atlanıyor")
                continue
            if not token:
                logger.warning(f"⚠️ Scheduled sync: {uid} için token yok, atlanıyor")
                continue
            try:
                sync_job(uid, refresh_token=token)
            except Exception as e:
                logger.error(f"❌ Scheduled sync hatası ({uid}): {e}")
    except Exception as e:
        logger.error(f"❌ Scheduled sync genel hata: {e}")

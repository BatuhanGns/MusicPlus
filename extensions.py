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
        if tracks:
            new_count = sheets.append_tracks(uid, tracks)
            logger.info(f"✅ {new_count} yeni kayıt eklendi ({uid})")
        else:
            logger.info("Yeni dinleme yok.")
        sheets.update_last_sync(uid)
        config._last_sync = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M") + " UTC"
        load_user_data(uid)
        # Sync sonrasi coin ve XP hesapla, cache'e yaz
        try:
            from utils.gamification import compute_gamification
            from utils.pets import compute_coins
            _h, _r = get_cached_data(uid)
            _gami  = compute_gamification(_h, _r)
            _coins = compute_coins(_h, _r, 1.0)
            _xp    = _gami.get('xp', 0)
            sheets.save_gamification_cache(uid, _coins, _xp)
            config._user_cache[uid]['coins'] = _coins
            config._user_cache[uid]['xp']    = _xp
        except Exception as _ge:
            logger.warning(f'Gamification cache guncellenemedi: {_ge}')
        logger.info(f"📊 Sync tamamlandı: {uid}")
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

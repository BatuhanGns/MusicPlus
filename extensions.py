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
# spotify: sadece HTTP request context'te (login, now-playing vs.) kullanılır.
# Background sync'te per-user SpotifyClient instance oluşturulur — race condition yok.
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
            f"💰 Sync rewards: uid={uid} +{earned_coin} coin (x{coin_mult}) "
            f"+{earned_xp} XP (x{xp_mult}) tracks={len(new_tracks)}"
        )
    except Exception as e:
        logger.warning(f"⚠️ _apply_sync_rewards hatasi: {e}")


def sync_job(user_id: str = None, refresh_token: str = None):
    """
    DÜZELTİLDİ:
    1. Her sync için ayrı SpotifyClient instance'ı oluşturulur.
       Böylece singleton race condition ortadan kalkar.
    2. Sheets'teki son played_at zamanı okunarak 'after' parametresiyle
       sadece yeni şarkılar çekilir — kaçırılan periyotlar telafi edilir.
    3. Aynı şarkıyı tekrar dinleme artık duplicate sayılmaz (played_at farklı).
    """
    uid = user_id
    if not uid:
        # Request context varsa session'dan al (manuel sync butonu)
        try:
            uid = get_current_user_id()
        except Exception:
            pass
    if not uid:
        logger.warning("⚠️ Sync: user_id yok, atlanıyor")
        return

    token = refresh_token or config.SPOTIFY_REFRESH_TOKEN
    if not token:
        logger.warning(f"⚠️ Sync: {uid} için refresh_token yok, atlanıyor")
        return

    # Token rotasyonu olduğunda bellekteki dict'i anında güncelle
    def _on_token_refresh(new_token: str):
        config._refresh_tokens[uid] = new_token
        logger.info(f"✅ Yeni refresh token bellekte güncellendi ({uid})")
        # Sheets'e de yaz (ikincil yedek olarak)
        try:
            sheets.save_refresh_token(uid, new_token)
        except Exception:
            pass  # Sheets hatası sync'i engellemez

    # Her kullanıcı için izole edilmiş yeni bir client — global state kirlenmez
    client = SpotifyClient(refresh_token=token, token_refresh_callback=_on_token_refresh)

    logger.info(f"🎵 Sync başladı: {uid}")
    try:
        # Son kaydedilen played_at zamanını al → Spotify 'after' parametresi için
        after_ms = sheets.get_last_played_at_ms(uid)
        
        tracks = client.get_recently_played(limit=50, after_ms=after_ms)
        
        new_tracks = []
        if tracks:
            new_count, new_tracks = sheets.append_tracks(uid, tracks)
            logger.info(f"✅ {new_count} yeni kayıt eklendi ({uid})")
        else:
            logger.info(f"Yeni dinleme yok ({uid}).")
            
        sheets.update_last_sync(uid)
        config._last_sync = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M") + " UTC"
        load_user_data(uid)

        if new_tracks:
            _apply_sync_rewards(uid, new_tracks)

    except Exception as e:
        logger.error(f"❌ Sync hatası ({uid}): {e}")


def scheduled_sync_all():
    """
    config._refresh_tokens dict'indeki tüm kullanıcıları sync eder.
    Sheets'e bağımlı değil — bellekteki token'lar kullanılır.
    Server restart'ta dict sıfırlanır; kullanıcılar tekrar giriş yapınca dolar.
    """
    try:
        tokens = dict(config._refresh_tokens)  # snapshot al (döngü sırasında değişmesin)
        if not tokens:
            logger.info("⏰ Scheduled sync: bellekte kayıtlı kullanıcı yok")
            # Fallback: Sheets'ten yükle (server restart sonrası ilk çalışma için)
            try:
                users = sheets.get_all_users_with_tokens()
                for u in users:
                    uid_s = u["user_id"]
                    tok_s = u["refresh_token"]
                    if uid_s and tok_s:
                        config._refresh_tokens[uid_s] = tok_s
                        logger.info(f"📥 Sheets fallback: {uid_s} token'ı belleğe yüklendi")
                tokens = dict(config._refresh_tokens)
            except Exception as fb_err:
                logger.warning(f"⚠️ Sheets fallback hatası: {fb_err}")

        if not tokens:
            return

        for uid, token in tokens.items():
            try:
                sync_job(uid, refresh_token=token)
            except Exception as e:
                logger.error(f"❌ Scheduled sync hatası ({uid}): {e}")
    except Exception as e:
        logger.error(f"❌ Scheduled sync genel hata: {e}")

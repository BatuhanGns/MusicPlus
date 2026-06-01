import os
import logging
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler

import config
from extensions import sheets, scheduled_sync_all
from routes import register_blueprints

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    app.config["PERMANENT_SESSION_LIFETIME"] = config.PERMANENT_SESSION_LIFETIME
    register_blueprints(app)
    _start_scheduler()
    return app


def _refresh_all_access_tokens():
    """
    50 dakikada bir tüm kullanıcıların access token'larını yeniler.
    - Sheets'teki expires_at okunur; süresi dolmak üzereyse yenilenir.
    - Yeni token hem bellek cache'ine hem Sheets H/I sütunlarına yazılır.
    - Refresh token iptal olmuşsa o kullanıcı atlanır, diğerleri etkilenmez.
    """
    from clients.spotify_client import SpotifyClient
    import time as _time

    # Sheets'ten tüm kullanıcıları yükle (server restart sonrası kurtarma)
    try:
        users = sheets.get_all_users_with_tokens()
        for u in users:
            uid_s = u["user_id"]
            tok_s = u["refresh_token"]
            if uid_s and tok_s and uid_s not in config._refresh_tokens:
                config._refresh_tokens[uid_s] = tok_s
                logger.info(f"📥 Token yenileme: {uid_s} Sheets'ten belleğe yüklendi")
    except Exception as e:
        logger.warning(f"⚠️ Sheets kullanıcı yükleme hatası: {e}")

    tokens = dict(config._refresh_tokens)
    if not tokens:
        logger.info("⏰ Token yenileme: bellekte kullanıcı yok")
        return

    logger.info(f"⏰ Access token yenileme başladı ({len(tokens)} kullanıcı)")
    for uid, r_token in tokens.items():
        try:
            # Sheets'teki expires_at kontrol et; hâlâ geçerliyse atla
            saved = sheets.get_access_token(uid)
            if saved and _time.time() < saved.get("expires_at", 0) - 60:
                logger.info(f"⏭️ Token hâlâ geçerli, atlanıyor: {uid}")
                continue

            def _on_rotate(new_rt, _uid=uid):
                config._refresh_tokens[_uid] = new_rt
                try:
                    sheets.save_refresh_token(_uid, new_rt)
                except Exception:
                    pass

            client = SpotifyClient(refresh_token=r_token, token_refresh_callback=_on_rotate)
            client._do_refresh(r_token, uid=uid, in_req=False, sheets_client=sheets)
            logger.info(f"✅ Access token yenilendi ve Sheets'e yazıldı: {uid}")
        except Exception as e:
            logger.warning(f"⚠️ Token yenileme hatası ({uid}): {e}")


def _start_scheduler():
    """
    - spotify_sync   : 15dk'da bir şarkı sync
    - token_refresh  : 45dk'da bir access token yenileme
    - daily_limit_reset: her gece 00:00 UTC'de limit sıfırlama
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.executors.pool import ThreadPoolExecutor

        executors    = {"default": ThreadPoolExecutor(2)}
        job_defaults = {"coalesce": True, "misfire_grace_time": 900}

        scheduler = BackgroundScheduler(executors=executors, job_defaults=job_defaults)

        # Şarkı sync — 15dk
        scheduler.add_job(
            scheduled_sync_all,
            "interval",
            minutes=15,
            id="spotify_sync",
            replace_existing=True,
        )

        # Access token yenileme — 50dk
        scheduler.add_job(
            _refresh_all_access_tokens,
            "interval",
            minutes=50,
            id="token_refresh",
            replace_existing=True,
        )

        # Günlük limit sıfırlama — gece yarısı UTC
        scheduler.add_job(
            lambda: sheets.reset_daily_limits(),
            "cron",
            hour=0, minute=0,
            id="daily_limit_reset",
            timezone="UTC",
            replace_existing=True,
        )

        scheduler.start()
        logger.info("✅ Scheduler başlatıldı (sync=15dk, token_refresh=45dk)")

    except Exception as e:
        logger.error(f"Scheduler hatası, fallback thread başlatılıyor: {e}")
        _start_fallback_thread()


def _start_fallback_thread():
    """APScheduler başlamazsa manuel thread'lerle çalışır."""
    import threading, time

    def sync_loop():
        time.sleep(60)
        while True:
            try:
                scheduled_sync_all()
            except Exception as ex:
                logger.error(f"Fallback sync hatası: {ex}")
            time.sleep(900)  # 15 dakika

    def token_loop():
        time.sleep(120)
        while True:
            try:
                _refresh_all_access_tokens()
            except Exception as ex:
                logger.error(f"Fallback token yenileme hatası: {ex}")
            time.sleep(3000)  # 50 dakika

    t1 = threading.Thread(target=sync_loop,  daemon=True, name="sync-fallback")
    t2 = threading.Thread(target=token_loop, daemon=True, name="token-refresh-fallback")
    t1.start()
    t2.start()
    logger.info("✅ Fallback thread'ler başlatıldı (sync=15dk, token_refresh=45dk)")


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

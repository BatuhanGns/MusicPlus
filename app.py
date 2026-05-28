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


def _start_scheduler():
    """
    DÜZELTİLDİ:
    - Sync aralığı 30dk → 15dk'ya indirildi.
      Spotify recently-played max 50 şarkı döndürür.
      15 dakikada 50 şarkı = saatte 200 → yeterince güvenli marj.
    - 'after' parametresi ile sadece son kayıttan sonrası çekildiği için
      15dk'da 50 limit hiçbir zaman aşılmaz.
    - coalesce=True: Render uyku sonrası biriken job'lar tek seferde çalışır.
    - misfire_grace_time=900: 15 dakikaya kadar gecikme tolere edilir.
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.executors.pool import ThreadPoolExecutor

        executors    = {"default": ThreadPoolExecutor(2)}
        job_defaults = {"coalesce": True, "misfire_grace_time": 900}

        scheduler = BackgroundScheduler(executors=executors, job_defaults=job_defaults)

        scheduler.add_job(
            scheduled_sync_all,
            "interval",
            minutes=15,           # 30dk'dan 15dk'ya indirildi
            id="spotify_sync",
            replace_existing=True,
        )
        scheduler.add_job(
            lambda: sheets.reset_daily_limits(),
            "cron",
            hour=0, minute=0,
            id="daily_limit_reset",
            timezone="UTC",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("✅ Scheduler başlatıldı (sync interval=15dk)")

    except Exception as e:
        logger.error(f"Scheduler hatası, fallback thread başlatılıyor: {e}")
        _start_fallback_thread()


def _start_fallback_thread():
    """APScheduler başlamazsa 15 dakikada bir sync yapar."""
    import threading, time

    def loop():
        time.sleep(60)  # ilk başlangıçta kısa bekle
        while True:
            try:
                scheduled_sync_all()
            except Exception as ex:
                logger.error(f"Fallback sync hatası: {ex}")
            time.sleep(900)  # 15 dakika

    t = threading.Thread(target=loop, daemon=True, name="sync-fallback")
    t.start()
    logger.info("✅ Fallback sync thread başlatıldı (15dk)")


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

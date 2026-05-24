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
    Scheduler'i baslatir. Hata olursa fallback thread loop ile devam eder.
    Render free tier'da uyku sonrasi da calismeye devam etmesi icin
    misfire_grace_time ve coalesce eklendi.
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.executors.pool import ThreadPoolExecutor
        executors  = {"default": ThreadPoolExecutor(2)}
        job_defaults = {"coalesce": True, "misfire_grace_time": 600}
        scheduler = BackgroundScheduler(executors=executors, job_defaults=job_defaults)
        scheduler.add_job(
            scheduled_sync_all, "interval", minutes=30,
            id="spotify_sync", replace_existing=True
        )
        scheduler.add_job(
            lambda: sheets.reset_daily_limits(),
            "cron", hour=0, minute=0, id="daily_limit_reset",
            timezone="UTC", replace_existing=True,
        )
        scheduler.start()
        logger.info("Scheduler baslatildi (interval=30dk)")
    except Exception as e:
        logger.error(f"Scheduler hatasi, fallback thread baslatiliyor: {e}")
        _start_fallback_thread()


def _start_fallback_thread():
    """APScheduler baslamazsa basit bir thread loop ile sync yapar."""
    import threading, time
    def loop():
        time.sleep(60)  # ilk baslangicta bekle
        while True:
            try:
                scheduled_sync_all()
            except Exception as ex:
                logger.error(f"Fallback sync hatasi: {ex}")
            time.sleep(1800)  # 30 dakika
    t = threading.Thread(target=loop, daemon=True, name="sync-fallback")
    t.start()
    logger.info("Fallback sync thread baslatildi")


# Gunicorn ve python app.py icin app nesnesi her zaman module seviyesinde olusturulur
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

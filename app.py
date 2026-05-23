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
    try:
        scheduler = BackgroundScheduler()
        scheduler.add_job(scheduled_sync_all, "cron", minute="0,30", id="spotify_sync")
        scheduler.add_job(
            lambda: sheets.reset_daily_limits(),
            "cron", hour=0, minute=0, id="daily_limit_reset", timezone="UTC",
        )
        scheduler.start()
        logger.info("Scheduler baslatildi")
    except Exception as e:
        logger.error(f"Scheduler hatasi: {e}")


# Gunicorn ve python app.py icin app nesnesi her zaman module seviyesinde olusturulur
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

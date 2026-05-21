"""
Music+ — Spotify Kişisel İstatistik ve AI Asistan Uygulaması
============================================================
Modüler Flask uygulaması. Tüm route'lar Blueprint'ler üzerinden yüklenir.

Yapı:
  config.py          → Ortam değişkenleri ve sabitler
  extensions.py      → Client instance'ları ve shared state
  utils/helpers.py   → Pure fonksiyonlar (istatistik, formatlama)
  routes/            → Endpoint blueprint'leri (auth, stats, songs, playlists, ai, system, topluluk)
  clients/           → Spotify, Sheets, Gemini client modülleri
  templates/         → HTML şablonları
"""

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
    """Flask application factory."""
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    app.config["PERMANENT_SESSION_LIFETIME"] = config.PERMANENT_SESSION_LIFETIME

    # Tüm blueprint'leri kaydet
    register_blueprints(app)

    return app


app = create_app()

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    # Her 30 dakikada bir tüm kullanıcıları senkronize et
    scheduler.add_job(scheduled_sync_all, "cron", minute="0,30", id="spotify_sync")
    # Her gün 00:00 UTC'de günlük AI limit sıfırla + aylık arşive yaz
    scheduler.add_job(
        lambda: sheets.reset_daily_limits(),
        "cron",
        hour=0,
        minute=0,
        id="daily_limit_reset",
        timezone="UTC",
    )
    scheduler.start()
    logger.info("⏰ Scheduler başlatıldı (her 30 dakika sync + 00:00 UTC limit sıfırlama)")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

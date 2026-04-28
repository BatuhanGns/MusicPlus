import os
import time
import logging
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from spotify_client import SpotifyClient
from sheets_client import SheetsClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

spotify = SpotifyClient()
sheets = SheetsClient()

def sync_job():
    logger.info("🎵 Sync başladı...")
    try:
        tracks = spotify.get_recently_played()
        if tracks:
            new_count = sheets.append_ham(tracks)
            logger.info(f"✅ {new_count} yeni kayıt eklendi.")
        else:
            logger.info("Yeni dinleme yok.")
        sheets.update_ozet()
        logger.info("📊 Özet güncellendi.")
    except Exception as e:
        logger.error(f"❌ Sync hatası: {e}")

@app.route("/")
def index():
    return jsonify({"status": "running", "message": "Spotify → Sheets sync aktif"})

@app.route("/sync")
def manual_sync():
    sync_job()
    return jsonify({"status": "ok", "message": "Manuel sync tamamlandı"})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(sync_job, "interval", minutes=30, id="spotify_sync")
    scheduler.start()
    logger.info("⏰ Scheduler başlatıldı (her 30 dakika)")
    sync_job()  # İlk çalıştırma
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

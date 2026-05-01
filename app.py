import os
import time
import logging
from datetime import datetime, timezone
from collections import defaultdict
from flask import Flask, jsonify, render_template
from apscheduler.schedulers.background import BackgroundScheduler
from spotify_client import SpotifyClient
from sheets_client import SheetsClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

spotify = SpotifyClient()
sheets  = SheetsClient()

_last_sync = "Henüz sync yapılmadı"

def sync_job():
    global _last_sync
    logger.info("🎵 Sync başladı...")
    try:
        tracks = spotify.get_recently_played()
        if tracks:
            new_count = sheets.append_ham(tracks)
            logger.info(f"✅ {new_count} yeni kayıt eklendi.")
        else:
            logger.info("Yeni dinleme yok.")
        sheets.update_ozet()
        sheets.update_analiz()
        _last_sync = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M") + " UTC"
        logger.info("📊 Özet ve Analiz güncellendi.")
    except Exception as e:
        logger.error(f"❌ Sync hatası: {e}")

@app.route("/")
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/api/dashboard")
def api_dashboard():
    try:
        ws = sheets._find_sheet("TümVeri")
        all_values = ws.get_all_values()

        if len(all_values) < 2:
            return jsonify({"error": "Veri yok"})

        headers = all_values[0]
        rows    = all_values[1:]

        idx_sarki   = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure    = headers.index("Süre (sn)")
        idx_tarih   = headers.index("Dinlenme Tarihi")

        track_counts   = defaultdict(lambda: {"count": 0, "sanatci": ""})
        artist_counts  = defaultdict(int)
        gun_sure       = defaultdict(int)
        ay_stats       = defaultdict(lambda: {"sure": 0, "kayit": 0, "gunler": set()})
        toplam_sure_sn = 0

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_tarih):
                continue
            sarki   = row[idx_sarki].strip()
            sanatci = row[idx_sanatci].strip()
            tarih   = row[idx_tarih].strip()
            try:
                sure = int(row[idx_sure])
            except (ValueError, IndexError):
                sure = 0

            toplam_sure_sn += sure

            if sarki:
                track_counts[sarki]["count"] += 1
                track_counts[sarki]["sanatci"] = sanatci
            if sanatci:
                artist_counts[sanatci] += 1
            if tarih:
                gun_sure[tarih] += sure
                try:
                    gun, ay, yil = tarih.split(".")
                    ay_key = f"{yil}-{ay}"
                    ay_stats[ay_key]["sure"]  += sure
                    ay_stats[ay_key]["kayit"] += 1
                    ay_stats[ay_key]["gunler"].add(tarih)
                except ValueError:
                    pass

        # Top 10 şarkı
        top_sarkilar = sorted(
            [{"sarki": k, "sanatci": v["sanatci"], "count": v["count"]} for k, v in track_counts.items()],
            key=lambda x: -x["count"]
        )[:10]

        # Top 10 sanatçı
        top_sanatcilar = sorted(
            [{"sanatci": k, "count": v} for k, v in artist_counts.items()],
            key=lambda x: -x["count"]
        )[:10]

        # Son 7 gün
        from datetime import timedelta
        TR_GUNLER = {0:"Pazartesi",1:"Salı",2:"Çarşamba",3:"Perşembe",4:"Cuma",5:"Cumartesi",6:"Pazar"}
        TR_AYLAR  = {1:"Ocak",2:"Şubat",3:"Mart",4:"Nisan",5:"Mayıs",6:"Haziran",
                     7:"Temmuz",8:"Ağustos",9:"Eylül",10:"Ekim",11:"Kasım",12:"Aralık"}
        bugun = datetime.now(timezone.utc).date()
        hafta = []
        for i in range(6, -1, -1):
            gun = bugun - timedelta(days=i)
            tarih_str = gun.strftime("%d.%m.%Y")
            hafta.append({
                "tarih":  tarih_str,
                "gun":    TR_GUNLER[gun.weekday()],
                "sure_sn": gun_sure.get(tarih_str, 0)
            })

        # Aylık analiz
        def fmt_sure(sn):
            s = int(sn)
            saat = s // 3600
            dk   = (s % 3600) // 60
            return f"{saat} Saat {dk} Dakika" if saat > 0 else f"{dk} Dakika"

        aylar = []
        for ay_key in sorted(ay_stats.keys()):
            yil, ay_no = ay_key.split("-")
            stats = ay_stats[ay_key]
            gun_sayisi = len(stats["gunler"])
            ort_sn = stats["sure"] // gun_sayisi if gun_sayisi > 0 else 0
            aylar.append({
                "ay":          f"{TR_AYLAR[int(ay_no)]} {yil}",
                "toplam":      fmt_sure(stats["sure"]),
                "ortalama":    fmt_sure(ort_sn),
                "kayit_sayisi": stats["kayit"]
            })

        return jsonify({
            "toplam_kayit":   len(rows),
            "farkli_sarki":   len(track_counts),
            "farkli_sanatci": len(artist_counts),
            "toplam_sure_sn": toplam_sure_sn,
            "son_sync":       _last_sync,
            "top_sarkilar":   top_sarkilar,
            "top_sanatcilar": top_sanatcilar,
            "hafta":          hafta,
            "aylar":          aylar,
        })

    except Exception as e:
        logger.error(f"❌ Dashboard API hatası: {e}")
        return jsonify({"error": str(e)}), 500

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
    sync_job()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

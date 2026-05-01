import os
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from flask import Flask, jsonify, render_template, request
from apscheduler.schedulers.background import BackgroundScheduler
from spotify_client import SpotifyClient
from sheets_client import SheetsClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
spotify = SpotifyClient()
sheets  = SheetsClient()

_last_sync = "Henüz sync yapılmadı"
_cached_rows = []
_cached_headers = []

TR_GUNLER = {0:"Pazartesi",1:"Salı",2:"Çarşamba",3:"Perşembe",4:"Cuma",5:"Cumartesi",6:"Pazar"}
TR_AYLAR  = {1:"Ocak",2:"Şubat",3:"Mart",4:"Nisan",5:"Mayıs",6:"Haziran",
             7:"Temmuz",8:"Ağustos",9:"Eylül",10:"Ekim",11:"Kasım",12:"Aralık"}
VAKIT = {
    range(6,12):  "Sabah (06-12)",
    range(12,18): "Öğleden Sonra (12-18)",
    range(18,24): "Akşam (18-24)",
    range(0,6):   "Gece (00-06)",
}

def get_vakit(saat):
    for r, label in VAKIT.items():
        if saat in r:
            return label
    return "Gece (00-06)"

def fmt_sure(sn):
    s = int(sn)
    saat = s // 3600
    dk   = (s % 3600) // 60
    return f"{saat} Saat {dk} Dakika" if saat > 0 else f"{dk} Dakika"

def load_tumveri():
    global _cached_rows, _cached_headers
    ws = sheets._find_sheet("TümVeri")
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        return [], []
    _cached_headers = all_values[0]
    _cached_rows    = all_values[1:]
    return _cached_headers, _cached_rows

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
        load_tumveri()
        logger.info("📊 Sync tamamlandı.")
    except Exception as e:
        logger.error(f"❌ Sync hatası: {e}")

@app.route("/")
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/api/dashboard")
def api_dashboard():
    try:
        headers, rows = load_tumveri()
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki   = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure    = headers.index("Süre (sn)")
        idx_tarih   = headers.index("Dinlenme Tarihi")

        track_counts  = defaultdict(lambda: {"count": 0, "sanatci": "", "sure": 0})
        artist_counts = defaultdict(lambda: {"count": 0, "sure": 0})
        gun_sure      = defaultdict(int)
        ay_stats      = defaultdict(lambda: {"sure": 0, "kayit": 0, "gunler": set()})
        toplam_sure   = 0

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_tarih):
                continue
            sarki   = row[idx_sarki].strip()
            sanatci = row[idx_sanatci].strip()
            tarih   = row[idx_tarih].strip()
            try:
                sure = int(row[idx_sure])
            except:
                sure = 0

            toplam_sure += sure

            if sarki:
                track_counts[sarki]["count"]  += 1
                track_counts[sarki]["sure"]   += sure
                track_counts[sarki]["sanatci"] = sanatci
            if sanatci:
                artist_counts[sanatci]["count"] += 1
                artist_counts[sanatci]["sure"]  += sure
            if tarih:
                gun_sure[tarih] += sure
                try:
                    g, ay, yil = tarih.split(".")
                    ak = f"{yil}-{ay}"
                    ay_stats[ak]["sure"]  += sure
                    ay_stats[ak]["kayit"] += 1
                    ay_stats[ak]["gunler"].add(tarih)
                except:
                    pass

        top_sarkilar = sorted(
            [{"sarki": k, "sanatci": v["sanatci"], "count": v["count"], "sure": v["sure"]}
             for k, v in track_counts.items()],
            key=lambda x: -x["count"]
        )[:10]

        top_sanatcilar = sorted(
            [{"sanatci": k, "count": v["count"], "sure": v["sure"]}
             for k, v in artist_counts.items()],
            key=lambda x: -x["count"]
        )[:10]

        bugun = datetime.now(timezone.utc).date()
        hafta = []
        for i in range(6, -1, -1):
            gun = bugun - timedelta(days=i)
            ts  = gun.strftime("%d.%m.%Y")
            hafta.append({"tarih": ts, "gun": TR_GUNLER[gun.weekday()], "sure_sn": gun_sure.get(ts, 0)})

        aylar = []
        for ak in sorted(ay_stats.keys()):
            yil, ay_no = ak.split("-")
            st = ay_stats[ak]
            gs = len(st["gunler"])
            aylar.append({
                "ay": f"{TR_AYLAR[int(ay_no)]} {yil}",
                "toplam": fmt_sure(st["sure"]),
                "ortalama": fmt_sure(st["sure"] // gs if gs else 0),
                "kayit_sayisi": st["kayit"]
            })

        return jsonify({
            "toplam_kayit":   len(rows),
            "farkli_sarki":   len(track_counts),
            "farkli_sanatci": len(artist_counts),
            "toplam_sure_sn": toplam_sure,
            "son_sync":       _last_sync,
            "top_sarkilar":   top_sarkilar,
            "top_sanatcilar": top_sanatcilar,
            "hafta":          hafta,
            "aylar":          aylar,
        })
    except Exception as e:
        logger.error(f"❌ Dashboard API hatası: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sarki/<path:sarki_adi>")
def api_sarki_detay(sarki_adi):
    try:
        if not _cached_rows:
            load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki   = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure    = headers.index("Süre (sn)")
        idx_iso     = headers.index("_played_at_iso")

        saat_counts = defaultdict(int)
        vakit_counts = defaultdict(int)
        toplam_count = 0
        toplam_sure  = 0
        sanatci      = ""

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_iso):
                continue
            if row[idx_sarki].strip() != sarki_adi:
                continue

            toplam_count += 1
            sanatci = row[idx_sanatci].strip()
            try:
                sure = int(row[idx_sure])
                toplam_sure += sure
            except:
                pass

            iso = row[idx_iso].strip()
            if iso and iso != "—":
                try:
                    # "2026-04-28T21:05:00.000Z"
                    dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                    saat_counts[dt.hour] += 1
                    vakit_counts[get_vakit(dt.hour)] += 1
                except:
                    pass

        # Saat dağılımı: 0-23 tüm saatleri döndür
        saatler = [{"saat": f"{h:02d}:00", "count": saat_counts.get(h, 0)} for h in range(24)]
        vakitler = [{"vakit": k, "count": v} for k, v in sorted(vakit_counts.items(), key=lambda x: -x[1])]

        return jsonify({
            "sarki":        sarki_adi,
            "sanatci":      sanatci,
            "toplam_count": toplam_count,
            "toplam_sure":  fmt_sure(toplam_sure),
            "saatler":      saatler,
            "vakitler":     vakitler,
        })
    except Exception as e:
        logger.error(f"❌ Şarkı detay hatası: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sanatci/<path:sanatci_adi>")
def api_sanatci_detay(sanatci_adi):
    try:
        if not _cached_rows:
            load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki   = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure    = headers.index("Süre (sn)")
        idx_iso     = headers.index("_played_at_iso")

        sarki_counts = defaultdict(lambda: {"count": 0, "sure": 0})
        saat_counts  = defaultdict(int)
        vakit_counts = defaultdict(int)
        toplam_count = 0
        toplam_sure  = 0

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_iso):
                continue
            if row[idx_sanatci].strip() != sanatci_adi:
                continue

            toplam_count += 1
            sarki = row[idx_sarki].strip()
            try:
                sure = int(row[idx_sure])
                toplam_sure += sure
            except:
                sure = 0

            if sarki:
                sarki_counts[sarki]["count"] += 1
                sarki_counts[sarki]["sure"]  += sure

            iso = row[idx_iso].strip()
            if iso and iso != "—":
                try:
                    dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                    saat_counts[dt.hour] += 1
                    vakit_counts[get_vakit(dt.hour)] += 1
                except:
                    pass

        top_sarkilar = sorted(
            [{"sarki": k, "count": v["count"], "sure": fmt_sure(v["sure"])}
             for k, v in sarki_counts.items()],
            key=lambda x: -x["count"]
        )[:10]

        saatler  = [{"saat": f"{h:02d}:00", "count": saat_counts.get(h, 0)} for h in range(24)]
        vakitler = [{"vakit": k, "count": v} for k, v in sorted(vakit_counts.items(), key=lambda x: -x[1])]

        return jsonify({
            "sanatci":      sanatci_adi,
            "toplam_count": toplam_count,
            "toplam_sure":  fmt_sure(toplam_sure),
            "top_sarkilar": top_sarkilar,
            "saatler":      saatler,
            "vakitler":     vakitler,
        })
    except Exception as e:
        logger.error(f"❌ Sanatçı detay hatası: {e}")
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

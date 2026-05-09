import os
import io
import csv
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from flask import Flask, jsonify, render_template, request, Response, stream_with_context, redirect, session
from apscheduler.schedulers.background import BackgroundScheduler
from spotify_client import SpotifyClient
from sheets_client import SheetsClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "muzik_istatistiklerin_gizli_anahtar")

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
    return f"{saat}s {dk}dk" if saat > 0 else f"{dk}dk"

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
    try:
        if not spotify.refresh_token:
            logger.warning("Sync atlandı: Refresh token yok (Kullanıcı henüz giriş yapmadı).")
            return
        tracks = spotify.get_recently_played()
        if tracks:
            sheets.append_ham(tracks)
        sheets.update_ozet()
        sheets.update_analiz()
        _last_sync = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M") + " UTC"
        load_tumveri()
    except Exception as e:
        logger.error(f"Sync hatası: {e}")

def get_redirect_uri():
    base_url = request.url_root.rstrip('/')
    if "localhost" not in base_url and "127.0.0.1" not in base_url:
        base_url = base_url.replace("http://", "https://")
    return f"{base_url}/callback"

# ----- AUTH ROTALARI -----

@app.route("/login")
def login():
    redirect_uri = get_redirect_uri()
    auth_url = spotify.get_auth_url(redirect_uri)
    return redirect(auth_url)

@app.route("/callback")
def callback():
    error = request.args.get("error")
    if error:
        return redirect("/")
    code = request.args.get("code")
    if code:
        try:
            redirect_uri = get_redirect_uri()
            spotify.exchange_code(code, redirect_uri)
            session["logged_in"] = True
        except Exception as e:
            logger.error(f"Login Hatası: {e}")
    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ----- GÖRÜNÜM & TEMEL İSTATİSTİKLER -----

@app.route("/")
@app.route("/dashboard")
def dashboard():
    logged_in = session.get("logged_in", False)
    return render_template("dashboard.html", logged_in=logged_in)

@app.route("/api/export-csv")
def export_csv():
    headers, rows = load_tumveri()
    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        yield output.getvalue()
        for row in rows:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(row)
            yield output.getvalue()
    return Response(stream_with_context(generate()), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=spotify_verilerim.csv"})

@app.route("/api/dashboard")
def api_dashboard():
    if not session.get("logged_in"): return jsonify({"error": "Unauthorized"}), 401
    try:
        headers, rows = load_tumveri()
        if not rows: return jsonify({"error": "Veri yok"})
        idx_sarki = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure = headers.index("Süre (sn)")
        idx_tarih = headers.index("Dinlenme Tarihi")
        idx_iso = headers.index("_played_at_iso") if "_played_at_iso" in headers else -1

        track_counts = defaultdict(lambda: {"count": 0, "sanatci": "", "sure": 0, "ilk_iso": None})
        artist_counts = defaultdict(lambda: {"count": 0, "sure": 0, "ilk_iso": None})
        gun_sure = defaultdict(int)
        ay_stats = defaultdict(lambda: {"sure": 0, "kayit": 0, "gunler": set()})
        toplam_sure = 0
        global_saat_counts = defaultdict(int)
        global_vakit_counts = defaultdict(int)
        ilk_kayit_iso = None

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_tarih): continue
            sarki, sanatci, tarih = row[idx_sarki].strip(), row[idx_sanatci].strip(), row[idx_tarih].strip()
            try: sure = int(row[idx_sure])
            except: sure = 0
            iso = row[idx_iso].strip() if idx_iso != -1 and len(row) > idx_iso else ""
            toplam_sure += sure

            if iso and iso != "—":
                try:
                    dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                    global_saat_counts[dt.hour] += 1
                    global_vakit_counts[get_vakit(dt.hour)] += 1
                    if ilk_kayit_iso is None or iso < ilk_kayit_iso: ilk_kayit_iso = iso
                except: pass

            if sarki:
                track_counts[sarki]["count"] += 1
                track_counts[sarki]["sure"] += sure
                track_counts[sarki]["sanatci"] = sanatci
                if iso and iso != "—":
                    if track_counts[sarki]["ilk_iso"] is None or iso < track_counts[sarki]["ilk_iso"]:
                        track_counts[sarki]["ilk_iso"] = iso
            if sanatci:
                artist_counts[sanatci]["count"] += 1
                artist_counts[sanatci]["sure"] += sure
                if iso and iso != "—":
                    if artist_counts[sanatci]["ilk_iso"] is None or iso < artist_counts[sanatci]["ilk_iso"]:
                        artist_counts[sanatci]["ilk_iso"] = iso
            if tarih:
                gun_sure[tarih] += sure
                try:
                    g, ay, yil = tarih.split(".")
                    ak = f"{yil}-{ay}"
                    ay_stats[ak]["sure"] += sure
                    ay_stats[ak]["kayit"] += 1
                    ay_stats[ak]["gunler"].add(tarih)
                except: pass

        bugun_date = datetime.now(timezone.utc).date()
        def calc_days(iso_str):
            if not iso_str: return None
            try: return max(0, (bugun_date - datetime.strptime(iso_str[:10], "%Y-%m-%d").date()).days)
            except: return None

        top_sarkilar = sorted([{"sarki": k, "sanatci": v["sanatci"], "count": v["count"], "sure": v["sure"], "kac_gundur": calc_days(v["ilk_iso"])} for k, v in track_counts.items()], key=lambda x: -x["count"])[:10]
        top_sanatcilar = sorted([{"sanatci": k, "count": v["count"], "sure": v["sure"], "kac_gundur": calc_days(v["ilk_iso"])} for k, v in artist_counts.items()], key=lambda x: -x["count"])[:10]
        
        hafta = []
        for i in range(6, -1, -1):
            gun = bugun_date - timedelta(days=i)
            ts = gun.strftime("%d.%m.%Y")
            hafta.append({"tarih": ts, "gun": TR_GUNLER[gun.weekday()], "sure_sn": gun_sure.get(ts, 0)})

        aylar = [{"ay": f"{TR_AYLAR[int(ak.split('-')[1])]} {ak.split('-')[0]}", "toplam": fmt_sure(st["sure"]), "ortalama": fmt_sure(st["sure"] // len(st["gunler"]) if len(st["gunler"]) else 0), "kayit_sayisi": st["kayit"]} for ak, st in sorted(ay_stats.items())]

        return jsonify({
            "toplam_kayit": len(rows), "farkli_sarki": len(track_counts), "farkli_sanatci": len(artist_counts),
            "toplam_sure_sn": toplam_sure, "son_sync": _last_sync,
            "ilk_kayit_tarihi": datetime.strptime(ilk_kayit_iso[:10], "%Y-%m-%d").strftime("%d.%m.%Y") if ilk_kayit_iso else "Bilinmiyor",
            "top_sarkilar": top_sarkilar, "top_sanatcilar": top_sanatcilar, "hafta": hafta, "aylar": aylar,
            "genel_saatler": [{"saat": f"{h:02d}:00", "count": global_saat_counts.get(h, 0)} for h in range(24)],
            "genel_vakitler": [{"vakit": k, "count": v} for k, v in sorted(global_vakit_counts.items(), key=lambda x: -x[1])]
        })
    except Exception as e:
        logger.error(f"Dashboard hatası: {e}")
        return jsonify({"error": str(e)}), 500

# ----- DETAY (MODAL) ROTALARI EKLENDİ -----

@app.route("/api/sarki/<path:sarki_adi>")
def api_sarki_detay(sarki_adi):
    if not session.get("logged_in"): return jsonify({"error": "Unauthorized"}), 401
    try:
        if not _cached_rows: load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        if not rows: return jsonify({"error": "Veri yok"})

        idx_sarki = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure = headers.index("Süre (sn)")
        idx_iso = headers.index("_played_at_iso")

        saat_counts = defaultdict(int)
        vakit_counts = defaultdict(int)
        toplam_count, toplam_sure = 0, 0
        sanatci, ilk_dinlenme_iso = "", None 

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_iso): continue
            if row[idx_sarki].strip() != sarki_adi: continue
            toplam_count += 1
            sanatci = row[idx_sanatci].strip()
            try: toplam_sure += int(row[idx_sure])
            except: pass

            iso = row[idx_iso].strip()
            if iso and iso != "—":
                if ilk_dinlenme_iso is None or iso < ilk_dinlenme_iso: ilk_dinlenme_iso = iso
                try:
                    dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                    saat_counts[dt.hour] += 1
                    vakit_counts[get_vakit(dt.hour)] += 1
                except: pass

        ilk_tarih_str = "Bilinmiyor"
        if ilk_dinlenme_iso:
            try: ilk_tarih_str = datetime.strptime(ilk_dinlenme_iso[:16], "%Y-%m-%dT%H:%M").strftime("%d.%m.%Y")
            except: pass

        return jsonify({
            "sarki": sarki_adi, "sanatci": sanatci, "toplam_count": toplam_count,
            "toplam_sure": fmt_sure(toplam_sure), "ilk_dinlenme": ilk_tarih_str, 
            "saatler": [{"saat": f"{h:02d}:00", "count": saat_counts.get(h, 0)} for h in range(24)],
            "vakitler": [{"vakit": k, "count": v} for k, v in sorted(vakit_counts.items(), key=lambda x: -x[1])],
        })
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/sanatci/<path:sanatci_adi>")
def api_sanatci_detay(sanatci_adi):
    if not session.get("logged_in"): return jsonify({"error": "Unauthorized"}), 401
    try:
        if not _cached_rows: load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        if not rows: return jsonify({"error": "Veri yok"})

        idx_sarki, idx_sanatci, idx_sure, idx_iso = headers.index("Şarkı Adı"), headers.index("Sanatçı"), headers.index("Süre (sn)"), headers.index("_played_at_iso")
        sarki_counts = defaultdict(lambda: {"count": 0, "sure": 0})
        saat_counts, vakit_counts = defaultdict(int), defaultdict(int)
        toplam_count, toplam_sure = 0, 0

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_iso): continue
            if row[idx_sanatci].strip() != sanatci_adi: continue
            toplam_count += 1
            sarki = row[idx_sarki].strip()
            try:
                sure = int(row[idx_sure])
                toplam_sure += sure
            except: sure = 0

            if sarki:
                sarki_counts[sarki]["count"] += 1
                sarki_counts[sarki]["sure"] += sure

            iso = row[idx_iso].strip()
            if iso and iso != "—":
                try:
                    dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                    saat_counts[dt.hour] += 1
                    vakit_counts[get_vakit(dt.hour)] += 1
                except: pass

        top_sarkilar = sorted([{"sarki": k, "count": v["count"], "sure": fmt_sure(v["sure"])} for k, v in sarki_counts.items()], key=lambda x: -x["count"])[:10]
        return jsonify({
            "sanatci": sanatci_adi, "toplam_count": toplam_count, "toplam_sure": fmt_sure(toplam_sure),
            "top_sarkilar": top_sarkilar,
            "saatler": [{"saat": f"{h:02d}:00", "count": saat_counts.get(h, 0)} for h in range(24)],
            "vakitler": [{"vakit": k, "count": v} for k, v in sorted(vakit_counts.items(), key=lambda x: -x[1])],
        })
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/api/tum-sanatcilar')
def api_tum_sanatcilar():
    if not session.get("logged_in"): return jsonify({"error": "Unauthorized"}), 401
    try:
        if not _cached_rows: load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        if not rows: return jsonify({"error": "Veri yok"})

        idx_sanatci, idx_sure = headers.index("Sanatçı"), headers.index("Süre (sn)")
        artist_counts = defaultdict(lambda: {"count": 0, "sure": 0})
        for row in rows:
            if len(row) <= max(idx_sanatci, idx_sure): continue
            sanatci = row[idx_sanatci].strip()
            if not sanatci: continue
            artist_counts[sanatci]["count"] += 1
            try: artist_counts[sanatci]["sure"] += int(row[idx_sure])
            except: pass

        sanatcilar = sorted([{"sanatci": k, "count": v["count"], "sure": fmt_sure(v["sure"])} for k, v in artist_counts.items()], key=lambda x: -x["count"])
        return jsonify({"sanatcilar": sanatcilar, "toplam": len(sanatcilar)})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/api/tum-sarkilar')
def api_tum_sarkilar():
    if not session.get("logged_in"): return jsonify({"error": "Unauthorized"}), 401
    try:
        if not _cached_rows: load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        if not rows: return jsonify({"error": "Veri yok"})

        idx_sarki, idx_sanatci, idx_sure = headers.index('Şarkı Adı'), headers.index('Sanatçı'), headers.index('Süre (sn)')
        track_counts = defaultdict(lambda: {"count": 0, "sure": 0, "sanatci": ""})
        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure): continue
            sarki = row[idx_sarki].strip()
            if not sarki: continue
            track_counts[sarki]["count"] += 1
            track_counts[sarki]["sanatci"] = row[idx_sanatci].strip()
            try: track_counts[sarki]["sure"] += int(row[idx_sure])
            except: pass

        sarkilar = sorted([{"sarki": k, "sanatci": v["sanatci"], "count": v["count"], "sure": fmt_sure(v["sure"])} for k, v in track_counts.items()], key=lambda x: -x["count"])
        return jsonify({"sarkilar": sarkilar, "toplam": len(sarkilar)})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/ay/<ay_label>")
def api_ay_detay(ay_label):
    if not session.get("logged_in"): return jsonify({"error": "Unauthorized"}), 401
    try:
        if not _cached_rows: load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        if not rows: return jsonify({"error": "Veri yok"})

        idx_sarki, idx_sanatci, idx_sure, idx_tarih = headers.index("Şarkı Adı"), headers.index("Sanatçı"), headers.index("Süre (sn)"), headers.index("Dinlenme Tarihi")
        TR_AYLAR_REV = {v: str(k).zfill(2) for k, v in TR_AYLAR.items()}
        try: ay_tr, yil = ay_label.split(" ")
        except: return jsonify({"error": "Geçersiz ay formatı"})
        ay_no = TR_AYLAR_REV.get(ay_tr)
        
        track_counts = defaultdict(lambda: {"count": 0, "sure": 0, "sanatci": ""})
        artist_counts = defaultdict(lambda: {"count": 0, "sure": 0})
        toplam_kayit, toplam_sure = 0, 0

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_tarih): continue
            tarih = row[idx_tarih].strip()
            if not tarih: continue
            try:
                _, ay, y = tarih.split(".")
                if y != yil or ay != ay_no: continue
            except: continue

            sarki, sanatci = row[idx_sarki].strip(), row[idx_sanatci].strip()
            try: sure = int(row[idx_sure])
            except: sure = 0

            toplam_kayit += 1
            toplam_sure += sure
            if sarki:
                track_counts[sarki]["count"] += 1
                track_counts[sarki]["sure"] += sure
                track_counts[sarki]["sanatci"] = sanatci
            if sanatci:
                artist_counts[sanatci]["count"] += 1
                artist_counts[sanatci]["sure"] += sure

        top_sarkilar = sorted([{"sarki": k, "sanatci": v["sanatci"], "count": v["count"], "sure": fmt_sure(v["sure"])} for k, v in track_counts.items()], key=lambda x: -x["count"])[:10]
        top_sanatcilar = sorted([{"sanatci": k, "count": v["count"], "sure": fmt_sure(v["sure"])} for k, v in artist_counts.items()], key=lambda x: -x["count"])[:10]

        return jsonify({
            "ay": ay_label, "toplam_kayit": toplam_kayit, "toplam_sure": fmt_sure(toplam_sure),
            "top_sarkilar": top_sarkilar, "top_sanatcilar": top_sanatcilar,
        })
    except Exception as e: return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(sync_job, "cron", minute="0,30", id="spotify_sync")
    scheduler.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
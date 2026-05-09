import os
import io
import csv
import random
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

# ----- YARDIMCI FONKSİYON -----
def get_redirect_uri():
    # Vercel için en güvenilir redirect link üreticisi
    host = request.headers.get('Host', request.host)
    if "localhost" in host or "127.0.0.1" in host:
        return f"http://{host}/callback"
    else:
        return f"https://{host}/callback"

# ----- AUTH ROTALARI -----

@app.route("/login")
def login():
    redirect_uri = get_redirect_uri()
    auth_url = spotify.get_auth_url(redirect_uri)
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if code:
        try:
            redirect_uri = get_redirect_uri()
            spotify.exchange_code(code, redirect_uri)
            session["logged_in"] = True
            logger.info("✅ Kullanıcı başarıyla giriş yaptı.")
        except Exception as e:
            logger.error(f"Login (Callback) Hatası: {e}")
    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    logger.info("Kullanıcı çıkış yaptı.")
    return redirect("/")

# ----- TEMEL ROTALAR -----

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

# ----- DÜZENLE & SPOTIFY ACTIONS -----

@app.route("/api/playlists")
def get_playlists():
    if not session.get("logged_in"): return jsonify({"error": "Unauthorized"}), 401
    try:
        pls = spotify.get_my_playlists()
        return jsonify(pls)
    except Exception as e:
        logger.error(f"Playlist çekme hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/action/create_playlist", methods=["POST"])
def create_playlist_action():
    if not session.get("logged_in"): return jsonify({"error": "Unauthorized"}), 401
    req = request.json
    ptype = req.get("type")
    
    headers, rows = load_tumveri()
    if not rows: return jsonify({"error": "Veri yok"}), 400
    idx_id = headers.index("Şarkı ID")
    
    track_counts = defaultdict(int)
    for r in rows:
        if len(r) > idx_id and r[idx_id].strip() != "—":
            track_counts[r[idx_id].strip()] += 1
            
    sorted_ids = [k for k, v in sorted(track_counts.items(), key=lambda x: -x[1])]

    try:
        if ptype == "top_tracks":
            pl = spotify.create_playlist("Top 50 - En Çok Dinlenenler")
            spotify.add_to_playlist(pl["id"], sorted_ids[:50])
            return jsonify({"message": "Playlist başarıyla oluşturuldu!"})
            
        elif ptype == "energy":
            top_100 = sorted_ids[:100]
            features = spotify.get_audio_features(top_100)
            energy_sorted = sorted([tid for tid in top_100 if tid in features], key=lambda x: features[x]["energy"], reverse=True)
            pl = spotify.create_playlist("Yüksek Enerji (Kişisel)")
            spotify.add_to_playlist(pl["id"], energy_sorted)
            return jsonify({"message": "Enerji playlisti başarıyla oluşturuldu!"})
            
        return jsonify({"error": "Bilinmeyen tip"}), 400
    except Exception as e:
        logger.error(f"Playlist oluşturma hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/action/playlist_edit", methods=["POST"])
def edit_playlist_action():
    if not session.get("logged_in"): return jsonify({"error": "Unauthorized"}), 401
    req = request.json
    action = req.get("action")
    pl_id = req.get("playlist_id")
    
    try:
        tracks = spotify.get_playlist_tracks(pl_id)
        t_ids = [t["id"] for t in tracks if t and t.get("id")]
        
        if action == "random_shuffle":
            random.shuffle(t_ids)
            spotify.replace_playlist_tracks(pl_id, t_ids)
            return jsonify({"message": "Playlist rastgele karıştırıldı!"})
            
        elif action == "energy_shuffle":
            features = spotify.get_audio_features(t_ids)
            energy_sorted = sorted([tid for tid in t_ids if tid in features], key=lambda x: features[x]["energy"], reverse=True)
            spotify.replace_playlist_tracks(pl_id, energy_sorted)
            return jsonify({"message": "Playlist enerjiye göre sıralandı!"})
            
        elif action == "follow_artists":
            artist_ids = [a["id"] for t in tracks for a in t.get("artists", []) if a.get("id")]
            spotify.modify_following("PUT", artist_ids)
            return jsonify({"message": f"{len(set(artist_ids))} sanatçı takip edildi!"})
            
        elif action == "unfollow_artists":
            artist_ids = [a["id"] for t in tracks for a in t.get("artists", []) if a.get("id")]
            spotify.modify_following("DELETE", artist_ids)
            return jsonify({"message": "Sanatçılar takipten çıkıldı!"})
            
        elif action == "like_all":
            spotify.modify_saved_tracks("PUT", t_ids)
            return jsonify({"message": "Tüm şarkılar beğenildi!"})
            
        elif action == "unlike_all":
            spotify.modify_saved_tracks("DELETE", t_ids)
            return jsonify({"message": "Tüm beğeniler kaldırıldı!"})
            
        elif action == "remove_liked":
            saved_status = spotify.check_saved_tracks(t_ids)
            keep_ids = [tid for tid in t_ids if not saved_status.get(tid, False)]
            spotify.replace_playlist_tracks(pl_id, keep_ids)
            return jsonify({"message": "Beğenilen şarkılar playlistten çıkarıldı!"})
            
        elif action == "remove_unliked":
            saved_status = spotify.check_saved_tracks(t_ids)
            keep_ids = [tid for tid in t_ids if saved_status.get(tid, False)]
            spotify.replace_playlist_tracks(pl_id, keep_ids)
            return jsonify({"message": "Beğenilmeyen şarkılar playlistten çıkarıldı!"})

        return jsonify({"error": "Geçersiz işlem"}), 400
    except Exception as e:
        logger.error(f"Playlist düzenleme hatası: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(sync_job, "cron", minute="0,30", id="spotify_sync")
    scheduler.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
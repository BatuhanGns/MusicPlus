import os
import io
import csv
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from flask import Flask, jsonify, render_template, request, Response, stream_with_context, session, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from spotify_client import SpotifyClient
from sheets_client import SheetsClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

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

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
spotify = SpotifyClient()
sheets  = SheetsClient()

def get_current_user_id():
    """Session'dan mevcut kullanıcının Spotify ID'sini döndürür"""
    return session.get("user_id")

def get_current_user_name():
    return session.get("display_name", "Kullanıcı")

# Cache: her kullanıcı için ayrı
_user_cache = {}  # user_id -> {"headers": [], "rows": [], "last_sync": ""}

def load_user_data(user_id: str):
    """Kullanıcının verisini Sheets'ten yükler ve cache'ler"""
    headers, rows = sheets.get_user_data(user_id)
    _user_cache[user_id] = {"headers": headers, "rows": rows}
    return headers, rows

def get_cached_data(user_id: str):
    if user_id not in _user_cache:
        return load_user_data(user_id)
    return _user_cache[user_id]["headers"], _user_cache[user_id]["rows"]

# Geriye dönük uyumluluk için
def load_tumveri():
    uid = get_current_user_id()
    if uid:
        return load_user_data(uid)
    return [], []

_last_sync = "Henüz sync yapılmadı"
_cached_rows = []
_cached_headers = []

def sync_job(user_id: str = None, refresh_token: str = None):
    global _last_sync
    uid = user_id or get_current_user_id()
    if not uid:
        logger.warning("⚠️ Sync: user_id yok, atlanıyor")
        return

    # Token varsa spotify client'a yükle
    if refresh_token:
        spotify.refresh_token = refresh_token
    elif not spotify.refresh_token:
        spotify.refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN", "")

    if not spotify.refresh_token:
        logger.warning(f"⚠️ Sync: {uid} için refresh_token yok, atlanıyor")
        return

    logger.info(f"🎵 Sync başladı: {uid}")
    try:
        tracks = spotify.get_recently_played()
        if tracks:
            new_count = sheets.append_tracks(uid, tracks)
            logger.info(f"✅ {new_count} yeni kayıt eklendi ({uid})")
        else:
            logger.info("Yeni dinleme yok.")
        sheets.update_last_sync(uid)
        _last_sync = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M") + " UTC"
        load_user_data(uid)
        logger.info(f"📊 Sync tamamlandı: {uid}")
    except Exception as e:
        logger.error(f"❌ Sync hatası ({uid}): {e}")

@app.route("/")
@app.route("/dashboard")
def dashboard():
    uid           = session.get("user_id")
    refresh_token = session.get("refresh_token")

    if not uid or not refresh_token:
        return redirect("/login")

    # Render restart sonrası spotify client'ın token'ını session'dan geri yükle
    if not spotify.refresh_token and refresh_token:
        spotify.refresh_token = refresh_token
        logger.info(f"🔄 Token session'dan geri yüklendi: {uid}")

    # Veri cache'de yoksa yükle
    if uid not in _user_cache:
        try:
            load_user_data(uid)
            sync_job(uid)
        except Exception as e:
            logger.warning(f"⚠️ Auto-sync hatası: {e}")

    return render_template("dashboard.html")

@app.route("/api/export-csv")
def export_csv():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    headers, rows = get_cached_data(uid)
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
    return Response(stream_with_context(generate()), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=spotify_verilerim.csv"})

def compute_stats(headers, rows):
    """Verilen satırlardan istatistik hesaplar — hem kişisel hem birleşik için kullanılır"""
    if not rows:
        return None

    idx_sarki   = headers.index("Şarkı Adı")
    idx_sanatci = headers.index("Sanatçı")
    idx_sure    = headers.index("Süre (sn)")
    idx_tarih   = headers.index("Dinlenme Tarihi")
    idx_iso     = headers.index("_played_at_iso") if "_played_at_iso" in headers else -1

    track_counts  = defaultdict(lambda: {"count": 0, "sanatci": "", "sure": 0, "ilk_iso": None})
    artist_counts = defaultdict(lambda: {"count": 0, "sure": 0, "ilk_iso": None})
    gun_sure      = defaultdict(int)
    ay_stats      = defaultdict(lambda: {"sure": 0, "kayit": 0, "gunler": set()})
    toplam_sure   = 0
    global_saat_counts  = defaultdict(int)
    global_vakit_counts = defaultdict(int)
    ilk_kayit_iso = None

    bugun_date = datetime.now(timezone.utc).date()

    for row in rows:
        if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_tarih):
            continue
        sarki   = row[idx_sarki].strip()
        sanatci = row[idx_sanatci].strip()
        tarih   = row[idx_tarih].strip()
        try: sure = int(row[idx_sure])
        except: sure = 0

        iso = row[idx_iso].strip() if idx_iso != -1 and len(row) > idx_iso else ""
        toplam_sure += sure

        if iso and iso != "—":
            try:
                dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                global_saat_counts[dt.hour] += 1
                global_vakit_counts[get_vakit(dt.hour)] += 1
                if ilk_kayit_iso is None or iso < ilk_kayit_iso:
                    ilk_kayit_iso = iso
            except: pass

        if sarki:
            track_counts[sarki]["count"]  += 1
            track_counts[sarki]["sure"]   += sure
            track_counts[sarki]["sanatci"] = sanatci
            if iso and iso != "—":
                if track_counts[sarki]["ilk_iso"] is None or iso < track_counts[sarki]["ilk_iso"]:
                    track_counts[sarki]["ilk_iso"] = iso

        if sanatci:
            artist_counts[sanatci]["count"] += 1
            artist_counts[sanatci]["sure"]  += sure
            if iso and iso != "—":
                if artist_counts[sanatci]["ilk_iso"] is None or iso < artist_counts[sanatci]["ilk_iso"]:
                    artist_counts[sanatci]["ilk_iso"] = iso

        if tarih:
            gun_sure[tarih] += sure
            try:
                g, ay, yil = tarih.split(".")
                ak = f"{yil}-{ay}"
                ay_stats[ak]["sure"]  += sure
                ay_stats[ak]["kayit"] += 1
                ay_stats[ak]["gunler"].add(tarih)
            except: pass

    def calc_days(iso_str):
        if not iso_str: return None
        try:
            dt = datetime.strptime(iso_str[:10], "%Y-%m-%d").date()
            return max(0, (bugun_date - dt).days)
        except: return None

    top_sarkilar = sorted(
        [{"sarki": k, "sanatci": v["sanatci"], "count": v["count"], "sure": v["sure"], "kac_gundur": calc_days(v["ilk_iso"])}
         for k, v in track_counts.items()],
        key=lambda x: -x["count"]
    )[:10]

    top_sanatcilar = sorted(
        [{"sanatci": k, "count": v["count"], "sure": v["sure"], "kac_gundur": calc_days(v["ilk_iso"])}
         for k, v in artist_counts.items()],
        key=lambda x: -x["count"]
    )[:10]

    hafta = []
    for i in range(6, -1, -1):
        gun = bugun_date - timedelta(days=i)
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

    ilk_tarih_str = "Bilinmiyor"
    if ilk_kayit_iso:
        try: ilk_tarih_str = datetime.strptime(ilk_kayit_iso[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
        except: pass

    return {
        "toplam_kayit":   len(rows),
        "farkli_sarki":   len(track_counts),
        "farkli_sanatci": len(artist_counts),
        "toplam_sure_sn": toplam_sure,
        "ilk_kayit_tarihi": ilk_tarih_str,
        "top_sarkilar":   top_sarkilar,
        "top_sanatcilar": top_sanatcilar,
        "hafta":          hafta,
        "aylar":          aylar,
        "genel_saatler":  [{"saat": f"{h:02d}:00", "count": global_saat_counts.get(h, 0)} for h in range(24)],
        "genel_vakitler": [{"vakit": k, "count": v} for k, v in sorted(global_vakit_counts.items(), key=lambda x: -x[1])],
    }

@app.route("/api/dashboard")
def api_dashboard():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            load_user_data(uid)
            headers, rows = get_cached_data(uid)
        stats = compute_stats(headers, rows)
        if not stats:
            return jsonify({"error": "Veri yok"})
        stats["son_sync"] = _last_sync
        return jsonify(stats)
    except Exception as e:
        logger.error(f"❌ Dashboard API hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/istatistikler")
def api_istatistikler():
    """Tüm izin veren kullanıcıların birleşik istatistiği"""
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        permitted = sheets.get_all_permitted_users()
        if not permitted:
            return jsonify({"error": "Henüz kimse izin vermemiş"})
        headers, rows = sheets.get_combined_data(permitted)
        stats = compute_stats(headers, rows)
        if not stats:
            return jsonify({"error": "Veri yok"})
        stats["katilimci_sayisi"] = len(permitted)
        stats["son_sync"] = _last_sync
        return jsonify(stats)
    except Exception as e:
        logger.error(f"❌ İstatistikler API hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/izin", methods=["POST"])
def api_izin():
    """Kullanıcının istatistikler sayfası iznini günceller"""
    try:
        uid  = get_current_user_id()
        name = get_current_user_name()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        data    = request.get_json()
        allowed = bool(data.get("allowed", False))
        sheets.set_user_permission(uid, name, allowed)
        return jsonify({"status": "ok", "allowed": allowed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/izin")
def api_izin_get():
    """Mevcut kullanıcının izin durumunu döndürür"""
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"allowed": False})
        return jsonify({"allowed": sheets.get_user_permission(uid)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/sarki/<path:sarki_adi>")
def api_sarki_detay(sarki_adi):
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
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
        
        ilk_dinlenme_iso = None 

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
                if ilk_dinlenme_iso is None or iso < ilk_dinlenme_iso:
                    ilk_dinlenme_iso = iso

                try:
                    dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                    saat_counts[dt.hour] += 1
                    vakit_counts[get_vakit(dt.hour)] += 1
                except:
                    pass

        ilk_tarih_str = "Bilinmiyor"
        if ilk_dinlenme_iso:
            try:
                dt = datetime.strptime(ilk_dinlenme_iso[:16], "%Y-%m-%dT%H:%M")
                ilk_tarih_str = dt.strftime("%d.%m.%Y")
            except:
                pass

        saatler = [{"saat": f"{h:02d}:00", "count": saat_counts.get(h, 0)} for h in range(24)]
        vakitler = [{"vakit": k, "count": v} for k, v in sorted(vakit_counts.items(), key=lambda x: -x[1])]

        return jsonify({
            "sarki":        sarki_adi,
            "sanatci":      sanatci,
            "toplam_count": toplam_count,
            "toplam_sure":  fmt_sure(toplam_sure),
            "ilk_dinlenme": ilk_tarih_str, 
            "saatler":      saatler,
            "vakitler":     vakitler,
        })
    except Exception as e:
        logger.error(f"❌ Şarkı detay hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/sanatci/<path:sanatci_adi>")
def api_sanatci_detay(sanatci_adi):
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
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

@app.route('/api/tum-sanatcilar')
def api_tum_sanatcilar():
    try:
        if not _cached_rows:
            load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sanatci = headers.index("Sanatçı")
        idx_sure    = headers.index("Süre (sn)")

        artist_counts = defaultdict(lambda: {"count": 0, "sure": 0})
        for row in rows:
            if len(row) <= max(idx_sanatci, idx_sure):
                continue
            sanatci = row[idx_sanatci].strip()
            if not sanatci:
                continue
            artist_counts[sanatci]["count"] += 1
            try:
                artist_counts[sanatci]["sure"] += int(row[idx_sure])
            except:
                pass

        sanatcilar = sorted(
            [{"sanatci": k, "count": v["count"], "sure": fmt_sure(v["sure"])}
             for k, v in artist_counts.items()],
            key=lambda x: -x["count"]
        )
        return jsonify({"sanatcilar": sanatcilar, "toplam": len(sanatcilar)})
    except Exception as e:
        logger.error(f"❌ Tüm sanatçılar hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tum-sarkilar')
def api_tum_sarkilar():
    try:
        if not _cached_rows:
            load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki   = headers.index('Şarkı Adı')
        idx_sanatci = headers.index('Sanatçı')
        idx_sure    = headers.index('Süre (sn)')

        track_counts = defaultdict(lambda: {"count": 0, "sure": 0, "sanatci": ""})
        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure):
                continue
            sarki = row[idx_sarki].strip()
            if not sarki:
                continue
            track_counts[sarki]["count"] += 1
            track_counts[sarki]["sanatci"] = row[idx_sanatci].strip()
            try:
                track_counts[sarki]["sure"] += int(row[idx_sure])
            except:
                pass

        sarkilar = sorted(
            [{"sarki": k, "sanatci": v["sanatci"], "count": v["count"], "sure": fmt_sure(v["sure"])}
             for k, v in track_counts.items()],
            key=lambda x: -x["count"]
        )
        return jsonify({"sarkilar": sarkilar, "toplam": len(sarkilar)})
    except Exception as e:
        logger.error(f"❌ Tüm şarkılar hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/ay/<ay_label>")
def api_ay_detay(ay_label):
    try:
        if not _cached_rows:
            load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki   = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure    = headers.index("Süre (sn)")
        idx_tarih   = headers.index("Dinlenme Tarihi")

        TR_AYLAR_REV = {v: str(k).zfill(2) for k, v in TR_AYLAR.items()}
        parca = ay_label.split(" ")
        if len(parca) != 2:
            return jsonify({"error": "Geçersiz ay formatı"})
        ay_tr, yil = parca
        ay_no = TR_AYLAR_REV.get(ay_tr)
        if not ay_no:
            return jsonify({"error": "Ay bulunamadı"})
        
        track_counts  = defaultdict(lambda: {"count": 0, "sure": 0, "sanatci": ""})
        artist_counts = defaultdict(lambda: {"count": 0, "sure": 0})
        toplam_kayit  = 0
        toplam_sure   = 0

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_tarih):
                continue
            tarih = row[idx_tarih].strip()
            if not tarih:
                continue
            try:
                g, ay, y = tarih.split(".")
                if y != yil or ay != ay_no:
                    continue
            except:
                continue

            sarki   = row[idx_sarki].strip()
            sanatci = row[idx_sanatci].strip()
            try:
                sure = int(row[idx_sure])
            except:
                sure = 0

            toplam_kayit += 1
            toplam_sure  += sure

            if sarki:
                track_counts[sarki]["count"]  += 1
                track_counts[sarki]["sure"]   += sure
                track_counts[sarki]["sanatci"] = sanatci
            if sanatci:
                artist_counts[sanatci]["count"] += 1
                artist_counts[sanatci]["sure"]  += sure

        top_sarkilar = sorted(
            [{"sarki": k, "sanatci": v["sanatci"], "count": v["count"], "sure": fmt_sure(v["sure"])}
             for k, v in track_counts.items()],
            key=lambda x: -x["count"]
        )[:10]

        top_sanatcilar = sorted(
            [{"sanatci": k, "count": v["count"], "sure": fmt_sure(v["sure"])}
             for k, v in artist_counts.items()],
            key=lambda x: -x["count"]
        )[:10]

        return jsonify({
            "ay":           ay_label,
            "toplam_kayit": toplam_kayit,
            "toplam_sure":  fmt_sure(toplam_sure),
            "top_sarkilar": top_sarkilar,
            "top_sanatcilar": top_sanatcilar,
        })
    except Exception as e:
        logger.error(f"❌ Ay detay hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/logout")
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/login")
def login_page():
    # Render gibi proxy arkasındaki sunucularda request.host_url http:// döndürebilir.
    # REDIRECT_URI env variable varsa onu kullan, yoksa https:// olarak zorla.
    base = os.environ.get("REDIRECT_URI") or (
        "https://" + request.host + "/callback"
    )
    redirect_uri = base.rstrip("/")
    if not redirect_uri.endswith("/callback"):
        redirect_uri += "/callback"
    auth_url = spotify.get_auth_url(redirect_uri)
    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Giriş Yap – Müzik İstatistiklerin</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=DM+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<style>
  :root{{--bg:#0a0a0a;--surface:#111;--border:#222;--green:#1db954;--text:#e8e8e8;--muted:#555;}}
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:var(--bg);color:var(--text);font-family:'DM Mono',monospace;min-height:100vh;display:flex;align-items:center;justify-content:center;}}
  body::before{{content:'';position:fixed;inset:0;background-image:linear-gradient(var(--border) 1px,transparent 1px),linear-gradient(90deg,var(--border) 1px,transparent 1px);background-size:48px 48px;opacity:.3;pointer-events:none;}}
  .card{{position:relative;z-index:1;background:var(--surface);border:1px solid var(--border);padding:56px 48px;max-width:420px;width:100%;text-align:center;}}
  h1{{font-family:'Syne',sans-serif;font-size:36px;font-weight:800;letter-spacing:-1px;margin-bottom:8px;}}
  h1 span{{color:var(--green);}}
  p{{font-size:12px;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-bottom:40px;}}
  a.btn{{display:block;background:var(--green);color:#000;font-family:'Syne',sans-serif;font-size:14px;font-weight:700;letter-spacing:1px;text-transform:uppercase;text-decoration:none;padding:18px 32px;transition:opacity .2s;}}
  a.btn:hover{{opacity:.85;}}
  .note{{margin-top:20px;font-size:11px;color:var(--muted);line-height:1.7;}}
</style>
</head>
<body>
<div class="card">
  <h1>MÜZİK<span>.</span></h1>
  <p>Kişisel Spotify İstatistiklerin</p>
  <a class="btn" href="{auth_url}">Spotify ile Giriş Yap</a>
  <div class="note">Bu uygulama yalnızca dinleme verilerini okur.<br>Hiçbir verin üçüncü taraflarla paylaşılmaz.</div>
</div>
</body>
</html>"""

@app.route("/callback")
def callback():
    code = request.args.get("code")
    error = request.args.get("error")
    if error or not code:
        return redirect("/login")
    base = os.environ.get("REDIRECT_URI") or (
        "https://" + request.host + "/callback"
    )
    redirect_uri = base.rstrip("/")
    if not redirect_uri.endswith("/callback"):
        redirect_uri += "/callback"
    try:
        spotify.exchange_code(code, redirect_uri)
        session.permanent = True  # 30 gün hatırla
        me = spotify._req("GET", "/me")
        session["user_id"]       = me.get("id", "")
        session["display_name"]  = me.get("display_name", me.get("id", "Kullanıcı"))
        session["refresh_token"] = spotify.refresh_token
        uid   = session["user_id"]
        name  = session["display_name"]
        token = spotify.refresh_token
        if uid:
            if not sheets._find_sheet(uid):
                sheets._ensure_user_sheet(uid)
                sheets.set_user_permission(uid, name, False, token)
            else:
                sheets.save_refresh_token(uid, token)
        return redirect("/")
    except Exception as e:
        logger.error(f"❌ OAuth callback hatası: {e}")
        return redirect("/login")

@app.route("/api/sync")
@app.route("/sync")
def manual_sync():
    sync_job()
    return jsonify({"status": "ok", "message": "Manuel sync tamamlandı", "son_sync": _last_sync})

@app.route("/api/health")
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "cached_rows": len(_cached_rows),
        "son_sync": _last_sync
    })

@app.route("/api/debug")
def debug():
    import traceback
    try:
        headers, rows = load_tumveri()
        return jsonify({
            "status": "ok",
            "cached_rows": len(_cached_rows),
            "cached_headers": _cached_headers,
            "son_sync": _last_sync,
            "env_vars": {
                "SPOTIFY_CLIENT_ID": bool(os.environ.get("SPOTIFY_CLIENT_ID")),
                "SPOTIFY_CLIENT_SECRET": bool(os.environ.get("SPOTIFY_CLIENT_SECRET")),
                "SPOTIFY_REFRESH_TOKEN": bool(os.environ.get("SPOTIFY_REFRESH_TOKEN")),
                "GOOGLE_SHEETS_ID": bool(os.environ.get("GOOGLE_SHEETS_ID")),
            }
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

# --- Playlist API endpoints ---

@app.route("/api/now-playing")
def api_now_playing():
    try:
        data = spotify.get_now_playing()
        return jsonify(data)
    except Exception as e:
        logger.error(f"❌ Now playing hatası: {e}")
        return jsonify({"playing": False}), 200

@app.route("/api/playlists")
def api_playlists():
    try:
        playlists = spotify.get_playlists()
        return jsonify({"playlists": playlists})
    except Exception as e:
        logger.error(f"❌ Playlist hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/create-top-tracks", methods=["POST"])
def api_create_top_tracks_playlist():
    try:
        if not _cached_rows:
            load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        idx_sarki    = headers.index("Şarkı Adı")
        idx_sarki_id = headers.index("Şarkı ID")

        from collections import Counter
        # Şarkı adına göre say, ama ID'yi de tut
        sarki_id_map = {}  # sarki_adi -> track_id
        sarki_counts = Counter()
        for row in rows:
            if len(row) > max(idx_sarki, idx_sarki_id):
                sarki = row[idx_sarki].strip()
                sid   = row[idx_sarki_id].strip()
                if sarki and sid:
                    sarki_counts[sarki] += 1
                    sarki_id_map[sarki] = sid

        top_sarkilar_ids = [
            f"spotify:track:{sarki_id_map[s]}"
            for s, _ in sarki_counts.most_common(50)
            if s in sarki_id_map
        ]

        user_id = spotify._get_user_id()
        pl = spotify._req("POST", f"/users/{user_id}/playlists", json={
            "name": "En Çok Dinlediklerim",
            "public": False,
            "description": "Spotify İstatistik uygulaması tarafından oluşturuldu"
        })
        playlist_id = pl["id"]
        for i in range(0, len(top_sarkilar_ids), 100):
            spotify._req("POST", f"/playlists/{playlist_id}/items", json={
                "uris": top_sarkilar_ids[i:i+100]
            })

        return jsonify({"status": "ok", "playlist_id": playlist_id, "track_count": len(top_sarkilar_ids)})
    except Exception as e:
        logger.error(f"❌ Playlist oluşturma hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/create-top-artists", methods=["POST"])
def api_create_top_artists_playlist():
    try:
        if not _cached_rows:
            load_tumveri()
        headers, rows = _cached_headers, _cached_rows
        idx_sanatci  = headers.index("Sanatçı")
        idx_sarki    = headers.index("Şarkı Adı")
        idx_sarki_id = headers.index("Şarkı ID")

        from collections import Counter, defaultdict
        sanatci_counts   = Counter()
        sanatci_sarkilar = defaultdict(dict)  # sanatci -> {sarki_adi: track_id}

        for row in rows:
            if len(row) > max(idx_sanatci, idx_sarki, idx_sarki_id):
                s   = row[idx_sanatci].strip()
                t   = row[idx_sarki].strip()
                tid = row[idx_sarki_id].strip()
                if s: sanatci_counts[s] += 1
                if s and t and tid:
                    sanatci_sarkilar[s][t] = tid

        top_sanatcilar = [s for s, _ in sanatci_counts.most_common(20) if s]
        track_uris = []
        for s in top_sanatcilar:
            # Her sanatçıdan en fazla 5 şarkı al
            for tid in list(sanatci_sarkilar[s].values())[:5]:
                uri = f"spotify:track:{tid}"
                if uri not in track_uris:
                    track_uris.append(uri)

        track_uris = track_uris[:50]

        user_id = spotify._get_user_id()
        pl = spotify._req("POST", f"/users/{user_id}/playlists", json={
            "name": "En Çok Dinlediğim Sanatçılar",
            "public": False,
            "description": "Spotify İstatistik uygulaması tarafından oluşturuldu"
        })
        playlist_id = pl["id"]
        for i in range(0, len(track_uris), 100):
            spotify._req("POST", f"/playlists/{playlist_id}/items", json={
                "uris": track_uris[i:i+100]
            })

        return jsonify({"status": "ok", "playlist_id": playlist_id, "track_count": len(track_uris)})
    except Exception as e:
        logger.error(f"❌ Sanatçı playlist hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/<playlist_id>/shuffle", methods=["POST"])
def api_playlist_shuffle(playlist_id):
    try:
        spotify.shuffle_playlist(playlist_id)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/<playlist_id>/follow-artists", methods=["POST"])
def api_follow_artists(playlist_id):
    try:
        count = spotify.follow_all_artists_in_playlist(playlist_id)
        return jsonify({"status": "ok", "followed": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/<playlist_id>/unfollow-artists", methods=["POST"])
def api_unfollow_artists(playlist_id):
    try:
        count = spotify.unfollow_all_artists_in_playlist(playlist_id)
        return jsonify({"status": "ok", "unfollowed": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/<playlist_id>/like-all", methods=["POST"])
def api_like_all(playlist_id):
    try:
        count = spotify.like_all_tracks_in_playlist(playlist_id)
        return jsonify({"status": "ok", "liked": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/<playlist_id>/unlike-all", methods=["POST"])
def api_unlike_all(playlist_id):
    try:
        count = spotify.unlike_all_tracks_in_playlist(playlist_id)
        return jsonify({"status": "ok", "unliked": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/<playlist_id>/remove-liked", methods=["POST"])
def api_remove_liked(playlist_id):
    try:
        count = spotify.remove_liked_tracks_from_playlist(playlist_id)
        return jsonify({"status": "ok", "removed": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/<playlist_id>/remove-unliked", methods=["POST"])
def api_remove_unliked(playlist_id):
    try:
        count = spotify.remove_unliked_tracks_from_playlist(playlist_id)
        return jsonify({"status": "ok", "removed": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def scheduled_sync_all():
    """Tüm kayıtlı kullanıcılar için sync yapar — token Sheets'ten alınır"""
    try:
        users = sheets.get_all_users_with_tokens()
        if not users:
            logger.info("⏰ Scheduled sync: kayıtlı kullanıcı yok")
            return
        for u in users:
            uid   = u["user_id"]
            token = u["refresh_token"]
            if not token:
                logger.warning(f"⚠️ Scheduled sync: {uid} için token yok, atlanıyor")
                continue
            try:
                sync_job(uid, refresh_token=token)
            except Exception as e:
                logger.error(f"❌ Scheduled sync hatası ({uid}): {e}")
    except Exception as e:
        logger.error(f"❌ Scheduled sync genel hata: {e}")

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_sync_all, "cron", minute="0,30", id="spotify_sync")
    scheduler.start()
    logger.info("⏰ Scheduler başlatıldı (her 30 dakika, tüm kullanıcılar)")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

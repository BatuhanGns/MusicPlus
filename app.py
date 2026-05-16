import os
import io
import csv
import logging
import time
import psutil
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from flask import Flask, jsonify, render_template, request, Response, stream_with_context, session, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from spotify_client import SpotifyClient
from sheets_client import SheetsClient
from gemini_client import GeminiClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SERVER_START_TIME = time.time()

# ── AI LIMIT VE UPTIMEROBOT ────────────────────────────────────────────────
AI_MAX_REQUESTS = 3000
ai_requests_used = 0
_ai_total_cache = {"value": 0, "ts": 0}  # Sheets toplamı için cache (60sn TTL)

_ur_cache = {"data": {"status": "Sorgulanıyor...", "uptime_ratio": "—"}, "last_fetch": 0}

def get_uptimerobot_data():
    global _ur_cache
    now = time.time()
    # 60 saniyelik önbellek
    if now - _ur_cache["last_fetch"] < 60:
        return _ur_cache["data"]

    ur_api_key = os.environ.get("UPTIMEROBOT_API_KEY", "")
    if not ur_api_key:
        return {"status": "API Key Bekleniyor", "uptime_ratio": "—"}

    try:
        resp = requests.post(
            "https://api.uptimerobot.com/v2/getMonitors",
            data={
                "api_key": ur_api_key,
                "format": "json",
                "response_times": 1,
                "response_times_limit": 24,
                "logs": 1,
                "logs_limit": 10,
                "custom_uptime_ratios": "1-7-30",
                "all_time_uptime_durations": 1,
            },
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("stat") == "ok" and data.get("monitors"):
                m = data["monitors"][0]
                s_code = m.get("status")

                # UptimeRobot status kodları: 0=Paused, 2=Up, 8=Seems Down, 9=Down
                if s_code == 2:
                    status_str = "AKTİF (UP)"
                elif s_code in [8, 9]:
                    status_str = "KAPALI (DOWN)"
                elif s_code == 0:
                    status_str = "DURAKLATILDI"
                else:
                    status_str = "BİLİNMİYOR"

                # ── Yanıt süreleri (son 24 ölçüm) ────────────────────────────
                rt_raw = m.get("response_times", [])
                response_times = []
                for rt in rt_raw:
                    response_times.append({
                        "value": rt.get("value", 0),
                        "datetime": rt.get("datetime", 0),
                    })

                # Ortalama yanıt süresi
                avg_response = 0
                if response_times:
                    avg_response = round(sum(r["value"] for r in response_times) / len(response_times))

                # ── Özel uptime oranları (1g / 7g / 30g) ─────────────────────
                custom_ratios_raw = m.get("custom_uptime_ratio", "")
                # "99.9-98.5-97.2" formatında gelir
                ratio_parts = str(custom_ratios_raw).split("-") if custom_ratios_raw else []
                uptime_1d  = ratio_parts[0] if len(ratio_parts) > 0 else "—"
                uptime_7d  = ratio_parts[1] if len(ratio_parts) > 1 else "—"
                uptime_30d = ratio_parts[2] if len(ratio_parts) > 2 else "—"

                # ── Toplam up/down süreleri ───────────────────────────────────
                durations = m.get("all_time_uptime_durations", {})
                total_up_sec   = int(durations.get("up_duration",   0))
                total_down_sec = int(durations.get("down_duration", 0))

                def _fmt_dur(secs):
                    if secs <= 0:
                        return "0dk"
                    d = secs // 86400
                    h = (secs % 86400) // 3600
                    mn = (secs % 3600) // 60
                    if d > 0:
                        return f"{d}g {h}s"
                    if h > 0:
                        return f"{h}s {mn}dk"
                    return f"{mn}dk"

                # ── Son kesinti logları ───────────────────────────────────────
                logs_raw = m.get("logs", [])
                logs = []
                for lg in logs_raw:
                    lg_type = lg.get("type")
                    # 1=Down, 2=Up, 98=Started, 99=Paused
                    if lg_type == 1:
                        lg_label = "⬇ Kesinti Başladı"
                        lg_color = "down"
                    elif lg_type == 2:
                        lg_label = "⬆ Geri Döndü"
                        lg_color = "up"
                    elif lg_type == 98:
                        lg_label = "▶ İzleme Başladı"
                        lg_color = "info"
                    else:
                        lg_label = "⏸ Duraklatıldı"
                        lg_color = "info"

                    dur_sec = int(lg.get("duration", 0))
                    logs.append({
                        "label":    lg_label,
                        "color":    lg_color,
                        "datetime": lg.get("datetime", 0),
                        "duration": _fmt_dur(dur_sec) if dur_sec > 0 else "",
                    })

                _ur_cache["data"] = {
                    "status":          status_str,
                    "uptime_ratio":    m.get("all_time_uptime_ratio", "—"),
                    "uptime_1d":       uptime_1d,
                    "uptime_7d":       uptime_7d,
                    "uptime_30d":      uptime_30d,
                    "avg_response_ms": avg_response,
                    "response_times":  response_times[-24:],   # en fazla 24 nokta
                    "total_up":        _fmt_dur(total_up_sec),
                    "total_down":      _fmt_dur(total_down_sec),
                    "logs":            logs[:10],
                    "monitor_name":    m.get("friendly_name", "Monitor"),
                    "check_interval":  m.get("interval", 300),
                }
                _ur_cache["last_fetch"] = now
    except Exception as e:
        logger.error(f"❌ UptimeRobot API hatası: {e}")

    return _ur_cache["data"]

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

import re as _re

def _extract_track_id(raw):
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("spotify:track:"):
        return raw.split(":")[-1]
    m = _re.search(r'/track/([A-Za-z0-9]+)', raw)
    if m:
        return m.group(1)
    if _re.match(r'^[A-Za-z0-9]{22}$', raw):
        return raw
    part = _re.split(r'[/:]', raw)[-1].split('?')[0].strip()
    if _re.match(r'^[A-Za-z0-9]{22}$', part):
        return part
    return None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "spotify-stats-2026-pkce-persistent-secret-key")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
spotify = SpotifyClient()
sheets  = SheetsClient()
gemini  = GeminiClient()

_ai_history: dict[str, list] = {}
AI_MAX_HISTORY = 20

def get_current_user_id():
    return session.get("user_id")

def get_current_user_name():
    return session.get("display_name", "Kullanıcı")

_user_cache = {}

def load_user_data(user_id: str):
    headers, rows = sheets.get_user_data(user_id)
    _user_cache[user_id] = {"headers": headers, "rows": rows}
    return headers, rows

def get_cached_data(user_id: str):
    if user_id not in _user_cache:
        return load_user_data(user_id)
    return _user_cache[user_id]["headers"], _user_cache[user_id]["rows"]

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

    if not spotify.refresh_token and refresh_token:
        spotify.refresh_token = refresh_token
        logger.info(f"🔄 Token session'dan geri yüklendi: {uid}")

    if uid not in _user_cache:
        try:
            load_user_data(uid)
            sync_job(uid)
        except Exception as e:
            logger.warning(f"⚠️ Auto-sync hatası: {e}")

    return render_template("dashboard.html")

@app.route("/api/system-stats")
def api_system_stats():
    global ai_requests_used
    try:
        # Sistem İstatistikleri
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        ram_percent = mem.percent
        ram_used_mb = mem.used / (1024 * 1024)
        ram_total_mb = mem.total / (1024 * 1024)
        net = psutil.net_io_counters()
        net_sent_mb = net.bytes_sent / (1024 * 1024)
        net_recv_mb = net.bytes_recv / (1024 * 1024)
        uptime_sec = int(time.time() - SERVER_START_TIME)
        uptime_hours = uptime_sec // 3600
        uptime_mins = (uptime_sec % 3600) // 60
        
        # Limit hesaplaması — Sheets'teki gerçek toplam (60sn cache)
        now_ts = time.time()
        if now_ts - _ai_total_cache["ts"] > 60:
            try:
                _ai_total_cache["value"] = sheets.get_total_used_from_sheets()
                _ai_total_cache["ts"]    = now_ts
            except Exception:
                pass  # eski cache değerini kullan
        ai_remaining = max(0, AI_MAX_REQUESTS - _ai_total_cache["value"])
        
        return jsonify({
            "status": "ok",
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "ram_used_mb": round(ram_used_mb, 1),
            "ram_total_mb": round(ram_total_mb, 1),
            "net_sent_mb": round(net_sent_mb, 2),
            "net_recv_mb": round(net_recv_mb, 2),
            "uptime": f"{uptime_hours}s {uptime_mins}dk",
            "ai_remaining": ai_remaining,
            "ai_total": AI_MAX_REQUESTS,
            "uptimerobot": get_uptimerobot_data()
        })
    except Exception as e:
        logger.error(f"❌ Sistem stats hatası: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

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
            except: pass

            iso = row[idx_iso].strip()
            if iso and iso != "—":
                if ilk_dinlenme_iso is None or iso < ilk_dinlenme_iso:
                    ilk_dinlenme_iso = iso
                try:
                    dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                    saat_counts[dt.hour] += 1
                    vakit_counts[get_vakit(dt.hour)] += 1
                except: pass

        ilk_tarih_str = "Bilinmiyor"
        if ilk_dinlenme_iso:
            try:
                dt = datetime.strptime(ilk_dinlenme_iso[:16], "%Y-%m-%dT%H:%M")
                ilk_tarih_str = dt.strftime("%d.%m.%Y")
            except: pass

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
                except: pass

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
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
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
            except: pass

        sanatcilar = sorted(
            [{"sanatci": k, "count": v["count"], "sure": fmt_sure(v["sure"])}
             for k, v in artist_counts.items()],
            key=lambda x: -x["count"]
        )
        return jsonify({"sanatcilar": sanatcilar, "toplam": len(sanatcilar)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/tum-sarkilar')
def api_tum_sarkilar():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
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
            except: pass

        sarkilar = sorted(
            [{"sarki": k, "sanatci": v["sanatci"], "count": v["count"], "sure": fmt_sure(v["sure"])}
             for k, v in track_counts.items()],
            key=lambda x: -x["count"]
        )
        return jsonify({"sarkilar": sarkilar, "toplam": len(sarkilar)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ay/<ay_label>")
def api_ay_detay(ay_label):
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
            except: continue

            sarki   = row[idx_sarki].strip()
            sanatci = row[idx_sanatci].strip()
            try: sure = int(row[idx_sure])
            except: sure = 0

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
        return jsonify({"error": str(e)}), 500

@app.route("/api/logout")
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/login")
def login_page():
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
<title>Giriş Yap – Music+</title>
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
  <h1>MUSIC<span style="color:var(--green)">+</span></h1>
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
        session.permanent = True
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
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"})
        idx_sarki    = headers.index("Şarkı Adı")
        idx_sarki_id = headers.index("Şarkı ID")

        from collections import Counter
        sarki_id_map = {}
        sarki_counts = Counter()
        for row in rows:
            if len(row) > max(idx_sarki, idx_sarki_id):
                sarki = row[idx_sarki].strip()
                sid   = row[idx_sarki_id].strip()
                if sarki and sid:
                    sarki_counts[sarki] += 1
                    sarki_id_map[sarki] = sid

        top_sarkilar_ids = []
        for s, _ in sarki_counts.most_common(50):
            if s not in sarki_id_map:
                continue
            tid = _extract_track_id(sarki_id_map[s])
            if tid:
                top_sarkilar_ids.append(f"spotify:track:{tid}")
            else:
                logger.warning(f"⚠️ Geçersiz şarkı ID'si atlandı: {sarki_id_map[s]!r}")

        logger.info(f"📋 Top şarkılar ID listesi (ilk 3): {top_sarkilar_ids[:3]}")
        if not top_sarkilar_ids:
            return jsonify({"error": "Playlist oluşturmak için yeterli şarkı verisi bulunamadı."}), 400

        pl = spotify._req("POST", "/me/playlists", json={
            "name": "En Çok Dinlediklerim",
            "public": False,
            "description": "Music+ Tarafından Oluşturulmuştur"
        })
        playlist_id = pl["id"]
        for i in range(0, len(top_sarkilar_ids), 100):
            chunk = top_sarkilar_ids[i:i+100]
            if chunk:  
                spotify._req("POST", f"/playlists/{playlist_id}/items", json={
                    "uris": chunk
                })

        return jsonify({"status": "ok", "playlist_id": playlist_id, "track_count": len(top_sarkilar_ids)})
    except Exception as e:
        logger.error(f"❌ Playlist oluşturma hatası: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/create-top-artists", methods=["POST"])
def api_create_top_artists_playlist():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"})
        idx_sanatci  = headers.index("Sanatçı")
        idx_sarki    = headers.index("Şarkı Adı")
        idx_sarki_id = headers.index("Şarkı ID")

        from collections import Counter, defaultdict
        sanatci_counts   = Counter()
        sanatci_sarkilar = defaultdict(dict)

        for row in rows:
            if len(row) > max(idx_sanatci, idx_sarki, idx_sarki_id):
                s       = row[idx_sanatci].strip()
                t       = row[idx_sarki].strip()
                raw_tid = row[idx_sarki_id].strip()
                if s:
                    sanatci_counts[s] += 1
                if s and t and raw_tid:
                    clean_tid = _extract_track_id(raw_tid)
                    if clean_tid:
                        sanatci_sarkilar[s][t] = clean_tid

        top_sanatcilar = [s for s, _ in sanatci_counts.most_common(20) if s]
        seen_ids = set()
        track_uris = []
        for s in top_sanatcilar:
            for tid in list(sanatci_sarkilar[s].values())[:5]:
                if tid not in seen_ids:
                    seen_ids.add(tid)
                    track_uris.append(f"spotify:track:{tid}")

        track_uris = track_uris[:50]
        logger.info(f"📋 Sanatçı playlist URI örnekleri (ilk 3): {track_uris[:3]}")

        if not track_uris:
            return jsonify({"error": "Playlist oluşturmak için yeterli şarkı verisi bulunamadı."}), 400

        pl = spotify._req("POST", "/me/playlists", json={
            "name": "En Çok Dinlediğim Sanatçılar",
            "public": False,
            "description": "Music+ Tarafından Oluşturulmuştur"
        })
        playlist_id = pl["id"]
        for i in range(0, len(track_uris), 100):
            chunk = track_uris[i:i+100]
            if chunk:  
                spotify._req("POST", f"/playlists/{playlist_id}/items", json={
                    "uris": chunk
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


# ══════════════════════════════════════════════════════════════════════════════
#  AI MODU — GEMINI STREAMING CHAT                                            
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    import json as _json
    global ai_requests_used

    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401

    body = request.get_json(silent=True) or {}
    user_message = (body.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Mesaj boş"}), 400

    history = _ai_history.get(uid, [])
    history.append({"role": "user", "content": user_message})

    now_playing   = None
    recent_tracks = None
    try:
        now_playing = spotify.get_now_playing()
    except Exception:
        pass
    try:
        recent_tracks = spotify.get_recently_played(10)
    except Exception:
        pass

    # Tam istatistik verisi (tüm sanatçılar/şarkılar, ay bazlı)
    full_stats_context = ""
    try:
        headers, rows = get_cached_data(uid)
        if rows:
            stats = compute_stats(headers, rows)
            if stats:
                top_sanatcilar_str = "\n".join(
                    f"  {i+1}. {s['sanatci']} — {s['count']} dinlenme"
                    for i, s in enumerate(stats["top_sanatcilar"])
                )
                top_sarkilar_str = "\n".join(
                    f"  {i+1}. {s['sarki']} — {s['sanatci']} ({s['count']} kez)"
                    for i, s in enumerate(stats["top_sarkilar"])
                )
                aylar_str = "\n".join(
                    f"  {a['ay']}: {a['kayit_sayisi']} dinlenme, {a['toplam']}"
                    for a in stats["aylar"]
                )
                full_stats_context = (
                    f"\n\nKULLANICININ TAM İSTATİSTİKLERİ:\n"
                    f"Toplam kayıt: {stats['toplam_kayit']}, Farklı şarkı: {stats['farkli_sarki']}, Farklı sanatçı: {stats['farkli_sanatci']}\n"
                    f"İlk kayıt: {stats['ilk_kayit_tarihi']}\n\n"
                    f"En çok dinlenen sanatçılar (Top 10):\n{top_sanatcilar_str}\n\n"
                    f"En çok dinlenen şarkılar (Top 10):\n{top_sarkilar_str}\n\n"
                    f"Aylık dinleme geçmişi:\n{aylar_str}"
                )
    except Exception:
        pass

    spotify_context = GeminiClient.build_spotify_context(now_playing, recent_tracks) + full_stats_context

    # Playlist listesini context'e ekle (ID'leriyle birlikte)
    playlist_context = ""
    try:
        playlists = spotify.get_playlists()
        if playlists:
            pl_lines = "\n".join(
                f"  - \"{p['name']}\" → ID: {p['id']} ({p['track_count']} şarkı)"
                for p in playlists
            )
            playlist_context = f"\n\nKULLANICININ SPOTİFY PLAYLİSTLERİ (düzenleme için MUTLAKA bu ID'leri kullan):\n{pl_lines}"
    except Exception:
        pass

    spotify_context += playlist_context

    def generate():
        global ai_requests_used
        full_response = ""
        request_successful = False
        used_model = ""
        
        try:
            for raw in gemini.stream_chat(history, spotify_context):
                chunk = _json.loads(raw)
                if chunk.get("type") == "text":
                    full_response += chunk["text"]
                elif chunk.get("type") == "done":
                    request_successful = True
                    used_model = chunk.get("model", "")
                    
                yield f"data: {raw}\n\n"
        except Exception as e:
            logger.error(f"❌ AI stream hatası: {e}")
            yield f"data: {_json.dumps({'type':'error','text':str(e)}, ensure_ascii=False)}\n\n"
        finally:
            if full_response:
                history.append({"role": "assistant", "content": full_response})
            trimmed = history[-AI_MAX_HISTORY:]
            _ai_history[uid] = trimmed
            
            if request_successful:
                ai_requests_used += 1
                # Cache'i geçersiz kıl — bir sonraki system-stats isteğinde Sheets'ten taze okusun
                _ai_total_cache["ts"] = 0
                # Sheets Limits sayfasına logla (arkaplanda, hata olursa yoksay)
                try:
                    display_name = get_current_user_name()
                    model_label = used_model if used_model else "gemma-4"
                    sheets.log_ai_request(uid, display_name, model_label)
                except Exception as log_err:
                    logger.warning(f"⚠️ Limits log hatası: {log_err}")

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/ai/history", methods=["GET"])
def api_ai_get_history():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    return jsonify({"history": _ai_history.get(uid, [])})


@app.route("/api/ai/history", methods=["DELETE"])
def api_ai_clear_history():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    _ai_history.pop(uid, None)
    return jsonify({"status": "ok"})


@app.route("/api/ai/search-track")
def api_ai_search_track():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Sorgu boş"}), 400
    try:
        tid = spotify._search_track(q)
        if tid:
            return jsonify({"id": tid, "uri": f"spotify:track:{tid}"})
        return jsonify({"id": None, "error": "Bulunamadı"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/create-playlist", methods=["POST"])
def api_ai_create_playlist():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    body        = request.get_json(silent=True) or {}
    name        = (body.get("name") or "AI Playlist").strip()
    track_names = body.get("tracks") or []
    try:
        playlist_id = spotify.create_playlist_from_track_names(
            name, track_names, description="Music+ Tarafından Oluşturulmuştur"
        )
        return jsonify({"status": "ok", "playlist_id": playlist_id, "track_count": len(track_names)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/add-to-playlist", methods=["POST"])
def api_ai_add_to_playlist():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    body        = request.get_json(silent=True) or {}
    playlist_id = (body.get("playlist_id") or "").strip()
    track_names = body.get("tracks") or []
    if not playlist_id:
        return jsonify({"error": "playlist_id gerekli"}), 400
    try:
        uris = []
        for t in track_names:
            tid = spotify._search_track(t)
            if tid:
                uris.append(f"spotify:track:{tid}")
        if uris:
            for i in range(0, len(uris), 100):
                spotify._req("POST", f"/playlists/{playlist_id}/items", json={"uris": uris[i:i+100]})
        return jsonify({"status": "ok", "added": len(uris)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/edit-playlist", methods=["POST"])
def api_ai_edit_playlist():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    body        = request.get_json(silent=True) or {}
    playlist_id = (body.get("playlist_id") or "").strip()
    track_names = body.get("tracks") or []
    new_name    = body.get("new_name")
    if not playlist_id:
        return jsonify({"error": "playlist_id gerekli"}), 400
    try:
        # İsim güncelleme
        if new_name:
            spotify._req("PUT", f"/playlists/{playlist_id}", json={
                "name": new_name,
                "description": "Music+ Tarafından Düzenlenmiştir"
            })
        else:
            # Sadece açıklamayı güncelle
            spotify._req("PUT", f"/playlists/{playlist_id}", json={
                "description": "Music+ Tarafından Düzenlenmiştir"
            })
        # Şarkı ekleme
        uris = []
        for t in track_names:
            tid = spotify._search_track(t)
            if tid:
                uris.append(f"spotify:track:{tid}")
        if uris:
            for i in range(0, len(uris), 100):
                spotify._req("POST", f"/playlists/{playlist_id}/items", json={"uris": uris[i:i+100]})
        return jsonify({"status": "ok", "added": len(uris)})
    except Exception as e:
        logger.error(f"❌ Playlist düzenleme hatası: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/limits")
def api_ai_limits():
    """Toplam AI kullanım limitini ve kullanıcı bazlı dökümü döndürür."""
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401

    summary = sheets.get_limits_summary()

    # Kullanıcı bazlı gruplama — today_used ve total_used ayrı ayrı
    user_totals = {}
    for row in summary:
        u     = row.get("user_id", "")
        dn    = row.get("display_name", u)
        m     = row.get("model", "")
        today = int(row.get("today_used", 0))  if str(row.get("today_used",  "0")).isdigit() else 0
        total = int(row.get("total_used", 0))  if str(row.get("total_used",  "0")).isdigit() else 0
        # Eski Limits sayfasıyla uyumluluk (requests_used sütunu varsa)
        if total == 0 and str(row.get("requests_used", "0")).isdigit():
            total = int(row.get("requests_used", 0))
            today = total
        lst   = row.get("last_used", "")
        if u not in user_totals:
            user_totals[u] = {"user_id": u, "display_name": dn, "today": 0, "total": 0, "models": [], "last_used": lst}
        user_totals[u]["today"] += today
        user_totals[u]["total"] += total
        user_totals[u]["models"].append({"model": m, "today": today, "total": total, "last_used": lst})
        if lst > user_totals[u]["last_used"]:
            user_totals[u]["last_used"] = lst

    # Toplam = Sheets'teki bütün total_used satırlarının gerçek toplamı
    grand_total = sum(u["total"] for u in user_totals.values())

    return jsonify({
        "total_used":  grand_total,
        "total_limit": AI_MAX_REQUESTS,
        "remaining":   max(0, AI_MAX_REQUESTS - grand_total),
        "users":       list(user_totals.values()),
    })


def scheduled_sync_all():
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
    # Her gün 00:00 UTC'de günlük AI limit sıfırla + aylık arşive yaz
    scheduler.add_job(
        lambda: sheets.reset_daily_limits(),
        "cron", hour=0, minute=0, id="daily_limit_reset",
        timezone="UTC"
    )
    scheduler.start()
    logger.info("⏰ Scheduler başlatıldı (her 30 dakika sync + 00:00 UTC limit sıfırlama)")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
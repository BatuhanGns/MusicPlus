"""
Yardımcı fonksiyonlar: istatistik hesaplama, formatlama, cache yönetimi.
Bu modül herhangi bir Flask nesnesine bağımlı değildir (pure fonksiyonlar).
"""

import re
import time
import requests
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import config

logger = logging.getLogger(__name__)


# ── Zaman / Format ───────────────────────────────────────────────────────────

def get_vakit(saat):
    for r, label in config.VAKIT.items():
        if saat in r:
            return label
    return "Gece (00-06)"


def fmt_sure(sn):
    s = int(sn)
    saat = s // 3600
    dk = (s % 3600) // 60
    return f"{saat} Saat {dk} Dakika" if saat > 0 else f"{dk} Dakika"


def _extract_track_id(raw):
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("spotify:track:"):
        return raw.split(":")[-1]
    m = re.search(r"/track/([A-Za-z0-9]+)", raw)
    if m:
        return m.group(1)
    if re.match(r"^[A-Za-z0-9]{22}$", raw):
        return raw
    part = re.split(r"[/:]", raw)[-1].split("?")[0].strip()
    if re.match(r"^[A-Za-z0-9]{22}$", part):
        return part
    return None


# ── UptimeRobot ──────────────────────────────────────────────────────────────

# ── İstatistik Hesaplama (CORE) ─────────────────────────────────────────────

def compute_stats(headers, rows):
    """
    Verilen headers + rows üzerinden TÜM istatistikleri hesaplar.
    Dashboard, topluluk, detay sayfalarının hepsi bu fonksiyonu kullanır.
    """
    if not rows:
        return None

    idx_sarki = headers.index("Şarkı Adı")
    idx_sanatci = headers.index("Sanatçı")
    idx_sure = headers.index("Süre (ms)")
    idx_tarih = headers.index("Dinlenme Tarihi")
    idx_iso = headers.index("_played_at_iso") if "_played_at_iso" in headers else -1
    idx_album = next(
        (i for i, h in enumerate(headers) if h.strip() in ("Albüm", "Album", "albüm", "album")),
        -1,
    )

    track_counts = defaultdict(lambda: {"count": 0, "sanatci": "", "album": "", "sure": 0, "ilk_iso": None})
    artist_counts = defaultdict(lambda: {"count": 0, "sure": 0, "ilk_iso": None})
    album_counts = defaultdict(lambda: {"count": 0, "sanatci": "", "sure": 0, "ilk_iso": None})
    gun_sure = defaultdict(int)
    ay_stats = defaultdict(lambda: {"sure": 0, "kayit": 0, "gunler": set()})
    toplam_sure = 0
    global_saat_counts = defaultdict(int)
    global_vakit_counts = defaultdict(int)
    ilk_kayit_iso = None
    bugun_date = datetime.now(timezone.utc).date()

    for row in rows:
        if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_tarih):
            continue
        sarki = (row[idx_sarki] or "").strip()
        sanatci = (row[idx_sanatci] or "").strip()
        tarih = (row[idx_tarih] or "").strip()
        album = (row[idx_album] or "").strip() if idx_album != -1 and len(row) > idx_album else ""
        try:
            sure = int(row[idx_sure]) // 1000
        except Exception:
            sure = 0

        iso = (row[idx_iso] or "").strip() if idx_iso != -1 and len(row) > idx_iso else ""
        toplam_sure += sure

        if iso and iso != "—":
            try:
                dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                global_saat_counts[dt.hour] += 1
                global_vakit_counts[get_vakit(dt.hour)] += 1
                if ilk_kayit_iso is None or iso < ilk_kayit_iso:
                    ilk_kayit_iso = iso
            except Exception:
                pass

        if sarki:
            track_counts[sarki]["count"] += 1
            track_counts[sarki]["sure"] += sure
            track_counts[sarki]["sanatci"] = sanatci
            track_counts[sarki]["album"] = album
            if iso and iso != "—":
                if track_counts[sarki]["ilk_iso"] is None or iso < track_counts[sarki]["ilk_iso"]:
                    track_counts[sarki]["ilk_iso"] = iso

        if sanatci:
            # "X, Y" gibi ortak sanatçıları bireysel olarak say
            for tek_sanatci in [s.strip() for s in sanatci.split(",") if s.strip()]:
                artist_counts[tek_sanatci]["count"] += 1
                artist_counts[tek_sanatci]["sure"] += sure
                if iso and iso != "—":
                    if artist_counts[tek_sanatci]["ilk_iso"] is None or iso < artist_counts[tek_sanatci]["ilk_iso"]:
                        artist_counts[tek_sanatci]["ilk_iso"] = iso

        if album:
            album_counts[album]["count"] += 1
            album_counts[album]["sure"] += sure
            album_counts[album]["sanatci"] = sanatci
            if iso and iso != "—":
                if album_counts[album]["ilk_iso"] is None or iso < album_counts[album]["ilk_iso"]:
                    album_counts[album]["ilk_iso"] = iso

        if tarih:
            gun_sure[tarih] += sure
            try:
                g, ay, yil = tarih.split(".")
                ak = f"{yil}-{ay}"
                ay_stats[ak]["sure"] += sure
                ay_stats[ak]["kayit"] += 1
                ay_stats[ak]["gunler"].add(tarih)
            except Exception:
                pass

    def calc_days(iso_str):
        if not iso_str:
            return None
        try:
            dt = datetime.strptime(iso_str[:10], "%Y-%m-%d").date()
            return max(0, (bugun_date - dt).days)
        except Exception:
            return None

    top_sarkilar = sorted(
        [
            {
                "sarki": k,
                "sanatci": v["sanatci"],
                "album": v["album"],
                "count": v["count"],
                "sure": v["sure"],
                "kac_gundur": calc_days(v["ilk_iso"]),
            }
            for k, v in track_counts.items()
        ],
        key=lambda x: -x["count"],
    )[:10]

    top_sanatcilar = sorted(
        [
            {
                "sanatci": k,
                "count": v["count"],
                "sure": v["sure"],
                "kac_gundur": calc_days(v["ilk_iso"]),
            }
            for k, v in artist_counts.items()
        ],
        key=lambda x: -x["count"],
    )

    top_albumler = sorted(
        [
            {
                "album": k,
                "sanatci": v["sanatci"],
                "count": v["count"],
                "sure": v["sure"],
                "kac_gundur": calc_days(v["ilk_iso"]),
            }
            for k, v in album_counts.items()
        ],
        key=lambda x: -x["count"],
    )[:10]

    hafta = []
    for i in range(6, -1, -1):
        gun = bugun_date - timedelta(days=i)
        ts = gun.strftime("%d.%m.%Y")
        hafta.append({"tarih": ts, "gun": config.TR_GUNLER[gun.weekday()], "sure_sn": gun_sure.get(ts, 0)})

    aylar = []
    for ak in sorted(ay_stats.keys()):
        yil, ay_no = ak.split("-")
        st = ay_stats[ak]
        gs = len(st["gunler"])
        aylar.append(
            {
                "ay": f"{config.TR_AYLAR[int(ay_no)]} {yil}",
                "toplam": fmt_sure(st["sure"]),
                "ortalama": fmt_sure(st["sure"] // gs if gs else 0),
                "kayit_sayisi": st["kayit"],
            }
        )

    ilk_tarih_str = "Bilinmiyor"
    if ilk_kayit_iso:
        try:
            ilk_tarih_str = datetime.strptime(ilk_kayit_iso[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            pass

    return {
        "toplam_kayit": len(rows),
        "farkli_sarki": len(track_counts),
        "farkli_sanatci": len(artist_counts),
        "farkli_album": len(album_counts),
        "toplam_sure_sn": toplam_sure,
        "ilk_kayit_tarihi": ilk_tarih_str,
        "top_sarkilar": top_sarkilar,
        "top_sanatcilar": top_sanatcilar,
        "top_albumler": top_albumler,
        "hafta": hafta,
        "aylar": aylar,
        "genel_saatler": [{"saat": f"{h:02d}:00", "count": global_saat_counts.get(h, 0)} for h in range(24)],
        "genel_vakitler": [{"vakit": k, "count": v} for k, v in sorted(global_vakit_counts.items(), key=lambda x: -x[1])],
    }

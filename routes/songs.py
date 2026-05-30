"""
Şarkı / Sanatçı / Albüm detay API'leri ve görsel endpoint'leri.
- GET  /api/sarki/<sarki_adi>
- GET  /api/sanatci/<sanatci_adi>
- GET  /api/album/<album_adi>
- GET  /api/ay/<ay_label>
- GET  /api/tum-sarkilar
- GET  /api/tum-sanatcilar
- GET  /api/tum-albumler
- GET  /api/sanatci-gorsel/<sanatci>
- GET  /api/sarki-gorsel/<sarki>
- GET  /api/album-gorsel/<album>
"""

import logging
import requests
from collections import defaultdict
from datetime import datetime
from flask import Blueprint, jsonify, request

import config
from extensions import get_current_user_id, get_cached_data, load_user_data, spotify
from utils.helpers import compute_stats, fmt_sure, get_vakit

logger = logging.getLogger(__name__)
bp = Blueprint("songs", __name__)


# ── Görsel Cache ─────────────────────────────────────────────────────────────
# {cache_key: (url_or_none, timestamp)}  — 24 saatte bir yenilenir
_gorsel_cache: dict = {}
_GORSEL_TTL = 86400  # 24 saat


def _gorsel_cache_get(key: str):
    """Cache'ten URL döndürür. Süresi geçmişse veya hiç yoksa None."""
    import time as _time
    entry = _gorsel_cache.get(key)
    if not entry:
        return "MISS"   # cache'te hiç yok
    url, ts = entry
    if _time.time() - ts > _GORSEL_TTL:
        del _gorsel_cache[key]
        return "MISS"   # süresi dolmuş
    return url          # None olabilir (bilinen bulunamayan)


def _gorsel_cache_set(key: str, url):
    import time as _time
    # DÜZELTME: Sadece BAŞARILI sonuçları cache'le.
    # None değerleri cache'leme — token geçersizdi ya da geçici hataydı.
    # Böylece token düzeldikten sonra yeniden dener.
    if url is not None:
        _gorsel_cache[key] = (url, _time.time())


def _spotify_search_image(q, item_type="artist"):
    """
    Spotify Search API kullanarak görsel URL döndürür.
    DÜZELTMELER:
    - None değerler artık cache'lenmez (token geçici hataları kalıcı olmaz).
    - 401 aldığında token'ı yenilemeye zorlar (force_refresh).
    - 24 saatlik TTL ile başarılı sonuçlar cache'lenir.
    """
    cache_key = f"{item_type}:{q}"
    cached = _gorsel_cache_get(cache_key)
    if cached != "MISS":
        return cached   # None da olsa bilinen sonuç (başarılı ama görsel yok)

    def _do_search(token):
        return requests.get(
            "https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": q, "type": item_type, "limit": 1},
            timeout=5,
        )

    try:
        token = spotify._get_access_token()
        if not token:
            return None

        resp = _do_search(token)

        # Token süresi dolmuşsa bir kez yenile ve tekrar dene
        if resp.status_code == 401:
            logger.warning(f"Görsel: 401 alındı, token yenileniyor ({item_type}:{q[:30]})")
            spotify._access_token    = None   # cache'i temizle → yenile
            spotify._token_expires_at = 0
            token = spotify._get_access_token()
            resp  = _do_search(token)

        if resp.status_code != 200:
            logger.warning(f"Görsel arama {resp.status_code} ({item_type}:{q[:40]})")
            return None   # cache'leme — geçici hata olabilir

        data    = resp.json()
        img_url = None

        if item_type == "artist":
            items = data.get("artists", {}).get("items", [])
            if items and items[0].get("images"):
                img_url = items[0]["images"][0]["url"]
        elif item_type == "track":
            items = data.get("tracks", {}).get("items", [])
            if items and items[0].get("album", {}).get("images"):
                img_url = items[0]["album"]["images"][0]["url"]
        elif item_type == "album":
            items = data.get("albums", {}).get("items", [])
            if items and items[0].get("images"):
                img_url = items[0]["images"][0]["url"]

        _gorsel_cache_set(cache_key, img_url)
        return img_url

    except Exception as e:
        logger.error(f"Görsel arama hatası ({item_type}:{q[:40]}): {e}")
        return None   # istisnalarda da cache'leme


# ── Şarkı Detay ──────────────────────────────────────────────────────────────

@bp.route("/api/sarki/<path:sarki_adi>")
def api_sarki_detay(sarki_adi):
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure = headers.index("Süre (sn)")
        idx_iso = headers.index("_played_at_iso")

        saat_counts = defaultdict(int)
        vakit_counts = defaultdict(int)
        toplam_count = 0
        toplam_sure = 0
        sanatci = ""
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
            except Exception:
                pass

            iso = row[idx_iso].strip()
            if iso and iso != "—":
                if ilk_dinlenme_iso is None or iso < ilk_dinlenme_iso:
                    ilk_dinlenme_iso = iso
                try:
                    dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                    saat_counts[dt.hour] += 1
                    vakit_counts[get_vakit(dt.hour)] += 1
                except Exception:
                    pass

        ilk_tarih_str = "Bilinmiyor"
        if ilk_dinlenme_iso:
            try:
                dt = datetime.strptime(ilk_dinlenme_iso[:16], "%Y-%m-%dT%H:%M")
                ilk_tarih_str = dt.strftime("%d.%m.%Y")
            except Exception:
                pass

        saatler = [{"saat": f"{h:02d}:00", "count": saat_counts.get(h, 0)} for h in range(24)]
        vakitler = [{"vakit": k, "count": v} for k, v in sorted(vakit_counts.items(), key=lambda x: -x[1])]

        return jsonify(
            {
                "sarki": sarki_adi,
                "sanatci": sanatci,
                "toplam_count": toplam_count,
                "toplam_sure": fmt_sure(toplam_sure),
                "ilk_dinlenme": ilk_tarih_str,
                "saatler": saatler,
                "vakitler": vakitler,
            }
        )
    except Exception as e:
        logger.error(f"❌ Şarkı detay hatası: {e}")
        return jsonify({"error": str(e)}), 500


# ── Sanatçı Detay ────────────────────────────────────────────────────────────

@bp.route("/api/sanatci/<path:sanatci_adi>")
def api_sanatci_detay(sanatci_adi):
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure = headers.index("Süre (sn)")
        idx_iso = headers.index("_played_at_iso")

        sarki_counts = defaultdict(lambda: {"count": 0, "sure": 0})
        saat_counts = defaultdict(int)
        vakit_counts = defaultdict(int)
        toplam_count = 0
        toplam_sure = 0

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_iso):
                continue
            # "Pera" için "Milat, Pera" gibi işbirliklerini de say
            sanatcilar_raw = [s.strip() for s in row[idx_sanatci].split(",")]
            if sanatci_adi not in sanatcilar_raw:
                continue

            toplam_count += 1
            sarki = row[idx_sarki].strip()
            try:
                sure = int(row[idx_sure])
                toplam_sure += sure
            except Exception:
                sure = 0

            if sarki:
                sarki_counts[sarki]["count"] += 1
                sarki_counts[sarki]["sure"] += sure

            iso = row[idx_iso].strip()
            if iso and iso != "—":
                try:
                    dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                    saat_counts[dt.hour] += 1
                    vakit_counts[get_vakit(dt.hour)] += 1
                except Exception:
                    pass

        # Farklı şarkı ve albüm sayısı
        idx_album = next((i for i,h in enumerate(headers) if h.strip() in ("Albüm","Album","albüm","album")), -1)
        unique_albums = set()
        if idx_album != -1:
            for row in rows:
                if len(row) > max(idx_sanatci, idx_album):
                    sanatcilar_raw = [s.strip() for s in row[idx_sanatci].split(",")]
                    if sanatci_adi in sanatcilar_raw:
                        alb = row[idx_album].strip()
                        if alb:
                            unique_albums.add(alb)

        top_sarkilar = sorted(
            [{"sarki": k, "count": v["count"], "sure": fmt_sure(v["sure"])} for k, v in sarki_counts.items()],
            key=lambda x: -x["count"],
        )[:10]

        saatler = [{"saat": f"{h:02d}:00", "count": saat_counts.get(h, 0)} for h in range(24)]
        vakitler = [{"vakit": k, "count": v} for k, v in sorted(vakit_counts.items(), key=lambda x: -x[1])]

        return jsonify(
            {
                "sanatci":       sanatci_adi,
                "toplam_count":  toplam_count,
                "toplam_sure":   fmt_sure(toplam_sure),
                "farkli_sarki":  len(sarki_counts),
                "farkli_album":  len(unique_albums),
                "top_sarkilar":  top_sarkilar,
                "saatler":       saatler,
                "vakitler":      vakitler,
            }
        )
    except Exception as e:
        logger.error(f"❌ Sanatçı detay hatası: {e}")
        return jsonify({"error": str(e)}), 500


# ── Albüm Detay ──────────────────────────────────────────────────────────────

@bp.route("/api/album/<path:album_adi>")
def api_album(album_adi):
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yok"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            load_user_data(uid)
            headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_album = next((i for i, h in enumerate(headers) if h.strip() in ("Albüm", "Album", "albüm", "album")), -1)
        idx_sure = headers.index("Süre (sn)")

        sarki_counts = defaultdict(lambda: {"count": 0, "sanatci": "", "sure": 0})
        toplam_count = 0
        toplam_sure = 0
        sanatci_ad = ""

        for row in rows:
            alb = row[idx_album].strip() if idx_album != -1 and len(row) > idx_album else ""
            if alb.lower() != album_adi.lower():
                continue
            sarki = row[idx_sarki].strip() if len(row) > idx_sarki else ""
            sanatci = row[idx_sanatci].strip() if len(row) > idx_sanatci else ""
            try:
                sure = int(row[idx_sure])
            except Exception:
                sure = 0
            sarki_counts[sarki]["count"] += 1
            sarki_counts[sarki]["sanatci"] = sanatci
            sarki_counts[sarki]["sure"] += sure
            toplam_count += 1
            toplam_sure += sure
            if not sanatci_ad:
                sanatci_ad = sanatci

        top_sarkilar = sorted(
            [{"sarki": k, "sanatci": v["sanatci"], "count": v["count"]} for k, v in sarki_counts.items()],
            key=lambda x: -x["count"],
        )[:20]

        return jsonify(
            {
                "album":         album_adi,
                "sanatci":       sanatci_ad,
                "toplam_count":  toplam_count,
                "toplam_sure":   fmt_sure(toplam_sure),
                "farkli_sarki":  len(sarki_counts),
                "top_sarkilar":  top_sarkilar,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Tüm Liste Endpoint'leri ──────────────────────────────────────────────────

@bp.route("/api/tum-sarkilar")
def api_tum_sarkilar():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure = headers.index("Süre (sn)")

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
            except Exception:
                pass

        sarkilar = sorted(
            [{"sarki": k, "sanatci": v["sanatci"], "count": v["count"], "sure": fmt_sure(v["sure"])} for k, v in track_counts.items()],
            key=lambda x: -x["count"],
        )
        return jsonify({"sarkilar": sarkilar, "toplam": len(sarkilar)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/tum-sanatcilar")
def api_tum_sanatcilar():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sanatci = headers.index("Sanatçı")
        idx_sure = headers.index("Süre (sn)")

        artist_counts = defaultdict(lambda: {"count": 0, "sure": 0})
        for row in rows:
            if len(row) <= max(idx_sanatci, idx_sure):
                continue
            sanatci = row[idx_sanatci].strip()
            if not sanatci:
                continue
            try:
                sure = int(row[idx_sure])
            except Exception:
                sure = 0
            # "X, Y" işbirliklerini bireysel olarak say
            for tek in [s.strip() for s in sanatci.split(",") if s.strip()]:
                artist_counts[tek]["count"] += 1
                artist_counts[tek]["sure"] += sure

        sanatcilar = sorted(
            [{"sanatci": k, "count": v["count"], "sure": fmt_sure(v["sure"])} for k, v in artist_counts.items()],
            key=lambda x: -x["count"],
        )
        return jsonify({"sanatcilar": sanatcilar, "toplam": len(sanatcilar)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/tum-albumler")
def api_tum_albumler():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yok"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            load_user_data(uid)
            headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"albumler": [], "toplam": 0})

        idx_album = next((i for i, h in enumerate(headers) if h.strip() in ("Albüm", "Album", "albüm", "album")), -1)
        idx_sanatci = headers.index("Sanatçı")
        if idx_album == -1:
            return jsonify({"albumler": [], "toplam": 0})

        counts = defaultdict(lambda: {"count": 0, "sanatci": ""})
        for row in rows:
            alb = row[idx_album].strip() if len(row) > idx_album else ""
            san = row[idx_sanatci].strip() if len(row) > idx_sanatci else ""
            if alb:
                counts[alb]["count"] += 1
                counts[alb]["sanatci"] = san

        albumler = sorted(
            [{"album": k, "sanatci": v["sanatci"], "count": v["count"]} for k, v in counts.items()],
            key=lambda x: -x["count"],
        )
        return jsonify({"albumler": albumler, "toplam": len(albumler)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Aylık Detay ──────────────────────────────────────────────────────────────

@bp.route("/api/ay/<path:ay_label>")
def api_ay_detay(ay_label):
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        headers, rows = get_cached_data(uid)
        if not rows:
            return jsonify({"error": "Veri yok"})

        idx_sarki = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure = headers.index("Süre (sn)")
        idx_tarih = headers.index("Dinlenme Tarihi")

        TR_AYLAR_REV = {v: str(k).zfill(2) for k, v in config.TR_AYLAR.items()}
        parca = ay_label.split(" ")
        if len(parca) != 2:
            return jsonify({"error": "Geçersiz ay formatı"})
        ay_tr, yil = parca
        ay_no = TR_AYLAR_REV.get(ay_tr)
        if not ay_no:
            return jsonify({"error": "Ay bulunamadı"})

        track_counts = defaultdict(lambda: {"count": 0, "sure": 0, "sanatci": ""})
        artist_counts = defaultdict(lambda: {"count": 0, "sure": 0})
        toplam_kayit = 0
        toplam_sure = 0

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
            except Exception:
                continue

            sarki = row[idx_sarki].strip()
            sanatci = row[idx_sanatci].strip()
            try:
                sure = int(row[idx_sure])
            except Exception:
                sure = 0

            toplam_kayit += 1
            toplam_sure += sure

            if sarki:
                track_counts[sarki]["count"] += 1
                track_counts[sarki]["sure"] += sure
                track_counts[sarki]["sanatci"] = sanatci
            if sanatci:
                artist_counts[sanatci]["count"] += 1
                artist_counts[sanatci]["sure"] += sure

        top_sarkilar = sorted(
            [{"sarki": k, "sanatci": v["sanatci"], "count": v["count"], "sure": fmt_sure(v["sure"])} for k, v in track_counts.items()],
            key=lambda x: -x["count"],
        )[:10]

        top_sanatcilar = sorted(
            [{"sanatci": k, "count": v["count"], "sure": fmt_sure(v["sure"])} for k, v in artist_counts.items()],
            key=lambda x: -x["count"],
        )[:10]

        return jsonify(
            {
                "ay": ay_label,
                "toplam_kayit": toplam_kayit,
                "toplam_sure": fmt_sure(toplam_sure),
                "top_sarkilar": top_sarkilar,
                "top_sanatcilar": top_sanatcilar,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Görsel Endpoint'leri ─────────────────────────────────────────────────────
# FIX: Path param yerine query param kullanılıyor.
# Eski: /api/sanatci-gorsel/Soner%20Sarıkabadayı  (encode sorunları)
# Yeni: /api/sanatci-gorsel?q=Soner+Sarıkabadayı  (güvenli, tüm karakterler çalışır)

@bp.route("/api/sanatci-gorsel")
def api_sanatci_gorsel():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yok"}), 401
    sanatci = request.args.get("q", "").strip()
    if not sanatci:
        return jsonify({"image_url": None})
    img = _spotify_search_image(sanatci, "artist")
    return jsonify({"image_url": img})


@bp.route("/api/sarki-gorsel")
def api_sarki_gorsel():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yok"}), 401
    sarki   = request.args.get("q", "").strip()
    sanatci = request.args.get("sanatci", "").strip()
    q = f"{sarki} {sanatci}".strip()
    if not q:
        return jsonify({"image_url": None})
    img = _spotify_search_image(q, "track")
    return jsonify({"image_url": img})


@bp.route("/api/album-gorsel")
def api_album_gorsel():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yok"}), 401
    album   = request.args.get("q", "").strip()
    sanatci = request.args.get("sanatci", "").strip()
    q = f"{album} {sanatci}".strip()
    if not q:
        return jsonify({"image_url": None})
    img = _spotify_search_image(q, "album")
    return jsonify({"image_url": img})

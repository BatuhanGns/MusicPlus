"""
MusicDNA — Spotify Wrapped tarzı kişisel müzik kimliği.
GET /api/music-dna  →  Tüm veriyi + kapak fotoğraflarını döner.
"""

import logging
import time
from datetime import datetime, timezone
from flask import Blueprint, jsonify, session

from extensions import get_current_user_id, get_cached_data, load_user_data, sheets, spotify
from utils.helpers import compute_stats, fmt_sure

logger = logging.getLogger(__name__)
bp = Blueprint("music_dna", __name__)


# ── Artwork Fetching ──────────────────────────────────────────────────────────

def _track_artwork(client, track_name, artist_name):
    try:
        data  = client._req("GET", "/search", params={
            "q": f"track:{track_name} artist:{artist_name}",
            "type": "track", "limit": 1
        })
        items = data.get("tracks", {}).get("items", [])
        if items:
            imgs = items[0].get("album", {}).get("images", [])
            return imgs[0]["url"] if imgs else None
    except Exception:
        pass
    return None


def _artist_artwork(client, artist_name):
    try:
        data  = client._req("GET", "/search", params={
            "q": artist_name, "type": "artist", "limit": 1
        })
        items = data.get("artists", {}).get("items", [])
        if items:
            imgs = items[0].get("images", [])
            # Orta boyutu tercih et (çok büyük göndermemek için)
            return imgs[1]["url"] if len(imgs) > 1 else (imgs[0]["url"] if imgs else None)
    except Exception:
        pass
    return None


def _album_artwork(client, album_name, artist_name):
    try:
        data  = client._req("GET", "/search", params={
            "q": f"album:{album_name} artist:{artist_name}",
            "type": "album", "limit": 1
        })
        items = data.get("albums", {}).get("items", [])
        if items:
            imgs = items[0].get("images", [])
            return imgs[0]["url"] if imgs else None
    except Exception:
        pass
    return None


def _get_ai_total(uid):
    try:
        ws = sheets._find_sheet("Limits")
        if not ws:
            return 0
        total = 0
        for row in ws.get_all_values()[1:]:
            if len(row) >= 5 and row[0] == uid and str(row[4]).isdigit():
                total += int(row[4])
        return total
    except Exception:
        return 0


# ── Route ─────────────────────────────────────────────────────────────────────

@bp.route("/api/music-dna")
def api_music_dna():
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
            return jsonify({"error": "Henüz dinleme verisi yok"}), 404

        top5_sarki   = stats["top_sarkilar"][:5]
        top5_sanatci = stats["top_sanatcilar"][:5]
        top5_album   = stats["top_albumler"][:5]

        en_cok_sarki   = top5_sarki[0]   if top5_sarki   else None
        en_cok_sanatci = top5_sanatci[0] if top5_sanatci else None

        vakitler   = stats.get("genel_vakitler", [])
        en_cok_vakit = vakitler[0]["vakit"] if vakitler else "—"

        aylar = stats.get("aylar", [])
        en_cok_ay = max(aylar, key=lambda a: a["kayit_sayisi"]) if aylar else None

        # ── Dinleme günü hesabı (tam sayı) ───────────────────────────────────
        secs = stats["toplam_sure_sn"]
        days = secs // 86400        # tam gün
        hrs  = secs // 3600         # tam saat

        # ── Kapak fotoğrafları (Spotify API) ─────────────────────────────────
        # Kullanıcının kendi token'ı ile istek at
        refresh_token = session.get("refresh_token", "")
        if refresh_token:
            from clients.spotify_client import SpotifyClient
            client = SpotifyClient(refresh_token=refresh_token)

            if en_cok_sarki:
                en_cok_sarki["artwork"] = _track_artwork(client, en_cok_sarki["sarki"], en_cok_sarki["sanatci"])
                time.sleep(0.1)

            if en_cok_sanatci:
                en_cok_sanatci["artwork"] = _artist_artwork(client, en_cok_sanatci["sanatci"])
                time.sleep(0.1)

            for t in top5_sarki:
                t["artwork"] = _track_artwork(client, t["sarki"], t["sanatci"])
                time.sleep(0.08)

            for a in top5_sanatci:
                a["artwork"] = _artist_artwork(client, a["sanatci"])
                time.sleep(0.08)

            for al in top5_album:
                al["artwork"] = _album_artwork(client, al["album"], al["sanatci"])
                time.sleep(0.08)

        return jsonify({
            "display_name":   session.get("display_name", "Dinleyici"),
            "generated_at":   datetime.now(timezone.utc).strftime("%d.%m.%Y"),
            "toplam_kayit":   stats["toplam_kayit"],
            "toplam_sure":    fmt_sure(secs),
            "toplam_sure_sn": secs,
            "dinleme_gun":    days,
            "dinleme_saat":   hrs,
            "farkli_sarki":   stats["farkli_sarki"],
            "farkli_sanatci": stats["farkli_sanatci"],
            "farkli_album":   stats["farkli_album"],
            "ilk_kayit":      stats["ilk_kayit_tarihi"],
            "top5_sarki":     top5_sarki,
            "top5_sanatci":   top5_sanatci,
            "top5_album":     top5_album,
            "en_cok_sarki":   en_cok_sarki,
            "en_cok_sanatci": en_cok_sanatci,
            "en_cok_vakit":   en_cok_vakit,
            "en_cok_ay":      en_cok_ay,
            "ai_total":       _get_ai_total(uid),
        })

    except Exception as e:
        logger.error(f"MusicDNA API hatası: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route("/api/img-proxy")
def img_proxy():
    """
    Spotify CDN görsellerini backend üzerinden servis eder.
    - Tarayıcıda CORS sorunu olmadan görseller yüklenir
    - html2canvas tainted canvas hatası almaz → PDF temiz çıkar
    - Güvenlik: sadece i.scdn.co ve mosaic.scdn.co domainlerine izin verilir
    """
    import requests as _req
    from flask import request as freq, Response

    url = freq.args.get("u", "").strip()
    # Spotify CDN domainleri (genişletilmiş liste)
    allowed = (
        "https://i.scdn.co/",
        "https://mosaic.scdn.co/",
        "https://image-cdn-ak.spotifycdn.com/",
        "https://image-cdn-fa.spotifycdn.com/",
        "https://thisis-images.spotifycdn.com/",
        "https://lineup-images.scdn.co/",
        "https://newjams-images.scdn.co/",
    )
    if not url or not any(url.startswith(a) for a in allowed):
        logger.warning(f"img-proxy: izinsiz domain — {url[:60]}")
        return "", 400

    try:
        r = _req.get(url, timeout=8, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer":    "https://open.spotify.com/",
        })
        if r.status_code != 200:
            logger.warning(f"img-proxy: CDN {r.status_code} — {url[:60]}")
            return "", r.status_code
        return Response(
            r.content,
            content_type=r.headers.get("content-type", "image/jpeg"),
            headers={
                "Cache-Control": "public, max-age=86400",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        logger.warning(f"img-proxy hatası: {e} — {url[:60]}")
        return "", 502


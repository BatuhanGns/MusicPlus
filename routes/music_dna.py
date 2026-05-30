"""
MusicDNA — Kullanıcının kişisel müzik kimliği özeti.
GET /api/music-dna  →  DNA kartı için tüm veriyi JSON döner.
"""

import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, session

from extensions import get_current_user_id, get_cached_data, load_user_data, sheets
from utils.helpers import compute_stats, fmt_sure

logger = logging.getLogger(__name__)
bp = Blueprint("music_dna", __name__)


def _get_ai_total(uid: str) -> int:
    """Kullanıcının toplam AI kullanım sayısını Limits sayfasından çeker."""
    try:
        ws = sheets._find_sheet("Limits")
        if not ws:
            return 0
        records = ws.get_all_values()
        total = 0
        for row in records[1:]:
            if len(row) >= 5 and row[0] == uid:
                val = row[4]
                if str(val).isdigit():
                    total += int(val)
        return total
    except Exception as e:
        logger.warning(f"AI total okuma hatası: {e}")
        return 0


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

        # ── Top 5 ────────────────────────────────────────────────────────────
        top5_sarki    = stats["top_sarkilar"][:5]
        top5_sanatci  = stats["top_sanatcilar"][:5]
        top5_album    = stats["top_albumler"][:5]

        # ── #1'ler ───────────────────────────────────────────────────────────
        en_cok_sarki    = top5_sarki[0]   if top5_sarki   else None
        en_cok_sanatci  = top5_sanatci[0] if top5_sanatci else None

        # ── En çok dinlenen zaman (vakit) ────────────────────────────────────
        vakitler = stats.get("genel_vakitler", [])
        en_cok_vakit = vakitler[0]["vakit"] if vakitler else "—"

        # ── En çok dinlenen ay ───────────────────────────────────────────────
        aylar = stats.get("aylar", [])
        en_cok_ay = None
        if aylar:
            en_cok_ay = max(aylar, key=lambda a: a["kayit_sayisi"])

        # ── AI kullanımı ─────────────────────────────────────────────────────
        ai_total = _get_ai_total(uid)

        # ── Genel özetler ─────────────────────────────────────────────────────
        display_name = session.get("display_name", "Dinleyici")

        return jsonify({
            "display_name":     display_name,
            "generated_at":     datetime.now(timezone.utc).strftime("%d.%m.%Y"),
            "toplam_kayit":     stats["toplam_kayit"],
            "toplam_sure":      fmt_sure(stats["toplam_sure_sn"]),
            "farkli_sarki":     stats["farkli_sarki"],
            "farkli_sanatci":   stats["farkli_sanatci"],
            "farkli_album":     stats["farkli_album"],
            "ilk_kayit":        stats["ilk_kayit_tarihi"],
            "top5_sarki":       top5_sarki,
            "top5_sanatci":     top5_sanatci,
            "top5_album":       top5_album,
            "en_cok_sarki":     en_cok_sarki,
            "en_cok_sanatci":   en_cok_sanatci,
            "en_cok_vakit":     en_cok_vakit,
            "en_cok_ay":        en_cok_ay,
            "ai_total":         ai_total,
        })

    except Exception as e:
        logger.error(f"MusicDNA API hatası: {e}")
        return jsonify({"error": str(e)}), 500

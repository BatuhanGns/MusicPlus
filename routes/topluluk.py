"""
Topluluk istatistikleri ve gizlilik/izin yönetimi API'leri.
- GET  /api/istatistikler?aralik=1hafta|1ay|1yil|tumzamanlar
- GET  /api/karsilastirma   → Her izin vermiş kullanıcının bireysel özet verileri
- POST /api/izin
- GET  /api/izin
"""

import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify

import config
from extensions import get_current_user_id, get_current_user_name, get_cached_data, load_user_data, sheets
from utils.helpers import compute_stats
from utils.gamification import compute_gamification, compute_xp_from_stats

logger = logging.getLogger(__name__)
bp = Blueprint("topluluk", __name__)


def _filter_rows_by_aralik(headers, rows, aralik):
    """stats.py ile aynı filtre mantığı."""
    if aralik == "tumzamanlar" or not aralik:
        return rows
    try:
        idx_iso = headers.index("_played_at_iso")
    except ValueError:
        return rows
    now = datetime.now(timezone.utc)
    if aralik == "1hafta":
        since = now - timedelta(weeks=1)
    elif aralik == "1ay":
        since = now - timedelta(days=30)
    elif aralik == "1yil":
        since = now - timedelta(days=365)
    else:
        return rows
    since_str = since.strftime("%Y-%m-%dT%H:%M")
    return [
        row for row in rows
        if len(row) > idx_iso
        and (row[idx_iso] or "").strip() not in ("", "—")
        and (row[idx_iso] or "").strip()[:16] >= since_str
    ]


@bp.route("/api/istatistikler")
def api_istatistikler():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        permitted = sheets.get_all_permitted_users()
        if not permitted:
            return jsonify({"error": "Henüz kimse izin vermemiş"})
        headers, rows = sheets.get_combined_data(permitted)
        aralik = request.args.get("aralik", "tumzamanlar")
        filtered = _filter_rows_by_aralik(headers, rows, aralik)
        stats = compute_stats(headers, filtered)
        if not stats:
            return jsonify({"error": "Veri yok"})
        stats["katilimci_sayisi"] = len(permitted)
        stats["son_sync"] = config._last_sync
        stats["aralik"] = aralik
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Istatistikler API hatasi: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/karsilastirma")
def api_karsilastirma():
    """
    İzin vermiş her kullanıcının bireysel istatistiklerini karşılaştırma için döner.
    Her kullanıcı için:
      - Toplam dinleme süresi (saat)
      - En çok dinlenen şarkı + kaç kez
      - En çok dinlenen sanatçı + kaç kez
      - En çok dinlenen albüm + kaç kez
      - En uzun seri (gün) + aktif seri
      - En güçlü pet (rarity + isim/coin_mult)
      - display_name
    """
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401

        permitted = sheets.get_all_permitted_users()
        if not permitted:
            return jsonify({"error": "Henüz kimse izin vermemiş"})

        # Settings sayfasından display_name haritası oluştur
        name_map = {}
        try:
            ws = sheets._find_sheet("Settings")
            if ws:
                for row in ws.get_all_values()[1:]:
                    if row and len(row) >= 2:
                        name_map[row[0]] = row[1] or row[0]
        except Exception:
            pass

        # Pets sayfasından pet verilerini oku
        pet_map = {}
        try:
            ws_pets = sheets._find_sheet("Pets")
            if ws_pets:
                import json as _json
                RARITY_TIER = {
                    "Common": 1, "Rare": 2, "Epic": 3,
                    "Legendary": 4, "Mysterious": 5, "Cosmic": 6
                }
                for row in ws_pets.get_all_values()[1:]:
                    if row and len(row) >= 3 and row[0]:
                        try:
                            pdata = _json.loads(row[2])
                            inv = pdata.get("inventory", [])
                            if inv:
                                # En güçlü pet: rarity tier'ına göre sırala
                                best = max(
                                    inv,
                                    key=lambda p: (
                                        RARITY_TIER.get(p.get("rarity", "Common"), 0),
                                        p.get("coin_mult", 1.0)
                                    )
                                )
                                pet_map[row[0]] = {
                                    "rarity": best.get("rarity", "Common"),
                                    "egg_type": best.get("egg_type", "normal"),
                                    "coin_mult": best.get("coin_mult", 1.0),
                                    "xp": best.get("xp", 0),
                                    "active": best.get("active", False),
                                }
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"Pets okuma hatasi: {e}")

        results = []
        for user_id in permitted:
            try:
                headers, rows = sheets.get_user_data(user_id)
                if not rows:
                    continue

                stats = compute_stats(headers, rows)
                if not stats:
                    continue

                # En çok dinlenen şarkı
                top_sarki = stats["top_sarkilar"][0] if stats["top_sarkilar"] else None
                # En çok dinlenen sanatçı
                top_sanatci = stats["top_sanatcilar"][0] if stats["top_sanatcilar"] else None
                # En çok dinlenen albüm
                top_album = stats["top_albumler"][0] if stats["top_albumler"] else None

                # Streak (gamification)
                streak_data = {"current": 0, "best": 0}
                try:
                    gami = compute_gamification(headers, rows)
                    streak_data = gami.get("streak", {"current": 0, "best": 0})
                except Exception:
                    pass

                entry = {
                    "user_id": user_id,
                    "display_name": name_map.get(user_id, user_id),
                    "is_me": (user_id == uid),
                    "toplam_sure_saat": round((stats.get("toplam_sure_sn") or 0) / 3600, 1),
                    "toplam_kayit": stats.get("toplam_kayit", 0),
                    "top_sarki": {
                        "ad": top_sarki["sarki"] if top_sarki else "—",
                        "sanatci": top_sarki["sanatci"] if top_sarki else "—",
                        "count": top_sarki["count"] if top_sarki else 0,
                    },
                    "top_sanatci": {
                        "ad": top_sanatci["sanatci"] if top_sanatci else "—",
                        "count": top_sanatci["count"] if top_sanatci else 0,
                    },
                    "top_album": {
                        "ad": top_album["album"] if top_album else "—",
                        "sanatci": top_album["sanatci"] if top_album else "—",
                        "count": top_album["count"] if top_album else 0,
                    },
                    "aktif_seri": streak_data.get("current", 0),
                    "en_uzun_seri": streak_data.get("best", 0),
                    "en_guclu_pet": pet_map.get(user_id),
                }
                results.append(entry)
            except Exception as e:
                logger.warning(f"Karsilastirma hatasi (uid={user_id}): {e}")
                continue

        return jsonify({
            "users": results,
            "katilimci_sayisi": len(results),
            "son_sync": config._last_sync,
        })
    except Exception as e:
        logger.error(f"Karsilastirma API hatasi: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/izin", methods=["POST"])
def api_izin():
    try:
        uid = get_current_user_id()
        name = get_current_user_name()
        if not uid:
            return jsonify({"error": "Giriş yapılmamış"}), 401
        data = request.get_json()
        allowed = bool(data.get("allowed", False))
        sheets.set_user_permission(uid, name, allowed)
        return jsonify({"status": "ok", "allowed": allowed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/izin")
def api_izin_get():
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify({"allowed": False})
        return jsonify({"allowed": sheets.get_user_permission(uid)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/collab-users")
def api_collab_users():
    """Settings'deki izin vermiş kullanıcıları {id, name} listesi olarak döner (kendisi hariç)."""
    try:
        uid = get_current_user_id()
        if not uid:
            return jsonify([])
        ws = sheets._find_sheet("Settings")
        if not ws:
            return jsonify([])
        users = []
        for row in ws.get_all_values()[1:]:
            if not row or len(row) < 3:
                continue
            row_uid  = row[0]
            row_name = row[1] or row_uid
            allowed  = row[2].lower() == "true"
            if allowed and row_uid != uid:
                users.append({"id": row_uid, "name": row_name})
        return jsonify(users)
    except Exception as e:
        logger.error(f"collab-users hata: {e}")
        return jsonify([])

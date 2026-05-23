"""
Pet Sistemi API Endpoint'leri
==============================
GET  /api/pets/state          → Tüm pet durumu (envanter, aktif, coin, bonus)
POST /api/pets/open           → Yumurta aç  {egg_type, count}
POST /api/pets/fusion         → Fusion dene {pet_ids: [id,id,id]}
POST /api/pets/equip          → Pet tak/çıkar {pet_id, slot (0-4)}
POST /api/pets/unequip        → Pet çıkar {pet_id}
"""

import json
import logging
import uuid
from flask import Blueprint, jsonify, request

from extensions import get_current_user_id, get_cached_data, load_user_data, sheets
from utils.pets import (
    open_eggs, attempt_fusion, calc_active_bonuses,
    compute_coins, calc_pet_level, EGG_COST, RARITIES, level_bonus,
)

logger = logging.getLogger(__name__)
bp = Blueprint("pets", __name__)

# ── Sheets Yardımcıları ──────────────────────────────────────────────────────

def _load_pet_data(uid: str) -> dict:
    """Sheets'ten pet verisini yükler. Yoksa varsayılan döner."""
    try:
        ws = sheets._find_sheet("Pets")
        if not ws:
            return _default_pet_data()
        rows = ws.get_all_values()
        for row in rows[1:]:
            if row and row[0] == uid:
                raw = row[2] if len(row) > 2 else "{}"
                try:
                    return json.loads(raw)
                except Exception:
                    return _default_pet_data()
    except Exception as e:
        logger.warning(f"Pet data yükleme hatası: {e}")
    return _default_pet_data()


def _save_pet_data(uid: str, display_name: str, data: dict):
    """Pet verisini Sheets'e kaydeder."""
    try:
        ws = _ensure_pets_sheet()
        if not ws:
            return
        raw      = json.dumps(data, ensure_ascii=False)
        summary  = _pet_summary(data)
        rows     = ws.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == uid:
                ws.update(f"A{i}:D{i}", [[uid, display_name, raw, summary]])
                return
        ws.append_row([uid, display_name, raw, summary], value_input_option="RAW")
    except Exception as e:
        logger.warning(f"Pet data kaydetme hatası: {e}")


def _ensure_pets_sheet():
    try:
        ws = sheets._find_sheet("Pets")
        if not ws:
            ws = sheets.sh.add_worksheet(title="Pets", rows=500, cols=4)
            ws.append_row(["user_id", "display_name", "data_json", "summary"], value_input_option="RAW")
            logger.info("✅ Pets sayfası oluşturuldu.")
        return ws
    except Exception as e:
        logger.error(f"Pets sheet oluşturulamadı: {e}")
        return None


def _pet_summary(data: dict) -> str:
    """Sheets'te okunabilir özet."""
    inv   = data.get("inventory", [])
    coins = data.get("coins", 0)
    active_count = sum(1 for p in inv if p.get("active"))
    return f"Coin:{coins} | Petler:{len(inv)} | Aktif:{active_count}"


def _default_pet_data() -> dict:
    return {
        "coins":     0,
        "inventory": [],  # [{id, rarity, egg_type, coin_mult, xp_mult, xp, active, slot}]
    }

# ── Coin Hesaplama ───────────────────────────────────────────────────────────

def _recalc_coins(uid: str, current_data: dict) -> int:
    """Tüm zamanlar verilerinden coin hesaplar (pet bonus dahil)."""
    headers, rows = get_cached_data(uid)
    if not rows:
        load_user_data(uid)
        headers, rows = get_cached_data(uid)

    active_pets = [p for p in current_data.get("inventory", []) if p.get("active")]
    bonuses     = calc_active_bonuses(active_pets)
    coins       = compute_coins(headers, rows, bonuses["coin_multiplier"])
    return coins


# ── Endpoint'ler ─────────────────────────────────────────────────────────────

@bp.route("/api/pets/debug")
def api_pets_debug():
    """Geçici debug endpoint — sorun tespiti için."""
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yok"}), 401
    try:
        headers, rows = get_cached_data(uid)
        if not rows:
            load_user_data(uid)
            headers, rows = get_cached_data(uid)
        return jsonify({
            "uid":          uid,
            "headers":      headers,
            "row_count":    len(rows),
            "sample_row":   rows[0] if rows else [],
            "sheets_ok":    sheets.sh is not None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/pets/state")
def api_pets_state():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    try:
        data = _load_pet_data(uid)

        # Coin hesapla — /api/dashboard ile ayni compute_stats verisinden,
        # Sheets'i tekrar taramaya gerek yok.
        from utils.helpers import compute_stats
        from utils.pets import compute_coins_from_stats
        headers, rows = get_cached_data(uid)
        if not rows:
            load_user_data(uid)
            headers, rows = get_cached_data(uid)
        _stats     = compute_stats(headers, rows) or {}
        base_coins = compute_coins_from_stats(_stats)

        inventory   = data.get('inventory', [])
        active_pets = [p for p in inventory if p.get('active')]
        bonuses     = calc_active_bonuses(active_pets)
        coins = int(base_coins * bonuses['coin_multiplier'])
        data['coins'] = coins

        # Pet level bilgilerini tazele
        for p in inventory:
            p["level_info"] = calc_pet_level(p.get("xp", 0))
            p["lv_bonus"]   = level_bonus(p["level_info"]["level"])

        logger.info(f"Pets state: uid={uid} coins={coins} pets={len(inventory)} active={len(active_pets)}")

        return jsonify({
            "coins":          coins,
            "base_coins":     base_coins,
            "inventory":      inventory,
            "active_bonuses": bonuses,
            "egg_costs":      EGG_COST,
            "max_active":     5,
        })
    except Exception as e:
        logger.error(f"Pets state hatası: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route("/api/pets/open", methods=["POST"])
def api_pets_open():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    try:
        from extensions import get_current_user_name
        body     = request.get_json(silent=True) or {}
        egg_type = body.get("egg_type", "normal")
        count    = int(body.get("count", 1))

        if egg_type not in EGG_COST:
            return jsonify({"error": "Geçersiz yumurta türü"}), 400
        if count < 1:
            return jsonify({"error": "Geçersiz adet"}), 400

        data          = _load_pet_data(uid)
        data["coins"] = _recalc_coins(uid, data)

        result = open_eggs(egg_type, count, data["coins"])

        if result.get("error"):
            return jsonify({"error": result["error"]}), 400

        # Yeni petleri envantere ekle
        for pet in result["results"]:
            pet["id"]     = str(uuid.uuid4())
            pet["active"] = False
            pet["slot"]   = None
            pet["xp"]     = 0
            pet["level_info"] = calc_pet_level(0)
            pet["lv_bonus"]   = 1.0
            data["inventory"].append(pet)

        data["coins"] = result["new_coins"]
        _save_pet_data(uid, get_current_user_name(), data)

        return jsonify({
            "opened":      result["results"],
            "coins_spent": result["coins_spent"],
            "coins":       data["coins"],
            "total_pets":  len(data["inventory"]),
        })
    except Exception as e:
        logger.error(f"Pets open hatası: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/pets/fusion", methods=["POST"])
def api_pets_fusion():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    try:
        from extensions import get_current_user_name
        body    = request.get_json(silent=True) or {}
        pet_ids = body.get("pet_ids", [])

        if len(pet_ids) != 3:
            return jsonify({"error": "Tam 3 pet seçmelisin"}), 400

        data      = _load_pet_data(uid)
        inventory = data.get("inventory", [])

        selected = [p for p in inventory if p.get("id") in pet_ids]
        if len(selected) != 3:
            return jsonify({"error": "Pet bulunamadı"}), 404

        # Aktif petler fusion'a gidemesin
        if any(p.get("active") for p in selected):
            return jsonify({"error": "Aktif petleri fusion için çıkar"}), 400

        egg_type = selected[0].get("egg_type", "normal")
        result   = attempt_fusion(selected, egg_type)

        # Consumed petleri envanterden sil
        consumed_ids = {p["id"] for p in selected}
        data["inventory"] = [p for p in inventory if p["id"] not in consumed_ids]

        if result["success"]:
            new_pet           = result["result_pet"]
            new_pet["id"]     = str(uuid.uuid4())
            new_pet["active"] = False
            new_pet["slot"]   = None
            new_pet["xp"]     = 0
            new_pet["level_info"] = calc_pet_level(0)
            new_pet["lv_bonus"]   = 1.0
            data["inventory"].append(new_pet)

        _save_pet_data(uid, get_current_user_name(), data)

        return jsonify({
            "success":    result["success"],
            "result_pet": result.get("result_pet"),
            "message":    "Fusion başarılı! Yeni pet kazandın! 🎉" if result["success"]
                          else "Fusion başarısız! 3 pet de gitti. 💔",
        })
    except Exception as e:
        logger.error(f"Pets fusion hatası: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/pets/equip", methods=["POST"])
def api_pets_equip():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    try:
        from extensions import get_current_user_name
        body   = request.get_json(silent=True) or {}
        pet_id = body.get("pet_id")
        slot   = int(body.get("slot", 0))

        data      = _load_pet_data(uid)
        inventory = data.get("inventory", [])

        active_count = sum(1 for p in inventory if p.get("active"))
        target = next((p for p in inventory if p["id"] == pet_id), None)
        if not target:
            return jsonify({"error": "Pet bulunamadı"}), 404
        if target.get("active"):
            return jsonify({"error": "Pet zaten aktif"}), 400
        if active_count >= 5:
            return jsonify({"error": "Maksimum 5 aktif pet"}), 400

        target["active"] = True
        target["slot"]   = slot
        _save_pet_data(uid, get_current_user_name(), data)

        active_pets = [p for p in data["inventory"] if p.get("active")]
        bonuses     = calc_active_bonuses(active_pets)
        return jsonify({"success": True, "active_bonuses": bonuses})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/pets/unequip", methods=["POST"])
def api_pets_unequip():
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giriş yapılmamış"}), 401
    try:
        from extensions import get_current_user_name
        body   = request.get_json(silent=True) or {}
        pet_id = body.get("pet_id")

        data      = _load_pet_data(uid)
        target    = next((p for p in data.get("inventory", []) if p["id"] == pet_id), None)
        if not target:
            return jsonify({"error": "Pet bulunamadı"}), 404

        target["active"] = False
        target["slot"]   = None
        _save_pet_data(uid, get_current_user_name(), data)

        active_pets = [p for p in data["inventory"] if p.get("active")]
        bonuses     = calc_active_bonuses(active_pets)
        return jsonify({"success": True, "active_bonuses": bonuses})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

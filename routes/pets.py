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

# In-memory pet cache — Sheets gecikme sorununu onler
_pet_cache: dict = {}

def _load_pet_data(uid: str) -> dict:
    """Once memory cache, sonra Sheets'ten pet verisini yukler."""
    if uid in _pet_cache:
        return json.loads(json.dumps(_pet_cache[uid]))  # deep copy
    try:
        ws = sheets._find_sheet("Pets")
        if not ws:
            return _default_pet_data()
        rows = ws.get_all_values()
        for row in rows[1:]:
            if row and row[0] == uid:
                raw = row[2] if len(row) > 2 else "{}"
                try:
                    data = json.loads(raw)
                    _pet_cache[uid] = json.loads(json.dumps(data))
                    return data
                except Exception:
                    return _default_pet_data()
    except Exception as e:
        logger.warning(f"Pet data yukleme hatasi: {e}")
    return _default_pet_data()


def _save_pet_data(uid: str, display_name: str, data: dict):
    """Pet verisini once memory cache'e, sonra Sheets'e kaydeder."""
    # Once cache'i guncelle — anlik yanit icin
    _pet_cache[uid] = json.loads(json.dumps(data))
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
        logger.warning(f"Pet data kaydetme hatasi: {e}")


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
        "coins":     0,   # sync'te biriken coin bakiyesi
        "xp":        0,   # sync'te biriken xp bakiyesi
        "inventory": [],
    }

# ── Coin Hesaplama ───────────────────────────────────────────────────────────

def _recalc_coins(uid: str, current_data: dict) -> int:
    """Artik dogrudan bakiyeyi dondurur — coin sync'te birikir."""
    return int(current_data.get("coins", 0))


def _update_snapshot(uid: str, data: dict):
    """
    Pet takildiginda/cikarildiginda snapshot'i guncelle.
    snapshot = su anki base_coins, snapshot_multiplier = yeni carpan.
    Boylece carpan sadece bu andan sonraki kazanimlara uygulanir.
    """
    from utils.helpers import compute_stats
    from utils.pets import compute_coins_from_stats
    headers, rows = get_cached_data(uid)
    if not rows:
        load_user_data(uid)
        headers, rows = get_cached_data(uid)
    _stats     = compute_stats(headers, rows) or {}
    base_coins = compute_coins_from_stats(_stats)

    active_pets = [p for p in data.get('inventory', []) if p.get('active')]
    bonuses     = calc_active_bonuses(active_pets)

    # Mevcut net coini koru: onceki birikim + (snapshot'tan sonraki * eski carpan) - spent
    old_snapshot  = data.get('base_snapshot', 0)
    old_mult      = data.get('snapshot_multiplier', 1.0)
    spent         = data.get('spent_coins', 0)
    new_since_old = max(0, base_coins - old_snapshot)
    current_coins = max(0, int(old_snapshot * old_mult) + int(new_since_old * old_mult) - spent)

    # Yeni snapshot: su anki base. Yeni spent: mevcut coini koruyacak sekilde ayarla
    # current_coins = base_coins * new_mult - new_spent
    # new_spent = base_coins * new_mult - current_coins
    new_mult  = bonuses['coin_multiplier']
    new_spent = max(0, int(base_coins * new_mult) - current_coins)

    data['base_snapshot']       = base_coins
    data['snapshot_multiplier'] = new_mult
    data['spent_coins']         = new_spent



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
        # Coin ve XP dogrudan bakiyeden okunur — sync aninda birikir
        inventory   = data.get('inventory', [])
        active_pets = [p for p in inventory if p.get('active')]
        bonuses     = calc_active_bonuses(active_pets)
        coins = int(data.get('coins', 0))
        # Pet level bilgilerini tazele
        for p in inventory:
            p["level_info"] = calc_pet_level(p.get("xp", 0))
            p["lv_bonus"]   = level_bonus(p["level_info"]["level"])

        logger.info(f"Pets state: uid={uid} coins={coins} pets={len(inventory)} active={len(active_pets)}")

        return jsonify({
            "coins":          coins,
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
        # Bakiye dogrudan data["coins"]'dan gelir

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

        # coin harcama
        data["coins"] = max(0, int(data.get("coins", 0)) - result["coins_spent"])
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




@bp.route("/api/pets/auto-fusion", methods=["POST"])
def api_pets_auto_fusion():
    """
    Destekleyen (non-diamond, non-active) petleri otomatik fusion yapar.
    Her turde rastgele 3 eslesen pet secilir, fusion denenir.
    Basarili fusion sonucu yeni pet eklenir, eskiler silinir.
    Donüs: {attempts, successes, new_pets, remaining}
    """
    uid = get_current_user_id()
    if not uid:
        return jsonify({"error": "Giris yapilmamis"}), 401
    try:
        from extensions import get_current_user_name
        from utils.pets import attempt_fusion, FUSION_ROUTES
        data      = _load_pet_data(uid)
        inventory = data.get("inventory", [])

        # Aktif olmayanlar ve diamond olmayanlari al
        candidates = [p for p in inventory if not p.get("active") and p.get("egg_type") in FUSION_ROUTES]

        # (rarity, egg_type) gruplarina gore grupla
        from collections import defaultdict
        groups = defaultdict(list)
        for p in candidates:
            groups[(p["rarity"], p["egg_type"])].append(p)

        attempts   = 0
        successes  = 0
        new_pets   = []
        removed_ids = set()

        for key, pets in groups.items():
            # Her gruptan 3'er 3'er al
            while len(pets) >= 3:
                trio   = pets[:3]
                pets   = pets[3:]
                result = attempt_fusion(trio, trio[0]["egg_type"])
                attempts += 1
                for p in trio:
                    removed_ids.add(p["id"])
                if result["success"]:
                    successes += 1
                    np = result["result_pet"]
                    np["id"]         = str(uuid.uuid4())
                    np["active"]     = False
                    np["slot"]       = None
                    np["xp"]         = 0
                    np["level_info"] = calc_pet_level(0)
                    np["lv_bonus"]   = 1.0
                    new_pets.append(np)

        # Envantere uygula
        data["inventory"] = [p for p in inventory if p["id"] not in removed_ids] + new_pets
        _save_pet_data(uid, get_current_user_name(), data)

        return jsonify({
            "attempts":  attempts,
            "successes": successes,
            "new_pets":  new_pets,
            "remaining": len(data["inventory"]),
        })
    except Exception as e:
        logger.error(f"Auto-fusion hatasi: {e}")
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
        _update_snapshot(uid, data)
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
        _update_snapshot(uid, data)
        _save_pet_data(uid, get_current_user_name(), data)

        active_pets = [p for p in data["inventory"] if p.get("active")]
        bonuses     = calc_active_bonuses(active_pets)
        return jsonify({"success": True, "active_bonuses": bonuses})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

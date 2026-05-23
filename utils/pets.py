"""
Pet Sistemi Motoru
==================
Yumurta açma, fusion, coin/XP çarpanları, seviye hesaplama.

Yumurta Türleri: normal | golden | diamond
Nadirlikler: Common, Rare, Epic, Legendary, Mysterious, Cosmic(sadece diamond)

Coin Kazanımı (gamification.py ile entegre):
  +1 coin / 5 dakika dinlenme
  +10 coin / farklı şarkı veya albüm
  +20 coin / farklı sanatçı

Yumurta Maliyeti: normal=5, golden=25, diamond=100
"""

import random
import math

# ── Nadirlik Tanımları ───────────────────────────────────────────────────────

RARITIES = {
    "Common":     {"color": "#9ca3af", "icon": "⬜", "tier": 1},
    "Rare":       {"color": "#3b82f6", "icon": "🔷", "tier": 2},
    "Epic":       {"color": "#8b5cf6", "icon": "💜", "tier": 3},
    "Legendary":  {"color": "#f59e0b", "icon": "🌟", "tier": 4},
    "Mysterious": {"color": "#ec4899", "icon": "🌸", "tier": 5},
    "Cosmic":     {"color": "#06b6d4", "icon": "🌌", "tier": 6},
}

# ── Yumurta Havuzları ────────────────────────────────────────────────────────

EGG_POOLS = {
    "normal": [
        ("Common",     60.0),
        ("Rare",       25.0),
        ("Epic",       10.0),
        ("Legendary",   4.0),
        ("Mysterious",  1.0),
    ],
    "golden": [
        ("Common",     60.0),
        ("Rare",       25.0),
        ("Epic",       10.0),
        ("Legendary",   4.0),
        ("Mysterious",  1.0),
    ],
    "diamond": [
        ("Common",     60.0),
        ("Rare",       25.0),
        ("Epic",       10.0),
        ("Legendary",   4.0),
        ("Mysterious",  0.9),
        ("Cosmic",      0.1),
    ],
}

EGG_COST = {"normal": 5, "golden": 25, "diamond": 100}

# ── Baz Çarpanlar (normal tier) ──────────────────────────────────────────────

BASE_MULTIPLIERS = {
    #              coin_mult  xp_mult
    "Common":     (1.05,      1.10),
    "Rare":       (1.25,      1.50),
    "Epic":       (1.50,      2.00),
    "Legendary":  (2.00,      3.00),
    "Mysterious": (6.00,     10.00),
    "Cosmic":     (60.00,   100.00),
}

# Tier çarpanları (normal→golden 1.5x, golden→diamond 2.0x)
TIER_MULT = {"normal": 1.0, "golden": 1.5, "diamond": 3.0}  # 1.5 * 2.0 = 3.0

MAX_ACTIVE_PETS = 5
MAX_PET_LEVEL   = 100
PET_LEVEL_XP_PER_MINUTE = 1  # Kullanıcı ile aynı oran, coin kazandıkça artar

# ── Yardımcı Fonksiyonlar ────────────────────────────────────────────────────

def get_multipliers(rarity: str, egg_type: str) -> tuple[float, float]:
    """Nadirlik + yumurta tipine göre coin ve XP çarpanlarını döndürür."""
    base_coin, base_xp = BASE_MULTIPLIERS.get(rarity, (1.0, 1.0))
    tier_factor = TIER_MULT.get(egg_type, 1.0)
    return round(base_coin * tier_factor, 2), round(base_xp * tier_factor, 2)


def level_bonus(level: int) -> float:
    """
    Her 10 seviyede çarpan +0.05 artar (1.05x baz).
    Max bonus: +0.50 (seviye 100'de 1.50x)
    """
    bonus = min((level // 10) * 0.05, 0.50)
    return round(1.0 + bonus, 3)


def get_pet_xp_for_level(level: int) -> int:
    """Pet'in level numaralı seviyeye ulaşmak için gereken toplam XP."""
    if level <= 0:
        return 0
    total = 0
    for lv in range(1, level + 1):
        total += 100 + (lv - 1) * 5
    return total


def calc_pet_level(xp: int) -> dict:
    level = 0
    while level < MAX_PET_LEVEL:
        needed = get_pet_xp_for_level(level + 1)
        if xp < needed:
            break
        level += 1
    level_start = get_pet_xp_for_level(level)
    level_end   = get_pet_xp_for_level(level + 1) if level < MAX_PET_LEVEL else level_start
    xp_in       = xp - level_start
    xp_needed   = level_end - level_start
    pct         = round(xp_in / xp_needed * 100, 1) if xp_needed else 100
    return {
        "level":     level,
        "xp":        xp,
        "xp_in":     xp_in,
        "xp_needed": xp_needed,
        "pct":       pct,
        "max":       level >= MAX_PET_LEVEL,
    }


# ── Yumurta Açma ────────────────────────────────────────────────────────────

def roll_rarity(egg_type: str) -> str:
    pool = EGG_POOLS.get(egg_type, EGG_POOLS["normal"])
    rarities = [r for r, _ in pool]
    weights  = [w for _, w in pool]
    return random.choices(rarities, weights=weights, k=1)[0]


def open_eggs(egg_type: str, count: int, current_coins: int) -> dict:
    """
    count adet yumurta aç.
    Dönüş: {results: [{rarity, coin_mult, xp_mult, pet_id}], coins_spent, new_coins}
    """
    cost_each  = EGG_COST[egg_type]
    affordable = min(count, current_coins // cost_each)
    if affordable == 0:
        return {"results": [], "coins_spent": 0, "new_coins": current_coins, "error": "Yetersiz coin"}

    results    = []
    coins_used = 0
    for _ in range(affordable):
        rarity    = roll_rarity(egg_type)
        coin_m, xp_m = get_multipliers(rarity, egg_type)
        results.append({
            "rarity":    rarity,
            "egg_type":  egg_type,
            "coin_mult": coin_m,
            "xp_mult":   xp_m,
            "xp":        0,
            "level_info": calc_pet_level(0),
        })
        coins_used += cost_each

    return {
        "results":     results,
        "coins_spent": coins_used,
        "new_coins":   current_coins - coins_used,
    }


# ── Fusion ───────────────────────────────────────────────────────────────────

FUSION_ROUTES = {
    "normal": "golden",
    "golden": "diamond",
}
FUSION_SUCCESS_RATE = 0.20  # %20


def attempt_fusion(pets: list, egg_type: str) -> dict:
    """
    3 aynı nadirlik + aynı egg_type'tan fusion dener.
    pets: [{rarity, egg_type, coin_mult, xp_mult, xp, ...}, ...]  — tam olarak 3 pet
    Dönüş: {success, result_pet | None, consumed_pets}
    """
    if len(pets) != 3:
        return {"success": False, "error": "Tam 3 pet gerekli"}

    rarity   = pets[0]["rarity"]
    src_type = pets[0]["egg_type"]
    dst_type = FUSION_ROUTES.get(src_type)

    if not dst_type:
        return {"success": False, "error": "Diamond petler daha da yükseltilemez"}

    if not all(p["rarity"] == rarity and p["egg_type"] == src_type for p in pets):
        return {"success": False, "error": "Tüm petler aynı nadirlik ve türde olmalı"}

    success = random.random() < FUSION_SUCCESS_RATE

    if success:
        coin_m, xp_m = get_multipliers(rarity, dst_type)
        result = {
            "rarity":    rarity,
            "egg_type":  dst_type,
            "coin_mult": coin_m,
            "xp_mult":   xp_m,
            "xp":        0,
            "level_info": calc_pet_level(0),
        }
        return {"success": True, "result_pet": result, "consumed_pets": pets}
    else:
        return {"success": False, "result_pet": None, "consumed_pets": pets}


# ── Aktif Pet Çarpanları ─────────────────────────────────────────────────────

def calc_active_bonuses(active_pets: list) -> dict:
    """
    En fazla MAX_ACTIVE_PETS peti değerlendirir.
    Tüm çarpanlar toplanır (çarpılmaz).
    Pet yoksa 1.0x baz çarpan döner (sıfır değil!).
    """
    if not active_pets:
        return {"coin_multiplier": 1.0, "xp_multiplier": 1.0}

    total_coin = 0.0
    total_xp   = 0.0
    for pet in active_pets[:MAX_ACTIVE_PETS]:
        lv_bonus = level_bonus(pet.get("level_info", {}).get("level", 0))
        total_coin += pet["coin_mult"] * lv_bonus
        total_xp   += pet["xp_mult"]  * lv_bonus
    return {
        "coin_multiplier": round(max(total_coin, 1.0), 3),
        "xp_multiplier":   round(max(total_xp,   1.0), 3),
    }


# ── Coin Hesaplama ───────────────────────────────────────────────────────────

def compute_coins(headers: list, rows: list, coin_multiplier: float = 1.0) -> int:
    """
    Dinleme verilerinden base coin hesaplar.
    +1/5 dakika, +10/farklı şarkı veya albüm, +20/farklı sanatçı
    coin_multiplier: aktif pet bonusu
    """
    if not headers or not rows:
        return 0

    # Header eşleşmesi — strip + lower ile encoding sorunlarına karşı güvenli
    h_lower = [str(h).strip().lower() for h in headers]

    def find_col(names):
        for name in names:
            try:
                return h_lower.index(name.lower())
            except ValueError:
                continue
        return -1

    idx_sarki   = find_col(["şarkı adı", "sarki adi", "track_name", "şarkı"])
    idx_sanatci = find_col(["sanatçı", "sanatci", "artist_name", "sanatçı adı"])
    idx_sure    = find_col(["süre (sn)", "sure (sn)", "duration_sec", "süre"])
    idx_album   = find_col(["albüm", "album", "albüm adı"])

    if idx_sarki == -1 or idx_sanatci == -1 or idx_sure == -1:
        return 0

    total_sn   = 0
    unique_s   = set()
    unique_art = set()
    unique_alb = set()

    for row in rows:
        max_needed = max(idx_sarki, idx_sanatci, idx_sure)
        if len(row) <= max_needed:
            continue
        try:
            total_sn += int(row[idx_sure])
        except Exception:
            pass
        sarki   = str(row[idx_sarki]).strip()
        sanatci = str(row[idx_sanatci]).strip()
        album   = str(row[idx_album]).strip() if idx_album != -1 and len(row) > idx_album else ""
        if sarki:   unique_s.add(sarki)
        if sanatci: unique_art.add(sanatci)
        if album:   unique_alb.add(album)

    base_coins = (
        (total_sn // 300)         # +1 / 5 dk
        + len(unique_s)   * 10   # +10 / farklı şarkı
        + len(unique_alb) * 10   # +10 / farklı albüm
        + len(unique_art) * 20   # +20 / farklı sanatçı
    )
    return int(base_coins * coin_multiplier)

# utils/

Hesaplama ve yardımcı modüller. Saf Python, Flask bağımsız.

## gamification.py
XP ve seviye sistemi.
- `calc_level(xp)` — XP'den seviye hesaplar
- `compute_gamification(headers, rows)` — ham veriden XP/streak hesaplar
- `compute_xp_from_stats(stats)` — stats dict'inden XP hesaplar

**XP kuralları:**
- Her kayıt: 1 XP × xp_multiplier

## pets.py
Pet sistemi mantığı.
- `open_eggs(egg_type, count, coins)` — yumurta açar, rarity belirler
- `attempt_fusion(pets, egg_type)` — %20 şans ile fusion dener
- `calc_active_bonuses(active_pets)` — aktif pet çarpanlarını toplar
- `compute_coins_from_stats(stats)` — stats'tan coin hesaplar
- `calc_pet_level(xp)` — pet seviyesi

**Coin kuralları:**
- Her yeni kayıt: 1 coin × coin_multiplier

**Rarity şansları (Normal/Golden):** Common 60%, Rare 25%, Epic 10%, Legendary 4%, Mysterious 1%
**Diamond ekstra:** Cosmic 0.1%

## helpers.py
Genel yardımcı fonksiyonlar.
- `compute_stats(headers, rows)` — genel istatistikleri hesaplar
- `fmt_sure(sn)` — saniyeleri insan okunur formata çevirir

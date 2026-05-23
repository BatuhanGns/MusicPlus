"""
Gamification motoru: XP, Seviye, Seri, Mastery hesaplama.

XP Sistemi:
  +1 XP / dakika müzik
  +25 XP / farklı şarkı
  +25 XP / farklı sanatçı
  +50 XP / farklı albüm

Seviye Eşikleri:
  Seviye 1→2 için baz 1000 XP
  0-100 arası: her seviye için +50 XP eklenir
  101-500 arası: her seviye için +100 XP eklenir
  501-1000 arası: her seviye için +250 XP eklenir
  1000'den sonra XP birikmeye devam eder ama seviye sabit kalır

Mastery (Yıllık, en çok dinlenen şarkı/sanatçı/albüm için):
  Acemi   > 1.000 dinlenme
  Keşifçi > 2.500 dinlenme
  Gurme   > 5.000 dinlenme
  Otorite > 10.000 dinlenme

Seri:
  Her takvim günü en az 1 dinleme var mı kontrol eder (UTC)
"""

from collections import defaultdict
from datetime import datetime, timezone, timedelta


# ── Seviye Hesaplama ─────────────────────────────────────────────────────────

def xp_for_level(level: int) -> int:
    """level numaralı seviyeye ulaşmak için gereken TOPLAM XP."""
    if level <= 0:
        return 0
    total = 0
    base  = 1000  # Seviye 1→2 için gereken Baz XP
    for lv in range(1, level + 1):
        if lv <= 100:
            total += base + (lv - 1) * 50
        elif lv <= 500:
            # 101-500 arası: ilk 100 seviyenin artışı (99*50) + yeni seviyenin artışı
            total += base + (99 * 50) + (lv - 100) * 100
        else:
            # 501-1000 arası: 100'e kadar olan (99*50) + 500'e kadar olan (400*100) + yeni artış
            total += base + (99 * 50) + (400 * 100) + (lv - 500) * 250
    return total

MAX_LEVEL = 1000

def calc_level(total_xp: int) -> dict:
    """
    Toplam XP'den mevcut seviye, bu seviyedeki XP ve bir sonraki seviye için gereken XP döner.
    Seviye 1000'de max'a ulaşır, XP artmaya devam eder.
    """
    level = 0
    while level < MAX_LEVEL:
        needed = xp_for_level(level + 1)
        if total_xp < needed:
            break
        level += 1

    if level >= MAX_LEVEL:
        return {
            "level":        MAX_LEVEL,
            "current_xp":   total_xp,
            "level_xp":     total_xp,
            "next_xp":      xp_for_level(MAX_LEVEL),
            "xp_in_level":  total_xp - xp_for_level(MAX_LEVEL),
            "xp_needed":    0,
            "pct":          100,
            "max":          True,
        }

    level_start = xp_for_level(level)
    level_end   = xp_for_level(level + 1)
    xp_in_level = total_xp - level_start
    xp_needed   = level_end - level_start
    pct         = round(xp_in_level / xp_needed * 100, 1) if xp_needed else 100

    return {
        "level":       level,
        "current_xp":  total_xp,
        "level_xp":    level_start,
        "next_xp":     level_end,
        "xp_in_level": xp_in_level,
        "xp_needed":   xp_needed,
        "pct":         pct,
        "max":         False,
    }


# ── Mastery ──────────────────────────────────────────────────────────────────

MASTERY_TIERS = [
    (10000, "Otorite",  "#f59e0b", "👑"),
    (5000,  "Gurme",    "#8b5cf6", "💎"),
    (2500,  "Keşifçi",  "#3b82f6", "🔭"),
    (1000,  "Acemi",    "#22c55e", "🌱"),
]

def get_mastery(count: int):
    for threshold, name, color, icon in MASTERY_TIERS:
        if count >= threshold:
            return {"name": name, "color": color, "icon": icon, "threshold": threshold}
    return None


# ── Seri Hesaplama ────────────────────────────────────────────────────────────

def calc_streak(daily_dates: set) -> dict:
    """
    Dinleme olan takvim günlerinden mevcut seri ve max seri hesaplar.
    daily_dates: "YYYY-MM-DD" string'lerinden oluşan set
    """
    if not daily_dates:
        return {"current": 0, "best": 0, "today": False}

    today = datetime.now(timezone.utc).date()
    sorted_dates = sorted(daily_dates, reverse=True)

    # Mevcut seri
    current = 0
    check = today
    for d_str in sorted_dates:
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except Exception:
            continue
        if d == check:
            current += 1
            check -= timedelta(days=1)
        elif d < check:
            break

    # Eğer bugün dinleme yok ama dün varsa seriyi devam ettir
    if current == 0:
        check = today - timedelta(days=1)
        for d_str in sorted_dates:
            try:
                d = datetime.strptime(d_str, "%Y-%m-%d").date()
            except Exception:
                continue
            if d == check:
                current += 1
                check -= timedelta(days=1)
            elif d < check:
                break

    # En iyi seri
    all_dates = sorted(daily_dates)
    best = 0
    streak = 1
    for i in range(1, len(all_dates)):
        try:
            prev = datetime.strptime(all_dates[i-1], "%Y-%m-%d").date()
            curr = datetime.strptime(all_dates[i],   "%Y-%m-%d").date()
            if (curr - prev).days == 1:
                streak += 1
                best = max(best, streak)
            else:
                streak = 1
        except Exception:
            streak = 1
    best = max(best, streak, current)

    today_str = today.strftime("%Y-%m-%d")
    return {
        "current": current,
        "best":    best,
        "today":   today_str in daily_dates,
    }


# ── Ana Hesaplama ─────────────────────────────────────────────────────────────

def compute_gamification(headers, rows) -> dict:
    """
    Verilen tüm zamanlar (filtresiz!) verilerden gamification durumunu hesaplar.
    Her zaman tumzamanlar verisi üzerinden çalışmalıdır.
    """
    if not rows:
        return _empty()

    # Sütun indexleri
    try:
        idx_sarki   = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure    = headers.index("Süre (sn)")
        idx_iso     = headers.index("_played_at_iso") if "_played_at_iso" in headers else -1
        idx_album   = next(
            (i for i, h in enumerate(headers) if h.strip() in ("Albüm","Album","albüm","album")),
            -1,
        )
    except ValueError:
        return _empty()

    # ── Toplama ─────────────────────────────────────────────
    toplam_sure_sn  = 0
    unique_tracks   = set()
    unique_artists  = set()
    unique_albums   = set()
    daily_dates     = set()

    # Yıllık sayaçlar: {yil: {sarki: count, ...}}
    yearly_track   = defaultdict(lambda: defaultdict(int))
    yearly_artist  = defaultdict(lambda: defaultdict(int))
    yearly_album   = defaultdict(lambda: defaultdict(int))

    for row in rows:
        if len(row) <= max(idx_sarki, idx_sanatci, idx_sure):
            continue

        sarki   = row[idx_sarki].strip()
        sanatci = row[idx_sanatci].strip()
        album   = row[idx_album].strip() if idx_album != -1 and len(row) > idx_album else ""
        iso     = row[idx_iso].strip() if idx_iso != -1 and len(row) > idx_iso else ""

        try:
            sure = int(row[idx_sure])
        except Exception:
            sure = 0

        toplam_sure_sn += sure
        if sarki:   unique_tracks.add(sarki)
        if sanatci: unique_artists.add(sanatci)
        if album:   unique_albums.add(album)

        if iso and iso != "—":
            try:
                dt    = datetime.strptime(iso[:10], "%Y-%m-%d")
                yil   = str(dt.year)
                d_str = iso[:10]
                daily_dates.add(d_str)
                if sarki:   yearly_track[yil][sarki]   += 1
                if sanatci: yearly_artist[yil][sanatci] += 1
                if album:   yearly_album[yil][album]    += 1
            except Exception:
                pass

    # ── XP ──────────────────────────────────────────────────
    xp_dakika   = toplam_sure_sn // 60
    xp_sarki    = len(unique_tracks)  * 25
    xp_sanatci  = len(unique_artists) * 25
    xp_album    = len(unique_albums)  * 50
    total_xp    = xp_dakika + xp_sarki + xp_sanatci + xp_album

    level_info = calc_level(total_xp)
    level_info["xp_breakdown"] = {
        "dakika":  xp_dakika,
        "sarki":   xp_sarki,
        "sanatci": xp_sanatci,
        "album":   xp_album,
    }

    # ── Seri ────────────────────────────────────────────────
    streak = calc_streak(daily_dates)

    # ── Mastery (her yıl için ayrı) ─────────────────────────
    masteries = []
    all_years = sorted(set(yearly_track) | set(yearly_artist) | set(yearly_album))

    for yil in all_years:
        # Şarkı
        if yearly_track[yil]:
            top_sarki  = max(yearly_track[yil], key=yearly_track[yil].get)
            top_s_cnt  = yearly_track[yil][top_sarki]
            m = get_mastery(top_s_cnt)
            if m:
                masteries.append({
                    "yil":   yil, "tip": "Şarkı", "isim": top_sarki,
                    "count": top_s_cnt, **m
                })
        # Sanatçı
        if yearly_artist[yil]:
            top_san    = max(yearly_artist[yil], key=yearly_artist[yil].get)
            top_san_c  = yearly_artist[yil][top_san]
            m = get_mastery(top_san_c)
            if m:
                masteries.append({
                    "yil":   yil, "tip": "Sanatçı", "isim": top_san,
                    "count": top_san_c, **m
                })
        # Albüm
        if yearly_album[yil]:
            top_alb    = max(yearly_album[yil], key=yearly_album[yil].get)
            top_alb_c  = yearly_album[yil][top_alb]
            m = get_mastery(top_alb_c)
            if m:
                masteries.append({
                    "yil":   yil, "tip": "Albüm", "isim": top_alb,
                    "count": top_alb_c, **m
                })

    return {
        "xp":        total_xp,
        "level":     level_info,
        "streak":    streak,
        "masteries": masteries,
        "xp_breakdown": level_info["xp_breakdown"],
    }


def _empty():
    return {
        "xp":        0,
        "level":     calc_level(0),
        "streak":    {"current": 0, "best": 0, "today": False},
        "masteries": [],
        "xp_breakdown": {"dakika": 0, "sarki": 0, "sanatci": 0, "album": 0},
    }


def compute_xp_from_stats(stats: dict) -> dict:
    """
    /api/dashboard ciktisindaki hazir stats verisinden XP ve seviye hesaplar.
    Sheets'i tekrar taramaya gerek yok.
    Streak ve mastery icin hala ham veri gerekir; bunlar ayri tutulur.
      stats: compute_stats() ciktisi
    """
    total_sn   = stats.get("toplam_sure_sn", 0)
    farkli_s   = stats.get("farkli_sarki",   0)
    farkli_art = stats.get("farkli_sanatci", 0)
    farkli_alb = stats.get("farkli_album",   0)

    xp_dakika  = total_sn // 60
    xp_sarki   = farkli_s   * 25
    xp_sanatci = farkli_art * 25
    xp_album   = farkli_alb * 50
    total_xp   = xp_dakika + xp_sarki + xp_sanatci + xp_album

    level_info = calc_level(total_xp)
    level_info["xp_breakdown"] = {
        "dakika":  xp_dakika,
        "sarki":   xp_sarki,
        "sanatci": xp_sanatci,
        "album":   xp_album,
    }
    return {
        "xp":           total_xp,
        "level":        level_info,
        "xp_breakdown": level_info["xp_breakdown"],
    }

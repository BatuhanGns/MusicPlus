"""
Gamification motoru: XP, Seviye, Seri hesaplama.

XP Sistemi:
  +1  XP / dakika müzik
  +25 XP / farklı şarkı
  +50 XP / farklı sanatçı
  +25 XP / farklı albüm

Seviye Eşikleri:
  Seviye 1→2 için baz 1000 XP
  0-100 arası  : her seviye için +50 XP eklenir
  101-500 arası: her seviye için +100 XP eklenir
  501-1000 arası: her seviye için +250 XP eklenir
  1000'den sonra XP birikmeye devam eder ama seviye sabit kalır

Seri:
  Her takvim günü en az 1 dinleme var mı kontrol eder (UTC)
"""

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
            total += base + (99 * 50) + (lv - 100) * 100
        else:
            total += base + (99 * 50) + (400 * 100) + (lv - 500) * 250
    return total


MAX_LEVEL = 1000


def calc_level(total_xp: int) -> dict:
    """
    Toplam XP'den mevcut seviye, bu seviyedeki XP ve
    bir sonraki seviye için gereken XP döner.
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
            "level":       MAX_LEVEL,
            "current_xp":  total_xp,
            "level_xp":    xp_for_level(MAX_LEVEL),
            "next_xp":     xp_for_level(MAX_LEVEL),
            "xp_in_level": total_xp - xp_for_level(MAX_LEVEL),
            "xp_needed":   0,
            "pct":         100,
            "max":         True,
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


# ── Seri Hesaplama ────────────────────────────────────────────────────────────

def calc_streak(daily_dates: set) -> dict:
    """
    Dinleme olan takvim günlerinden mevcut seri ve max seri hesaplar.
    daily_dates: "YYYY-MM-DD" string'lerinden oluşan set
    """
    if not daily_dates:
        return {"current": 0, "best": 0, "today": False}

    today        = datetime.now(timezone.utc).date()
    sorted_dates = sorted(daily_dates, reverse=True)

    # Mevcut seri — bugünden geriye doğru
    current = 0
    check   = today
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

    # Bugün dinleme yoksa dünden başlat (seriyi kırmamak için)
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
    best   = 0
    streak = 1
    for i in range(1, len(all_dates)):
        try:
            prev = datetime.strptime(all_dates[i - 1], "%Y-%m-%d").date()
            curr = datetime.strptime(all_dates[i],     "%Y-%m-%d").date()
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
    Ham Sheets satırlarından gamification durumunu hesaplar.
    Her zaman filtresiz (tumzamanlar) veri üzerinden çalışmalıdır.
    """
    if not rows:
        return _empty()

    # Sütun indexleri
    try:
        idx_sarki   = headers.index("Şarkı Adı")
        idx_sanatci = headers.index("Sanatçı")
        idx_sure    = headers.index("Süre (ms)")
        idx_iso     = headers.index("_played_at_iso") if "_played_at_iso" in headers else -1
        idx_album   = next(
            (i for i, h in enumerate(headers) if h.strip() in ("Albüm", "Album", "albüm", "album")),
            -1,
        )
    except ValueError:
        return _empty()

    toplam_sure_sn = 0
    unique_tracks  = set()
    unique_artists = set()
    unique_albums  = set()
    daily_dates    = set()

    for row in rows:
        if len(row) <= max(idx_sarki, idx_sanatci, idx_sure):
            continue

        sarki   = row[idx_sarki].strip()
        sanatci = row[idx_sanatci].strip()
        album   = row[idx_album].strip() if idx_album != -1 and len(row) > idx_album else ""
        iso     = row[idx_iso].strip()   if idx_iso   != -1 and len(row) > idx_iso   else ""

        try:
            sure = int(row[idx_sure]) // 1000
        except Exception:
            sure = 0

        toplam_sure_sn += sure
        if sarki:   unique_tracks.add(sarki)
        if sanatci: unique_artists.add(sanatci)
        if album:   unique_albums.add(album)

        if iso and iso != "—":
            try:
                datetime.strptime(iso[:10], "%Y-%m-%d")  # format kontrolü
                daily_dates.add(iso[:10])
            except Exception:
                pass

    # XP
    xp_dakika  = toplam_sure_sn // 60       # +1  XP / dakika
    xp_sarki   = len(unique_tracks)  * 25   # +25 XP / farklı şarkı
    xp_sanatci = len(unique_artists) * 50   # +50 XP / farklı sanatçı
    xp_album   = len(unique_albums)  * 25   # +25 XP / farklı albüm
    total_xp   = xp_dakika + xp_sarki + xp_sanatci + xp_album

    xp_breakdown = {
        "dakika":  xp_dakika,
        "sarki":   xp_sarki,
        "sanatci": xp_sanatci,
        "album":   xp_album,
    }

    level_info = calc_level(total_xp)
    level_info["xp_breakdown"] = xp_breakdown

    streak = calc_streak(daily_dates)

    return {
        "xp":          total_xp,
        "level":       level_info,
        "streak":      streak,
        "xp_breakdown": xp_breakdown,
    }


def _empty() -> dict:
    level_info = calc_level(0)
    xp_breakdown = {"dakika": 0, "sarki": 0, "sanatci": 0, "album": 0}
    level_info["xp_breakdown"] = xp_breakdown
    return {
        "xp":          0,
        "level":       level_info,
        "streak":      {"current": 0, "best": 0, "today": False},
        "xp_breakdown": xp_breakdown,
    }


def compute_xp_from_stats(stats: dict) -> dict:
    """
    /api/dashboard çıktısındaki hazır stats verisinden XP ve seviye hesaplar.
    Sheets'i tekrar taramaya gerek yoktur.
    """
    total_sn   = stats.get("toplam_sure_sn", 0)
    farkli_s   = stats.get("farkli_sarki",   0)
    farkli_art = stats.get("farkli_sanatci", 0)
    farkli_alb = stats.get("farkli_album",   0)

    xp_dakika  = total_sn  // 60   # +1  XP / dakika
    xp_sarki   = farkli_s   * 25   # +25 XP / farklı şarkı
    xp_sanatci = farkli_art * 50   # +50 XP / farklı sanatçı
    xp_album   = farkli_alb * 25   # +25 XP / farklı albüm
    total_xp   = xp_dakika + xp_sarki + xp_sanatci + xp_album

    xp_breakdown = {
        "dakika":  xp_dakika,
        "sarki":   xp_sarki,
        "sanatci": xp_sanatci,
        "album":   xp_album,
    }

    level_info = calc_level(total_xp)
    level_info["xp_breakdown"] = xp_breakdown

    return {
        "xp":          total_xp,
        "level":       level_info,
        "xp_breakdown": xp_breakdown,
    }

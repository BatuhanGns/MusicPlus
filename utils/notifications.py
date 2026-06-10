"""
Bildirim kontrolcüsü.
APScheduler job'ı tarafından her gün 18:00 UTC'de çağrılır.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def run_notifications():
    """
    Tüm kullanıcılar için bildirim kontrolü yapar.
    - Streak uyarısı  (bugün dinleme yoksa, gece 00:00 UTC'den 6 saat önce = 18:00 UTC)
    - Spotify ödeme hatırlatması (ödeme günü - 1)
    - Haftalık/aylık özet (Pazar 18:00 UTC / ayın 1'i 18:00 UTC)
    """
    from extensions import sheets
    from clients.mail_client import (
        send_mail,
        mail_streak_uyari,
        mail_spotify_odeme,
        mail_haftalik_ozet,
        mail_aylik_ozet,
    )

    now_utc   = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")

    try:
        users = sheets.get_notification_users()
    except Exception as e:
        logger.error(f"Bildirim kullanıcıları alınamadı: {e}")
        return

    for u in users:
        uid          = u.get("user_id", "")
        email        = u.get("email", "").strip()
        display_name = u.get("display_name", "Kullanıcı")

        if not email:
            continue

        streak_acik = u.get("streak_bildirimi", "false").lower() == "true"
        ozet_acik   = u.get("ozet_bildirimi",   "false").lower() == "true"
        ozet_sikligi = u.get("ozet_sikligi", "weekly")   # weekly | monthly
        odeme_gunu  = u.get("spotify_odeme_gunu", "").strip()

        # ── Streak uyarısı ─────────────────────────────────────────────────
        if streak_acik:
            _check_streak(
                uid, email, display_name,
                today_str, u, sheets, send_mail, mail_streak_uyari
            )

        # ── Spotify ödeme hatırlatması ──────────────────────────────────────
        if odeme_gunu and streak_acik:
            _check_odeme(
                email, display_name, odeme_gunu, now_utc,
                send_mail, mail_spotify_odeme
            )

        # ── Haftalık / aylık özet ───────────────────────────────────────────
        if ozet_acik:
            _check_ozet(
                uid, email, display_name,
                ozet_sikligi, now_utc, today_str, u,
                sheets, send_mail, mail_haftalik_ozet, mail_aylik_ozet
            )


# ── Yardımcı Fonksiyonlar ─────────────────────────────────────────────────────

def _check_streak(uid, email, display_name, today_str, u, sheets, send_mail, mail_fn):
    """Bugün dinleme yoksa ve daha önce bildirilmediyse streak uyarısı gönder."""
    son_bildirim = u.get("son_streak_bildirimi", "")
    if son_bildirim == today_str:
        return  # Bugün zaten gönderildi

    try:
        headers, rows = sheets.get_user_data(uid)
        if not rows:
            return

        idx_iso = headers.index("_played_at_iso")
        bugun_var = any(
            row[idx_iso][:10] == today_str
            for row in rows
            if len(row) > idx_iso and row[idx_iso]
        )

        if bugun_var:
            return  # Bugün zaten dinlemiş

        # Streak hesapla
        from utils.gamification import calc_streak
        daily_dates = {
            row[idx_iso][:10]
            for row in rows
            if len(row) > idx_iso and row[idx_iso]
        }
        streak_data = calc_streak(daily_dates)
        streak = streak_data.get("current", 0)

        if streak < 2:
            return  # 1 günlük seri için uyarı göndermemeye gerek yok

        subject, html = mail_fn(display_name, streak, "6 saat")
        if send_mail(email, subject, html):
            sheets.set_notification_field(uid, "son_streak_bildirimi", today_str)

    except Exception as e:
        logger.error(f"Streak bildirim hatası ({uid}): {e}")


def _check_odeme(email, display_name, odeme_gunu_str, now_utc, send_mail, mail_fn):
    """Yarın ödeme günüyse hatırlatma gönder."""
    try:
        odeme_gunu = int(odeme_gunu_str)
        yarin      = now_utc + timedelta(days=1)
        if yarin.day == odeme_gunu:
            subject, html = mail_fn(display_name, odeme_gunu)
            send_mail(email, subject, html)
    except (ValueError, Exception) as e:
        logger.error(f"Ödeme bildirim hatası ({email}): {e}")


def _check_ozet(uid, email, display_name, sikligi, now_utc, today_str, u,
                sheets, send_mail, haftalik_fn, aylik_fn):
    """Haftalık (Pazar) veya aylık (ayın 1'i) özet gönder."""
    son_ozet = u.get("son_ozet_bildirimi", "")
    if son_ozet == today_str:
        return

    # Haftalık: Pazar (weekday=6), Aylık: Ayın 1'i
    gonder = False
    if sikligi == "weekly"  and now_utc.weekday() == 6:
        gonder = True
    elif sikligi == "monthly" and now_utc.day == 1:
        gonder = True

    if not gonder:
        return

    try:
        istatistik = _hesapla_ozet(uid, sheets, sikligi, now_utc)
        if sikligi == "weekly":
            subject, html = haftalik_fn(display_name, istatistik)
        else:
            subject, html = aylik_fn(display_name, istatistik)

        if send_mail(email, subject, html):
            sheets.set_notification_field(uid, "son_ozet_bildirimi", today_str)

    except Exception as e:
        logger.error(f"Özet bildirim hatası ({uid}): {e}")


def _hesapla_ozet(uid: str, sheets, sikligi: str, now_utc: datetime) -> dict:
    """Son 7 gün veya son ay istatistiklerini hesaplar."""
    from utils.gamification import calc_streak

    headers, rows = sheets.get_user_data(uid)
    if not rows:
        return {}

    idx_sarki   = headers.index("Şarkı Adı")
    idx_sanatci = headers.index("Sanatçı")
    idx_sure    = headers.index("Süre (ms)")
    idx_iso     = headers.index("_played_at_iso")

    if sikligi == "weekly":
        baslangic = (now_utc - timedelta(days=7)).date()
    else:
        # Önceki ay
        if now_utc.month == 1:
            baslangic = now_utc.replace(year=now_utc.year - 1, month=12, day=1).date()
        else:
            baslangic = now_utc.replace(month=now_utc.month - 1, day=1).date()

    sarki_say   = defaultdict(int)
    sanatci_say = defaultdict(int)
    toplam      = 0
    toplam_ms   = 0
    daily_dates = set()

    for row in rows:
        if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_iso):
            continue
        iso = row[idx_iso]
        if not iso:
            continue
        gun = datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
        if gun < baslangic:
            continue
        toplam += 1
        try:
            toplam_ms += int(row[idx_sure])
        except (ValueError, TypeError):
            pass
        sarki_say[row[idx_sarki]] += 1
        sanatci_say[row[idx_sanatci]] += 1
        daily_dates.add(gun.strftime("%Y-%m-%d"))

    en_cok_sarki   = max(sarki_say,   key=sarki_say.get)   if sarki_say   else "—"
    en_cok_sanatci = max(sanatci_say, key=sanatci_say.get) if sanatci_say else "—"
    streak_data    = calc_streak({
        row[idx_iso][:10] for row in rows
        if len(row) > idx_iso and row[idx_iso]
    })

    return {
        "toplam_dinlenme": toplam,
        "toplam_sure_dk":  round(toplam_ms / 60000),
        "en_cok_sarki":    en_cok_sarki,
        "en_cok_sanatci":  en_cok_sanatci,
        "streak":          streak_data.get("current", 0),
    }

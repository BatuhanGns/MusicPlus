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
        # Ödeme hatırlatması streak bildiriminden bağımsızdır
        if odeme_gunu:
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
    """
    Haftalık veya aylık özet istatistiklerini hesaplar.

    Haftalık : Son tamamlanan Pazartesi–Pazar haftası
               (Pazar günü gönderilirse bu hafta, diğer günlerde önceki hafta)
    Aylık    : İçinde bulunulan ayın 1'inden bugüne (ayın başında gönderilir)
    """
    from utils.gamification import calc_streak
    import calendar

    headers, rows = sheets.get_user_data(uid)
    if not rows:
        return {}

    def _idx(col):
        try:
            return headers.index(col)
        except ValueError:
            return -1

    idx_sarki   = _idx("Şarkı Adı")
    idx_sanatci = _idx("Sanatçı")
    idx_sure    = _idx("Süre (ms)")
    idx_iso     = _idx("_played_at_iso")

    if any(i == -1 for i in [idx_sarki, idx_sanatci, idx_sure, idx_iso]):
        logger.warning("_hesapla_ozet: Zorunlu sütun eksik")
        return {}

    today = now_utc.date()

    if sikligi == "weekly":
        # Pazartesi=0 … Pazar=6
        # Pazar (weekday=6) → bu haftanın Pazartesi'si baslangic
        # Diğer günler → önceki haftanın Pazartesi'si
        days_since_monday = today.weekday()          # 0=Pzt, 6=Paz
        this_monday = today - timedelta(days=days_since_monday)
        if today.weekday() == 6:                     # Pazar → bu hafta
            baslangic = this_monday
            bitis     = today
        else:                                         # Diğer → geçen hafta
            baslangic = this_monday - timedelta(days=7)
            bitis     = this_monday - timedelta(days=1)
        donem_label = f"{baslangic.strftime('%d.%m')} – {bitis.strftime('%d.%m.%Y')}"
    else:
        # Aylık: ayın 1'i → bugün (bu ay özeti)
        baslangic   = today.replace(day=1)
        bitis       = today
        ay_adi      = [
            "", "Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
            "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"
        ][today.month]
        donem_label = f"{ay_adi} {today.year}"

    sarki_say   = defaultdict(int)
    sanatci_say = defaultdict(int)
    sarki_sure  = defaultdict(int)   # şarkı başına toplam süre (ms)
    toplam      = 0
    toplam_ms   = 0
    aktif_gunler = set()

    for row in rows:
        if len(row) <= max(idx_sarki, idx_sanatci, idx_sure, idx_iso):
            continue
        iso = (row[idx_iso] or "").strip()
        if not iso:
            continue
        try:
            gun = datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if not (baslangic <= gun <= bitis):
            continue

        sarki   = (row[idx_sarki]   or "").strip()
        sanatci = (row[idx_sanatci] or "").strip()
        try:
            sure_ms = int(row[idx_sure])
        except (ValueError, TypeError):
            sure_ms = 0

        toplam    += 1
        toplam_ms += sure_ms
        aktif_gunler.add(gun)

        if sarki:
            sarki_say[sarki]  += 1
            sarki_sure[sarki] += sure_ms
        if sanatci:
            for tek in [s.strip() for s in sanatci.split(",") if s.strip()]:
                sanatci_say[tek] += 1

    # Top 5 şarkı — dinlenme sayısına göre
    top5_sarki = sorted(sarki_say.items(), key=lambda x: -x[1])[:5]
    top5_sarki = [
        {"sarki": ad, "count": cnt, "sure_dk": round(sarki_sure.get(ad, 0) / 60000)}
        for ad, cnt in top5_sarki
    ]

    # Top 5 sanatçı — dinlenme sayısına göre
    top5_sanatci = sorted(sanatci_say.items(), key=lambda x: -x[1])[:5]
    top5_sanatci = [{"sanatci": ad, "count": cnt} for ad, cnt in top5_sanatci]

    streak_data = calc_streak({
        row[idx_iso][:10] for row in rows
        if len(row) > idx_iso and (row[idx_iso] or "").strip()
    })

    return {
        "donem_label":    donem_label,
        "toplam_dinlenme": toplam,
        "toplam_sure_dk":  round(toplam_ms / 60000),
        "aktif_gun":       len(aktif_gunler),
        "top5_sarki":      top5_sarki,
        "top5_sanatci":    top5_sanatci,
        # Geriye dönük uyumluluk için tek şarkı/sanatçı da tut
        "en_cok_sarki":    top5_sarki[0]["sarki"]   if top5_sarki   else "—",
        "en_cok_sanatci":  top5_sanatci[0]["sanatci"] if top5_sanatci else "—",
        "streak":          streak_data.get("current", 0),
    }

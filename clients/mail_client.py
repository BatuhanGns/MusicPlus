"""
EmailJS REST API üzerinden mail gönderici.
SMTP portu kullanmaz — Render ücretsiz plan dahil her ortamda çalışır.

Kurulum:
  1. emailjs.com → ücretsiz kayıt
  2. Account → Security → "Allow EmailJS API for non-browser applications" → AÇ
  3. Email Services → Gmail bağla → Service ID kopyala
  4. Email Templates → Yeni template:
       To email   : {{to_email}}
       Subject    : {{subject}}
       Content    : HTML moduna geç → {{{html_content}}}  (3 süslü parantez!)
       Template ID kopyala
  5. Account → API Keys → Public Key + Private Key
  6. Render env:
       EMAILJS_SERVICE_ID, EMAILJS_TEMPLATE_ID, EMAILJS_PUBLIC_KEY, EMAILJS_PRIVATE_KEY
"""

import json
import logging
import urllib.request
import urllib.error

import config

logger = logging.getLogger(__name__)

EMAILJS_API_URL = "https://api.emailjs.com/api/v1.0/email/send"


def send_mail(to: str, subject: str, html: str) -> bool:
    service_id  = getattr(config, "EMAILJS_SERVICE_ID",  "").strip()
    template_id = getattr(config, "EMAILJS_TEMPLATE_ID", "").strip()
    public_key  = getattr(config, "EMAILJS_PUBLIC_KEY",  "").strip()
    private_key = getattr(config, "EMAILJS_PRIVATE_KEY", "").strip()

    if not all([service_id, template_id, public_key]):
        logger.warning("Mail gönderilemedi: EmailJS env değişkenleri eksik.")
        return False

    payload = json.dumps({
        "service_id":  service_id,
        "template_id": template_id,
        "user_id":     public_key,
        "accessToken": private_key,
        "template_params": {
            "to_email":     to,
            "subject":      subject,
            "html_content": html,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        url=EMAILJS_API_URL, data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent":   "MusicPlus/1.0",
            "Origin":       "https://musicplus.app",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info(f"✅ Mail → {to} | {subject}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error(f"❌ EmailJS {e.code} ({to}): {body}")
        if e.code == 403:
            logger.error("Account → Security → 'Allow EmailJS API for non-browser applications' açık mı?")
        return False
    except Exception as e:
        logger.error(f"❌ Mail gönderilemedi ({to}): {e}")
        return False


# ── Ortak Tasarım ─────────────────────────────────────────────────────────────

_GREEN  = "#1db954"
_BG     = "#0a0a0a"
_CARD   = "#111111"
_BORDER = "#1f1f1f"
_TEXT   = "#cccccc"
_MUTED  = "#666666"


def _base(baslik: str, emoji: str, icerik: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:{_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{_TEXT};padding:24px 16px}}
.wrap{{max-width:540px;margin:0 auto}}
.header{{background:{_CARD};border:1px solid {_BORDER};border-bottom:3px solid {_GREEN};padding:20px 28px;display:flex;align-items:center;gap:12px}}
.logo{{font-size:13px;font-weight:900;letter-spacing:4px;color:{_GREEN}}}
.header-emoji{{font-size:22px}}
.card{{background:{_CARD};border:1px solid {_BORDER};padding:28px;margin-top:2px}}
h1{{font-size:20px;font-weight:800;color:#fff;margin-bottom:8px}}
.subtitle{{font-size:13px;color:{_MUTED};margin-bottom:24px}}
.greeting{{font-size:14px;color:{_TEXT};margin-bottom:20px;line-height:1.6}}
.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:2px;margin:20px 0}}
.stat{{background:{_BG};padding:16px 8px;text-align:center}}
.stat-val{{font-size:26px;font-weight:900;color:{_GREEN}}}
.stat-lbl{{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:{_MUTED};margin-top:4px}}
.section-title{{font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:{_MUTED};margin:24px 0 10px}}
.rank-row{{display:flex;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid {_BORDER}}}
.rank-row:last-child{{border-bottom:none}}
.rank-num{{font-size:11px;font-weight:900;color:{_MUTED};min-width:22px;flex-shrink:0}}
.rank-name{{font-size:13px;color:#fff;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}}
.rank-count{{font-size:12px;color:{_MUTED};white-space:nowrap;flex-shrink:0;min-width:32px;text-align:right}}
.rank-bar-wrap{{width:48px;height:3px;background:{_BORDER};border-radius:2px;overflow:hidden;flex-shrink:0}}
.rank-bar{{height:100%;background:{_GREEN};border-radius:2px}}
.footer{{text-align:center;font-size:11px;color:#333;padding:20px 0 4px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <span class="header-emoji">{emoji}</span>
    <span class="logo">MUSIC+</span>
  </div>
  <div class="card">
    <h1>{baslik}</h1>
    {icerik}
  </div>
  <div class="footer">Bu bildirimi Music+ profil ayarlarından kapatabilirsiniz.</div>
</div>
</body>
</html>"""


def _rank_rows(items: list, key: str, count_key: str = "count", max_val: int = 1) -> str:
    """Sıralı liste satırları (şarkı veya sanatçı için)."""
    html = ""
    for i, item in enumerate(items, 1):
        name  = item.get(key, "—")
        count = item.get(count_key, 0)
        pct   = round(count / max_val * 100) if max_val else 0
        html += f"""
        <div class="rank-row">
          <span class="rank-num">#{i}</span>
          <span class="rank-name">{name}</span>
          <div class="rank-bar-wrap"><div class="rank-bar" style="width:{pct}%"></div></div>
          <span class="rank-count">{count}×</span>
        </div>"""
    return html


# ── Mail Şablonları ───────────────────────────────────────────────────────────

def mail_streak_uyari(display_name: str, streak: int, kalan_sure: str) -> tuple[str, str]:
    subject = f"🔥 {streak} günlük seriniz tehlikede!"
    icerik  = f"""
    <p class="greeting">Merhaba <strong style="color:#fff">{display_name}</strong>,</p>
    <p style="font-size:14px;color:{_TEXT};line-height:1.7;margin-bottom:20px">
      Bugün henüz müzik dinlemediniz. <strong>{kalan_sure}</strong> içinde
      en az bir şarkı dinlemezseniz <strong style="color:{_GREEN}">{streak} günlük seri</strong>niz sıfırlanacak.
    </p>
    <div class="stats">
      <div class="stat">
        <div class="stat-val">{streak}</div>
        <div class="stat-lbl">Mevcut Seri</div>
      </div>
    </div>
    <p style="font-size:13px;color:{_MUTED};margin-top:16px">
      Hemen bir şarkı açın ve serinizi koruyun 🎵
    </p>"""
    return subject, _base(f"🔥 {streak} Günlük Seri Tehlikede", "🔥", icerik)


def mail_spotify_odeme(display_name: str, gun: int) -> tuple[str, str]:
    subject = "💳 Yarın Spotify ödemesi"
    icerik  = f"""
    <p class="greeting">Merhaba <strong style="color:#fff">{display_name}</strong>,</p>
    <p style="font-size:14px;color:{_TEXT};line-height:1.7">
      Spotify abonelik ödemeniz yarın <strong style="color:{_GREEN}">{gun}. günde</strong>
      tahsil edilecek. Ödeme yönteminizin güncel olduğundan emin olun.
    </p>
    <p style="font-size:12px;color:{_MUTED};margin-top:20px">
      Bu hatırlatmayı Music+ profil ayarlarından kapatabilirsiniz.
    </p>"""
    return subject, _base("💳 Spotify Ödeme Hatırlatması", "💳", icerik)


def _ozet_html(display_name: str, donem_label: str,
               toplam: int, sure_dk: int, aktif_gun: int, streak: int,
               top5_sarki: list, top5_sanatci: list) -> str:

    max_sarki   = top5_sarki[0]["count"]   if top5_sarki   else 1
    max_sanatci = top5_sanatci[0]["count"] if top5_sanatci else 1

    saat     = sure_dk // 60
    dk       = sure_dk % 60
    sure_str = f"{saat}s {dk}dk" if saat else f"{dk} dk"

    sarki_rows   = _rank_rows(top5_sarki,   key="sarki",   max_val=max_sarki)
    sanatci_rows = _rank_rows(top5_sanatci, key="sanatci", max_val=max_sanatci)

    return f"""
    <p class="subtitle">{donem_label}</p>
    <p class="greeting">Merhaba <strong style="color:#fff">{display_name}</strong> 👋</p>

    <div class="stats">
      <div class="stat">
        <div class="stat-val">{toplam}</div>
        <div class="stat-lbl">Dinlenme</div>
      </div>
      <div class="stat">
        <div class="stat-val">{sure_str}</div>
        <div class="stat-lbl">Süre</div>
      </div>
      <div class="stat">
        <div class="stat-val">{aktif_gun}</div>
        <div class="stat-lbl">Aktif Gün</div>
      </div>
    </div>

    <p class="section-title">🎵 En Çok Dinlenen Şarkılar</p>
    <div>{sarki_rows}</div>

    <p class="section-title">🎤 En Çok Dinlenen Sanatçılar</p>
    <div>{sanatci_rows}</div>"""


def mail_haftalik_ozet(display_name: str, istatistik: dict) -> tuple[str, str]:
    subject = "📊 Haftalık müzik özeti"
    donem   = istatistik.get("donem_label", "Bu Hafta")
    icerik  = _ozet_html(
        display_name      = display_name,
        donem_label       = donem,
        toplam            = istatistik.get("toplam_dinlenme", 0),
        sure_dk           = istatistik.get("toplam_sure_dk",  0),
        aktif_gun         = istatistik.get("aktif_gun",       0),
        streak            = istatistik.get("streak",          0),
        top5_sarki        = istatistik.get("top5_sarki",      []),
        top5_sanatci      = istatistik.get("top5_sanatci",    []),
    )
    return subject, _base("📊 Haftalık Müzik Özeti", "📊", icerik)


def mail_aylik_ozet(display_name: str, istatistik: dict) -> tuple[str, str]:
    subject = "📅 Aylık müzik özeti"
    donem   = istatistik.get("donem_label", "Bu Ay")
    icerik  = _ozet_html(
        display_name      = display_name,
        donem_label       = donem,
        toplam            = istatistik.get("toplam_dinlenme", 0),
        sure_dk           = istatistik.get("toplam_sure_dk",  0),
        aktif_gun         = istatistik.get("aktif_gun",       0),
        streak            = istatistik.get("streak",          0),
        top5_sarki        = istatistik.get("top5_sarki",      []),
        top5_sanatci      = istatistik.get("top5_sanatci",    []),
    )
    return subject, _base("📅 Aylık Müzik Özeti", "📅", icerik)

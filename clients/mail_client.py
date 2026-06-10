"""
Resend API üzerinden mail gönderici.
Env: RESEND_API_KEY, RESEND_FROM_EMAIL (opsiyonel, varsayılan: Music+ <onboarding@resend.dev>)
"""

import logging
import json
import urllib.request
import urllib.error

import config

logger = logging.getLogger(__name__)


def send_mail(to: str, subject: str, html: str) -> bool:
    """
    HTML mail gönderir (Resend API).
    Başarılıysa True, hata varsa False döner.
    """
    api_key    = getattr(config, "RESEND_API_KEY", "")
    from_email = getattr(config, "RESEND_FROM_EMAIL", "") or "Music+ <onboarding@resend.dev>"

    if not api_key:
        logger.warning("Mail gönderilemedi: RESEND_API_KEY eksik.")
        return False

    payload = json.dumps({
        "from":    from_email,
        "to":      [to],
        "subject": subject,
        "html":    html,
    }).encode("utf-8")

    req = urllib.request.Request(
        url     = "https://api.resend.com/emails",
        data    = payload,
        method  = "POST",
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            logger.info(f"✅ Mail gönderildi → {to} | {subject} | id={data.get('id','?')}")
            return True

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error(f"❌ Resend HTTP {e.code} ({to}): {body}")
        return False

    except Exception as e:
        logger.error(f"❌ Mail gönderilemedi ({to}): {e}")
        return False


# ── Mail Şablonları ───────────────────────────────────────────────────────────

def _base_template(baslik: str, icerik: str) -> str:
    return f"""
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{{margin:0;padding:0;background:#0a0a0a;font-family:'Helvetica Neue',Arial,sans-serif;color:#e5e5e5;}}
  .wrap{{max-width:520px;margin:40px auto;background:#111;border:1px solid #1f1f1f;}}
  .header{{background:#0a0a0a;border-bottom:2px solid #1db954;padding:24px 32px;}}
  .header h1{{margin:0;font-size:18px;font-weight:800;letter-spacing:3px;color:#1db954;}}
  .body{{padding:32px;}}
  .body h2{{margin:0 0 8px;font-size:22px;font-weight:800;color:#fff;}}
  .body p{{margin:0 0 16px;font-size:14px;line-height:1.7;color:#aaa;}}
  .stat-row{{display:flex;gap:1px;background:#1f1f1f;margin:24px 0;}}
  .stat{{flex:1;background:#111;padding:16px;text-align:center;}}
  .stat .val{{font-size:28px;font-weight:800;color:#1db954;}}
  .stat .lbl{{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:#555;margin-top:4px;}}
  .btn{{display:inline-block;margin-top:8px;background:#1db954;color:#000;padding:12px 28px;font-weight:800;font-size:13px;letter-spacing:1px;text-decoration:none;}}
  .footer{{border-top:1px solid #1f1f1f;padding:20px 32px;font-size:11px;color:#333;text-align:center;}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header"><h1>MUSIC+</h1></div>
  <div class="body">
    <h2>{baslik}</h2>
    {icerik}
  </div>
  <div class="footer">Bu bildirimi Music+ uygulamanızdan kapayabilirsiniz.</div>
</div>
</body>
</html>
"""


def mail_streak_uyari(display_name: str, streak: int, kalan_sure: str) -> tuple[str, str]:
    """Streak bozulma uyarısı maili. (subject, html) döner."""
    subject = f"⚠️ {streak} günlük seriniz tehlikede!"
    icerik  = f"""
    <p>Merhaba <strong>{display_name}</strong>,</p>
    <p>Bugün henüz müzik dinlemediniz. Eğer <strong>{kalan_sure}</strong> içinde en az bir şarkı dinlemezseniz <strong>{streak} günlük seri</strong>niz sıfırlanacak.</p>
    <div class="stat-row">
      <div class="stat">
        <div class="val">{streak}</div>
        <div class="lbl">Mevcut Seri</div>
      </div>
    </div>
    <p>Hemen bir şarkı açın ve serinizi koruyun! 🎵</p>
    """
    return subject, _base_template(f"🔥 {streak} Günlük Seriniz Tehlikede", icerik)


def mail_spotify_odeme(display_name: str, gun: int) -> tuple[str, str]:
    """Spotify ödeme hatırlatma maili. (subject, html) döner."""
    subject = "💳 Yarın Spotify ödemesi"
    icerik  = f"""
    <p>Merhaba <strong>{display_name}</strong>,</p>
    <p>Spotify abonelik ödemeniz yarın (<strong>{gun}. gün</strong>) tahsil edilecek. Ödeme yönteminizin güncel olduğundan emin olun.</p>
    <p style="color:#555;font-size:12px;">Bu hatırlatmayı Music+ profilinizden kapatabilirsiniz.</p>
    """
    return subject, _base_template("💳 Spotify Ödeme Hatırlatması", icerik)


def mail_haftalik_ozet(display_name: str, istatistik: dict) -> tuple[str, str]:
    """Haftalık dinleme özeti maili. (subject, html) döner."""
    subject = "📊 Haftalık müzik özeti"
    en_cok  = istatistik.get("en_cok_sarki", "—")
    sanatci = istatistik.get("en_cok_sanatci", "—")
    toplam  = istatistik.get("toplam_dinlenme", 0)
    sure_dk = istatistik.get("toplam_sure_dk", 0)
    streak  = istatistik.get("streak", 0)

    icerik = f"""
    <p>Merhaba <strong>{display_name}</strong>, bu haftaki müzik özeti hazır!</p>
    <div class="stat-row">
      <div class="stat">
        <div class="val">{toplam}</div>
        <div class="lbl">Dinlenme</div>
      </div>
      <div class="stat">
        <div class="val">{sure_dk}</div>
        <div class="lbl">Dakika</div>
      </div>
      <div class="stat">
        <div class="val">{streak}</div>
        <div class="lbl">Seri</div>
      </div>
    </div>
    <p>🎵 <strong>En çok dinlenen şarkı:</strong> {en_cok}</p>
    <p>🎤 <strong>En çok dinlenen sanatçı:</strong> {sanatci}</p>
    """
    return subject, _base_template("📊 Haftalık Müzik Özetin", icerik)


def mail_aylik_ozet(display_name: str, istatistik: dict) -> tuple[str, str]:
    """Aylık dinleme özeti maili. (subject, html) döner."""
    subject = "📅 Aylık müzik özeti"
    en_cok  = istatistik.get("en_cok_sarki", "—")
    sanatci = istatistik.get("en_cok_sanatci", "—")
    toplam  = istatistik.get("toplam_dinlenme", 0)
    sure_dk = istatistik.get("toplam_sure_dk", 0)
    streak  = istatistik.get("streak", 0)

    icerik = f"""
    <p>Merhaba <strong>{display_name}</strong>, bu ayki müzik özeti hazır!</p>
    <div class="stat-row">
      <div class="stat">
        <div class="val">{toplam}</div>
        <div class="lbl">Dinlenme</div>
      </div>
      <div class="stat">
        <div class="val">{sure_dk}</div>
        <div class="lbl">Dakika</div>
      </div>
      <div class="stat">
        <div class="val">{streak}</div>
        <div class="lbl">En İyi Seri</div>
      </div>
    </div>
    <p>🎵 <strong>En çok dinlenen şarkı:</strong> {en_cok}</p>
    <p>🎤 <strong>En çok dinlenen sanatçı:</strong> {sanatci}</p>
    """
    return subject, _base_template("📅 Aylık Müzik Özetin", icerik)

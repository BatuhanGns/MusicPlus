"""
Music+ uygulaması merkezi konfigürasyon dosyası.
Tüm ortam değişkenleri, sabitler ve global cache'ler burada tanımlanır.
"""

import os
import time
from datetime import timedelta

# ── Flask ────────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY ortam değişkeni ayarlanmamış! "
        "Güvenli rastgele bir değer oluşturup env'e ekleyin: "
        "python -c \"import secrets; print(secrets.token_hex(32))\""
    )
PERMANENT_SESSION_LIFETIME = timedelta(days=30)

# ── Spotify ──────────────────────────────────────────────────────────────────
SPOTIFY_CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REFRESH_TOKEN = os.environ.get("SPOTIFY_REFRESH_TOKEN", "")

# ── Google Sheets ────────────────────────────────────────────────────────────
GOOGLE_SHEETS_ID         = os.environ.get("GOOGLE_SHEETS_ID", "")
GOOGLE_CREDENTIALS_JSON  = os.environ.get("GOOGLE_CREDENTIALS_JSON", "{}")

# ── Gemini AI ────────────────────────────────────────────────────────────────
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
AI_MAX_REQUESTS = 3000

# ── Mail (EmailJS REST API) ───────────────────────────────────────────────────
# Kurulum: emailjs.com → ücretsiz kayıt → Gmail bağla → template oluştur
# Account → Security → Allow EmailJS API for non-browser applications → AÇ
EMAILJS_SERVICE_ID  = os.environ.get("EMAILJS_SERVICE_ID",  "")
EMAILJS_TEMPLATE_ID = os.environ.get("EMAILJS_TEMPLATE_ID", "")
EMAILJS_PUBLIC_KEY  = os.environ.get("EMAILJS_PUBLIC_KEY",  "")
EMAILJS_PRIVATE_KEY = os.environ.get("EMAILJS_PRIVATE_KEY", "")

# ── Uygulama Sabitleri ───────────────────────────────────────────────────────
TR_GUNLER = {
    0: "Pazartesi", 1: "Salı", 2: "Çarşamba", 3: "Perşembe",
    4: "Cuma", 5: "Cumartesi", 6: "Pazar",
}
TR_AYLAR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
}
VAKIT = {
    range(6, 12): "Sabah (06-12)",
    range(12, 18): "Öğleden Sonra (12-18)",
    range(18, 24): "Akşam (18-24)",
    range(0, 6): "Gece (00-06)",
}

# Sheets sütun düzeni — tek kaynak olarak burada tanımlanır.
# sheets_client.py ve helpers.py bu listeye göre çalışır.
# Kullanıcı istedi: "Tür" sütunu YOK — 8 sütun.
HAM_HEADERS = [
    "Dinlenme Tarihi",  # 0
    "Şarkı ID",         # 1
    "Şarkı Adı",        # 2
    "Sanatçı",          # 3
    "Sanatçı ID",       # 4
    "Albüm",            # 5
    "Süre (ms)",        # 6
    "_played_at_iso",   # 7
]

# ── Global State (uygulama ömrü boyunca bellekte) ────────────────────────────
SERVER_START_TIME = time.time()
ai_requests_used  = 0
_ai_total_cache   = {"value": 0, "ts": 0}
_user_cache       = {}
_gorsel_cache     = {}  # Albüm/sanatçı görsel URL cache'i
_ai_history       = {}
AI_MAX_HISTORY    = 20
_cached_rows      = []
_cached_headers   = []
_last_sync        = "Henüz sync yapılmadı"

# ── Refresh Token Belleği ─────────────────────────────────────────────────────
_refresh_tokens: dict = {}  # { user_id: refresh_token }

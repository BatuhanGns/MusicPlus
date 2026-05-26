"""
Music+ uygulaması merkezi konfigürasyon dosyası.
Tüm ortam değişkenleri, sabitler ve global cache'ler burada tanımlanır.
"""

import os
import time
from datetime import timedelta

# ── Flask ────────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "spotify-stats-2026-pkce-persistent-secret-key")
PERMANENT_SESSION_LIFETIME = timedelta(days=30)

# ── Spotify ──────────────────────────────────────────────────────────────────
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REFRESH_TOKEN = os.environ.get("SPOTIFY_REFRESH_TOKEN", "")

# ── Google Sheets ────────────────────────────────────────────────────────────
GOOGLE_SHEETS_ID = os.environ.get("GOOGLE_SHEETS_ID", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "{}")

# ── Gemini AI ────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
AI_MAX_REQUESTS = 3000

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
HAM_HEADERS = [
    "Dinlenme Tarihi", "Şarkı ID", "Şarkı Adı", "Sanatçı",
    "Albüm", "Süre (ms)", "Süre (sn)", "_played_at_iso",
]

# ── Global State (uygulama ömrü boyunca bellekte) ────────────────────────────
SERVER_START_TIME = time.time()
ai_requests_used = 0
_ai_total_cache = {"value": 0, "ts": 0}
_user_cache = {}
_ai_history = {}
AI_MAX_HISTORY = 20
_cached_rows = []
_cached_headers = []
_last_sync = "Henüz sync yapılmadı"
_gorsel_cache = {}

# Music+

Spotify dinleme geçmişinizi takip eden, gamification sistemiyle eğlenceli hale getiren açık kaynaklı bir web uygulaması.

## Özellikler

- Spotify dinleme istatistikleri (şarkı, sanatçı, albüm, saat dağılımı)
- XP ve coin bazlı gamification sistemi
- Pet sistemi (yumurta aç, fusion yap, aktif pet çarpanları)
- Otomatik playlist oluşturma
- Gemini AI ile müzik sohbeti
- Google Sheets veri depolama

## Gereksinimler

- Python 3.10+
- Spotify Developer uygulaması (Client ID + Secret)
- Google Cloud Service Account (Sheets API için)
- Gemini API anahtarı (opsiyonel, AI özelliği için)

## Kurulum

### 1. Repoyu klonla

```bash
git clone https://github.com/kullanici/musicplus.git
cd musicplus
```

### 2. Bağımlılıkları yükle

```bash
pip install -r requirements.txt
```

### 3. Ortam değişkenlerini ayarla

`.env` dosyası oluştur veya ortam değişkenlerini export et:

```bash
SECRET_KEY=gizli-anahtar-buraya
SPOTIFY_CLIENT_ID=spotify_client_id
SPOTIFY_CLIENT_SECRET=spotify_client_secret
SPOTIFY_REFRESH_TOKEN=spotify_refresh_token
GOOGLE_SHEETS_ID=sheets_id
GOOGLE_CREDENTIALS_JSON={"type":"service_account",...}
GEMINI_API_KEY=gemini_api_key          # opsiyonel
UPTIMEROBOT_API_KEY=ur_api_key         # opsiyonel
```

### 4. Çalıştır

**Self-hosted (geliştirme):**
```bash
python app.py
```

**Self-hosted (production):**
```bash
gunicorn app:app --bind 0.0.0.0:8000 --workers 1 --threads 2 --timeout 120
```

**Docker:**
```bash
docker build -t musicplus .
docker run -p 8000:8000 --env-file .env musicplus
```

## Platform Desteği

| Platform | Destek | Notlar |
|---|---|---|
| Self-hosted (Linux/macOS/Windows) | ✅ | `python app.py` |
| Render | ✅ | `render.yaml` hazır |
| Railway | ✅ | `Procfile` hazır |
| Fly.io | ✅ | Dockerfile ile |
| Heroku | ✅ | `Procfile` hazır |
| Docker | ✅ | Dockerfile oluştur |
| VPS (nginx + gunicorn) | ✅ | Yaygın deployment |

Detaylar için `docs/` klasörüne bakın.

## Proje Yapısı

```
musicplus/
├── app.py              # Flask uygulama başlangıcı + scheduler
├── config.py           # Ortam değişkenleri + sabitler
├── extensions.py       # Sync job, cache, yardımcı fonksiyonlar
├── clients/            # Dış servis istemcileri
├── routes/             # API endpoint'leri
├── utils/              # Hesaplama ve yardımcı modüller
├── templates/          # HTML şablonları
├── static/             # Statik dosyalar (pet görselleri vb.)
├── requirements.txt    # Python bağımlılıkları
├── render.yaml         # Render deployment config
└── Procfile            # Heroku/Railway config
```

## Lisans

MIT

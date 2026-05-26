<div align="center">
<img width="1189" height="468" alt="IMG_20260526_114416_215" src="https://github.com/user-attachments/assets/bc18bd8c-18f5-4162-807e-52fe9e5795f1" />


**Spotify dinleme geçmişini takip eden, gamification ile eğlenceli hale getiren açık kaynak web uygulaması.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat-square&logo=flask)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Deploy on Render](https://img.shields.io/badge/Deploy-Render-46E3B7?style=flat-square&logo=render)](docs/DEPLOYMENT.md)

</div>

---

## 🎵 Ne Yapar?

Music+, Spotify hesabına bağlanarak dinleme geçmişini **Google Sheets**'e kaydeder ve sana özelleştirilmiş bir dashboard sunar. Sıradan bir stats uygulamasının ötesinde; XP, seviye, streak, pet sistemi ve Gemini AI sohbetiyle dinleme deneyimini oyunlaştırır.

---

## ✨ Özellikler

| Kategori | Özellik |
|---|---|
| 📊 İstatistikler | Şarkı, sanatçı, albüm — günlük / aylık / tüm zamanlar |
| ⏰ Zaman analizi | Dinleme saati dağılımı (sabah, öğle, akşam, gece) |
| 🎮 Gamification | XP & coin sistemi, seviye atlama, günlük streak |
| 🐾 Pet sistemi | Yumurta aç, fusion yap, aktif pet çarpanları kazan |
| 🤖 AI sohbet | Gemini ile şarkı önerisi, playlist oluşturma, analiz |
| 🎧 Playlist yönetimi | Otomatik & manuel playlist oluşturma / düzenleme |
| 🔄 Otomatik sync | Her 30 dakikada bir Spotify'dan yeni dinlemeleri çeker |

---

## 🚀 Hızlı Başlangıç

### Gereksinimler

- Python 3.10+
- Spotify Developer uygulaması (ücretsiz)
- Google Cloud Service Account (Sheets API için, ücretsiz)
- Gemini API anahtarı _(opsiyonel — AI sohbet için)_

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

```bash
cp .env.example .env
# .env dosyasını düzenle — her değişken için docs/ENVIRONMENT.md'ye bak
```

### 4. Çalıştır

```bash
# Geliştirme
python app.py

# Production
gunicorn app:app --bind 0.0.0.0:8000 --workers 1 --threads 2 --timeout 120
```

Tarayıcıda `http://localhost:5000` adresini aç, Spotify ile giriş yap.

> **Her ortam değişkenini nasıl alacağını merak ediyorsan → [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md)**

---

## 🏗️ Proje Yapısı

```
musicplus/
├── app.py              # Flask uygulaması + APScheduler başlangıcı
├── config.py           # Tüm env değişkenleri ve sabitler
├── extensions.py       # Spotify/Sheets client örnekleri, sync job
│
├── clients/            # Dış servis istemcileri
│   ├── spotify_client.py   # Spotify Web API (auth, playback, playlists)
│   ├── sheets_client.py    # Google Sheets (veri depolama katmanı)
│   └── gemini_client.py    # Gemini AI (opsiyonel, sohbet)
│
├── routes/             # Flask Blueprint'leri (API endpoint'leri)
│   ├── auth.py             # OAuth2 PKCE akışı (/login, /callback)
│   ├── stats.py            # İstatistik API'leri
│   ├── songs.py            # Şarkı/sanatçı/albüm detayları
│   ├── pets.py             # Pet sistemi
│   ├── playlists.py        # Playlist yönetimi
│   ├── ai.py               # Gemini AI sohbet endpoint'i
│   ├── system.py           # Sağlık kontrolü, manuel sync
│   ├── dashboard.py        # Ana sayfa render
│   └── topluluk.py         # Topluluk istatistikleri
│
├── utils/              # Saf Python hesaplama modülleri
│   ├── gamification.py     # XP, seviye, streak hesaplama
│   ├── pets.py             # Pet açma, fusion, bonus hesaplama
│   └── helpers.py          # Genel istatistik hesaplama
│
├── templates/
│   └── dashboard.html      # Tek sayfa uygulama (tüm UI)
│
├── static/pets/        # Pet görselleri (normal/golden/diamond × rarity)
│
├── docs/               # Detaylı dokümantasyon
│   ├── ENVIRONMENT.md      # Her env değişkenini nasıl alırsın
│   └── DEPLOYMENT.md       # Platform bazlı deployment rehberi
│
├── .env.example        # Örnek ortam değişkenleri
├── requirements.txt    # Python bağımlılıkları
├── Dockerfile          # Docker imajı
├── Procfile            # Heroku / Railway
└── render.yaml         # Render.com config
```

---

## ⚙️ Ortam Değişkenleri (Özet)

| Değişken | Zorunlu | Açıklama |
|---|---|---|
| `SECRET_KEY` | ✅ | Flask session şifreleme anahtarı |
| `SPOTIFY_CLIENT_ID` | ✅ | Spotify Developer uygulaması |
| `SPOTIFY_CLIENT_SECRET` | ✅ | Spotify Developer uygulaması |
| `SPOTIFY_REFRESH_TOKEN` | ✅ | Sunucu tarafı token (arka plan sync için) |
| `GOOGLE_SHEETS_ID` | ✅ | Veri depolama için Sheets ID |
| `GOOGLE_CREDENTIALS_JSON` | ✅ | Service Account JSON (string olarak) |
| `GEMINI_API_KEY` | ⬜ | AI sohbet özelliği için (opsiyonel) |

> Her değişkeni adım adım nasıl alacağını öğrenmek için → **[docs/ENVIRONMENT.md](docs/ENVIRONMENT.md)**

---

## 🌍 Deployment Seçenekleri

| Platform | Durum | Notlar |
|---|---|---|
| Self-hosted (Linux/macOS) | ✅ | `python app.py` veya gunicorn |
| Docker | ✅ | `Dockerfile` hazır |
| Render | ✅ | `render.yaml` hazır, tek tıkla deploy |
| Railway | ✅ | `Procfile` hazır |
| Heroku | ✅ | `Procfile` hazır |
| Fly.io | ✅ | Dockerfile ile çalışır |
| VPS (nginx + gunicorn) | ✅ | Yaygın production kurulumu |

Detaylı adımlar için → **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**

---

## 🔐 Gizlilik

Music+ tamamen kendi altyapında çalışır. Dinleme verilerini kendi Google Sheets'ine kaydeder; hiçbir üçüncü taraf sunucuya veri gönderilmez.

---

## 📄 Lisans

[MIT](LICENSE)

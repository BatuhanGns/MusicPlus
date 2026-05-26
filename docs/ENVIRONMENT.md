# Ortam Değişkenleri Rehberi

Bu belge, Music+'ın ihtiyaç duyduğu her ortam değişkenini **nereden ve nasıl alacağını** adım adım açıklar.

`.env.example` dosyasını kopyalayıp düzenleyerek başla:

```bash
cp .env.example .env
```

---

## İçindekiler

1. [SECRET\_KEY](#1-secret_key)
2. [Spotify Değişkenleri](#2-spotify-değişkenleri)
   - SPOTIFY\_CLIENT\_ID
   - SPOTIFY\_CLIENT\_SECRET
   - SPOTIFY\_REFRESH\_TOKEN
3. [Google Sheets Değişkenleri](#3-google-sheets-değişkenleri)
   - GOOGLE\_SHEETS\_ID
   - GOOGLE\_CREDENTIALS\_JSON
4. [GEMINI\_API\_KEY (Opsiyonel)](#4-gemini_api_key-opsiyonel)
5. [Tamamlanmış .env Örneği](#5-tamamlanmış-env-örneği)

---

## 1. `SECRET_KEY`

Flask oturumlarını (session) şifrelemek için kullanılır. **Güvenli ve rastgele** bir değer olmalıdır.

**Nasıl üretilir:**

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Çıktıyı doğrudan kopyala:

```
SECRET_KEY=buraya-uretilen-rastgele-deger-yapistir
```

> ⚠️ Bu değeri asla başkasıyla paylaşma ve repoya commit etme.

---

## 2. Spotify Değişkenleri

Music+ Spotify Web API'yi kullanır. Bunun için ücretsiz bir **Spotify Developer** uygulaması gerekir.

### Adım 1 — Developer hesabı ve uygulama oluştur

1. [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)'a giriş yap (normal Spotify hesabınla giriş yapabilirsin).
2. **"Create App"** butonuna tıkla.
3. Forma şunları gir:
   - **App name:** `MusicPlus` (dilediğin bir isim)
   - **App description:** kısa bir açıklama
   - **Redirect URI:** `http://localhost:5000/callback` *(geliştirme için)*
   - **APIs used:** `Web API` seç
4. Oluştur ve uygulama sayfasına geç.

### Adım 2 — `SPOTIFY_CLIENT_ID` ve `SPOTIFY_CLIENT_SECRET` al

Uygulama sayfasında **Settings** → **Basic Information** altında:

```
SPOTIFY_CLIENT_ID=buraya-client-id-yapistir
SPOTIFY_CLIENT_SECRET=buraya-client-secret-yapistir
```

> 🔒 Client Secret'ı **"View client secret"** butonuna tıklayarak görüntüleyebilirsin.

### Adım 3 — `SPOTIFY_REFRESH_TOKEN` al

Refresh token, Music+'ın arka planda (tarayıcı olmadan) Spotify'a erişmesini sağlar. Tek seferlik bir işlemdir.

**a) Geçici bir Python scripti çalıştır:**

```python
# get_token.py
import urllib.parse, webbrowser

CLIENT_ID     = "BURAYA_CLIENT_ID_YAZ"
REDIRECT_URI  = "http://localhost:5000/callback"
SCOPES = " ".join([
    "user-read-recently-played",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-modify-public",
    "playlist-modify-private",
    "user-library-read",
    "user-library-modify",
])

url = (
    "https://accounts.spotify.com/authorize"
    f"?client_id={CLIENT_ID}"
    f"&response_type=code"
    f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
    f"&scope={urllib.parse.quote(SCOPES)}"
)
print("Tarayıcı açılıyor...")
webbrowser.open(url)
print("Giriş sonrası URL'deki 'code' parametresini kopyala.")
```

```bash
python get_token.py
```

**b) Spotify onay sayfasında izin ver.** Yönlendirilen URL şöyle görünür:

```
http://localhost:5000/callback?code=AQBxxx...
```

URL'deki `code=` kısmından sonraki değeri kopyala.

**c) Bu kodu refresh token'a çevir:**

```python
# exchange_code.py
import requests, base64

CLIENT_ID     = "BURAYA_CLIENT_ID_YAZ"
CLIENT_SECRET = "BURAYA_CLIENT_SECRET_YAZ"
CODE          = "BURAYA_CODE_YAZ"
REDIRECT_URI  = "http://localhost:5000/callback"

credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

resp = requests.post(
    "https://accounts.spotify.com/api/token",
    headers={"Authorization": f"Basic {credentials}"},
    data={
        "grant_type":   "authorization_code",
        "code":         CODE,
        "redirect_uri": REDIRECT_URI,
    },
)
data = resp.json()
print("REFRESH TOKEN:", data.get("refresh_token"))
```

```bash
python exchange_code.py
```

Çıktıdaki değeri `.env`'e ekle:

```
SPOTIFY_REFRESH_TOKEN=buraya-refresh-token-yapistir
```

### Production için Redirect URI Güncelleme

Uygulamayı bir sunucuya deploy ettiğinde, Spotify Developer Dashboard'da Redirect URI'ye production URL'ini de ekle:

```
https://alan-adin.com/callback
```

---

## 3. Google Sheets Değişkenleri

Music+ dinleme verilerini Google Sheets'e kaydeder. Bunun için bir **Service Account** gerekir.

### Adım 1 — Google Cloud projesi oluştur

1. [Google Cloud Console](https://console.cloud.google.com/)'a giriş yap.
2. Üst menüden **"Yeni Proje"** oluştur (örn. `musicplus`).

### Adım 2 — Google Sheets API'yi etkinleştir

1. Sol menüden **"API'ler ve Hizmetler"** → **"Kitaplık"** seç.
2. Arama kutusuna `Google Sheets API` yaz ve etkinleştir.
3. Aynı şekilde `Google Drive API`'yi de etkinleştir.

### Adım 3 — Service Account oluştur

1. **"API'ler ve Hizmetler"** → **"Kimlik Bilgileri"** → **"Kimlik Bilgisi Oluştur"** → **"Hizmet Hesabı"**.
2. Bir ad gir (örn. `musicplus-bot`), **"Oluştur ve Devam Et"** tıkla.
3. Rol adımını geçebilirsin (rol seçme zorunlu değil).
4. **"Bitti"** tıkla.

### Adım 4 — JSON anahtarı indir

1. Oluşturulan service account'a tıkla.
2. **"Anahtarlar"** sekmesi → **"Anahtar Ekle"** → **"Yeni Anahtar Oluştur"** → **JSON** seç.
3. JSON dosyası otomatik indirilir. **Bu dosyayı güvenli sakla.**

### Adım 5 — Google Sheet oluştur ve `GOOGLE_SHEETS_ID` al

1. [Google Sheets](https://sheets.google.com)'e git ve boş bir elektronik tablo oluştur.
2. URL'den ID'yi kopyala:

```
https://docs.google.com/spreadsheets/d/  BURASI_SHEETS_ID  /edit
```

```
GOOGLE_SHEETS_ID=buraya-sheets-id-yapistir
```

### Adım 6 — Service Account'u Sheet'e ekle

1. Google Sheet'i aç → sağ üstten **"Paylaş"** butonuna tıkla.
2. Service account e-posta adresini (örn. `musicplus-bot@musicplus.iam.gserviceaccount.com`) ekle ve **Düzenleyici** yetkisi ver.

### Adım 7 — `GOOGLE_CREDENTIALS_JSON` hazırla

İndirilen JSON dosyasının içeriğini **tek satır string** olarak `.env`'e ekle:

```bash
# JSON dosyasını tek satıra çevir
python -c "import json; f=open('service-account.json'); print(json.dumps(json.load(f)))"
```

Çıktıyı kopyala:

```
GOOGLE_CREDENTIALS_JSON={"type":"service_account","project_id":"musicplus",...}
```

> ⚠️ Bu JSON içinde özel anahtar (`private_key`) bulunur. Asla repoya commit etme.

---

## 4. `GEMINI_API_KEY` (Opsiyonel)

AI müzik asistanı özelliği için gereklidir. Bu değişken olmadan uygulama çalışır, yalnızca AI sohbet devre dışı kalır.

### Nasıl alınır:

1. [Google AI Studio](https://aistudio.google.com/)'ya giriş yap.
2. Sol menüden **"Get API key"** → **"Create API key"** tıkla.
3. Projeyi seç (yukarıda oluşturduğun Google Cloud projesi) veya yeni proje oluştur.
4. Oluşturulan anahtarı kopyala:

```
GEMINI_API_KEY=buraya-gemini-api-key-yapistir
```

> Gemini API, belirli bir kullanım kotasına kadar ücretsizdir.

---


## 5. Tamamlanmış `.env` Örneği

```env
# Flask
SECRET_KEY=b3f2a1c8d9e4f7a6b2c5d8e1f4a7b0c3d6e9f2a5b8c1d4e7f0a3b6c9d2e5f8a1

# Spotify
SPOTIFY_CLIENT_ID=abc123def456ghi789
SPOTIFY_CLIENT_SECRET=xyz987wvu654tsr321
SPOTIFY_REFRESH_TOKEN=AQBklmn...uzun-token-buraya

# Google Sheets
GOOGLE_SHEETS_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
GOOGLE_CREDENTIALS_JSON={"type":"service_account","project_id":"musicplus","private_key_id":"abc...","private_key":"-----BEGIN PRIVATE KEY-----\n...","client_email":"musicplus-bot@musicplus.iam.gserviceaccount.com","client_id":"...","auth_uri":"...","token_uri":"..."}

# Opsiyonel
GEMINI_API_KEY=AIzaSy...
```

---

## Sık Karşılaşılan Sorunlar

| Sorun | Çözüm |
|---|---|
| `INVALID_CLIENT: Invalid redirect URI` | Spotify Dashboard'da Redirect URI'nin tam olarak eşleştiğini kontrol et |
| `SpreadsheetNotFound` | Service account e-postasının Sheet'e "Düzenleyici" olarak eklendiğini doğrula |
| `GOOGLE_CREDENTIALS_JSON` parse hatası | JSON'un tek satır ve tırnak işaretlerinin doğru olduğunu kontrol et |
| Refresh token çalışmıyor | Token kodunu birden fazla kez kullanmış olabilirsin — yeniden `get_token.py` çalıştır |
| AI özellikleri görünmüyor | `GEMINI_API_KEY` ayarlanmamış — opsiyonel, sorun değil |

# routes/

Flask Blueprint'leri. Her dosya belirli bir endpoint grubunu yönetir.

## auth.py
Spotify OAuth2 PKCE akışı.
- `GET /login` — Spotify login yönlendirmesi
- `GET /callback` — OAuth callback, token alma
- `GET /logout` — oturum sonlandırma

## stats.py
Kullanıcı istatistik API'leri.
- `GET /api/dashboard?aralik=` — dashboard verisi
- `GET /api/now-playing` — şu an çalan
- `GET /api/gamification` — XP/seviye/streak

## songs.py
Şarkı/sanatçı/albüm detay API'leri.
- `GET /api/sarki/<ad>` — şarkı detayı + dinleme saatleri
- `GET /api/sanatci/<ad>` — sanatçı detayı + farklı şarkı/albüm sayısı
- `GET /api/album/<ad>` — albüm detayı + farklı şarkı sayısı

## pets.py
Pet sistemi API'leri.
- `GET /api/pets/state` — envanter, coin, aktif bonuslar
- `POST /api/pets/open` — yumurta aç
- `POST /api/pets/fusion` — manuel fusion
- `POST /api/pets/auto-fusion` — otomatik fusion
- `POST /api/pets/equip` / `unequip` — pet tak/çıkar

## playlists.py
Playlist yönetimi.
- `POST /api/playlist/create` — gelişmiş playlist oluşturma
- `POST /api/player/play|pause|next|previous` — oynatma kontrolü

## system.py
- `GET /api/health` — uygulama durumu
- `GET /api/sync` — manuel sync tetikle

## ai.py
- `POST /api/ai` — Gemini AI sohbet

## dashboard.py
- `GET /` — ana sayfa (dashboard.html)

## topluluk.py
Topluluk istatistikleri.

# clients/

Dış servis istemcileri. Her istemci kendi API'siyle iletişimi yönetir.

## spotify_client.py
Spotify Web API istemcisi.
- `get_now_playing()` — şu an çalan şarkı
- `get_recently_played()` — son dinlemeler (max 50)
- `get_playlists()` — kullanıcı playlistleri
- `get_liked_songs()` — beğenilen şarkılar
- `get_all_user_playlists()` — tüm playlistler
- `play() / pause() / next_track() / previous_track()` — oynatma kontrolü
- `_req()` — token yenileme dahil tüm API istekleri

**Gerekli env:** `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN`

## sheets_client.py
Google Sheets istemcisi. Veri depolama katmanı olarak kullanılır.
- `get_user_data()` — kullanıcı dinleme verilerini çeker
- `append_tracks()` — yeni şarkıları ekler, new_tracks listesi döner
- `save_gamification_cache()` / `get_gamification_cache()` — XP/coin cache
- `get_all_users_with_tokens()` — scheduled sync için

**Gerekli env:** `GOOGLE_SHEETS_ID`, `GOOGLE_CREDENTIALS_JSON`

## gemini_client.py
Google Gemini AI istemcisi. AI sohbet özelliği için kullanılır.
- Opsiyoneldir; `GEMINI_API_KEY` yoksa AI özellikleri devre dışı kalır.

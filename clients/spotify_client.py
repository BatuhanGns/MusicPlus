import os
import time
import logging
import requests
import base64
import urllib.parse
import secrets
import hashlib
from flask import session, has_request_context

logger = logging.getLogger(__name__)

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE  = "https://api.spotify.com/v1"

# Bellek cache: { user_id: {"access_token": str, "expires_at": float} }
# Sheets ile senkronize tutulur; uygulama başında Sheets'ten yüklenir.
_access_token_cache: dict = {}


class SpotifyClient:
    def __init__(self, refresh_token: str = None, token_refresh_callback=None):
        self.client_id        = os.environ.get("SPOTIFY_CLIENT_ID", "")
        self.client_secret    = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        self.refresh_token    = refresh_token or os.environ.get("SPOTIFY_REFRESH_TOKEN", "")
        self._access_token    = None
        self._token_expires_at = 0
        self._user_id         = None
        self._token_refresh_callback = token_refresh_callback

    # ------------------------------------------------------------------ #
    #  AUTH                                                                #
    # ------------------------------------------------------------------ #

    def get_auth_url(self, redirect_uri):
        scopes = " ".join([
            "user-read-recently-played",
            "user-read-currently-playing",
            "playlist-read-private",
            "playlist-modify-public",
            "playlist-modify-private",
            "user-follow-modify",
            "user-library-modify",
            "user-library-read",
        ])
        encoded_scopes   = urllib.parse.quote(scopes)
        encoded_redirect = urllib.parse.quote(redirect_uri)

        verifier = secrets.token_urlsafe(64)
        if has_request_context():
            session["pkce_verifier"] = verifier

        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("utf-8")).digest()
        ).decode("utf-8").rstrip("=")

        return (
            f"https://accounts.spotify.com/authorize"
            f"?client_id={self.client_id}"
            f"&response_type=code"
            f"&redirect_uri={encoded_redirect}"
            f"&scope={encoded_scopes}"
            f"&code_challenge_method=S256"
            f"&code_challenge={challenge}"
            f"&show_dialog=true"
        )

    def exchange_code(self, code, redirect_uri):
        verifier = None
        if has_request_context():
            verifier = session.pop("pkce_verifier", None)

        if not verifier:
            logger.warning("⚠️ PKCE verifier bulunamadi!")

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        data = {
            "grant_type":  "authorization_code",
            "code":         code,
            "redirect_uri": redirect_uri,
            "client_id":    self.client_id,
        }
        if verifier:
            data["code_verifier"] = verifier

        resp = requests.post(TOKEN_URL, headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        }, data=data)

        if resp.status_code != 200:
            logger.error(f"Code Exchange Hatasi: {resp.text}")
        resp.raise_for_status()

        d           = resp.json()
        new_token   = d["access_token"]
        new_expires = time.time() + d["expires_in"]
        new_refresh = d.get("refresh_token")

        self._access_token     = new_token
        self._token_expires_at = new_expires
        if new_refresh:
            self.refresh_token = new_refresh
            logger.info("✅ Yeni refresh token alindi.")
        return True

    def set_user_id_for_cache(self, user_id: str, sheets_client=None):
        """Login sonrası access token'ı bellek cache'ine ve Sheets'e kaydeder."""
        if self._access_token and self._token_expires_at:
            _access_token_cache[user_id] = {
                "access_token": self._access_token,
                "expires_at":   self._token_expires_at,
            }
            if sheets_client:
                try:
                    sheets_client.save_access_token(user_id, self._access_token, self._token_expires_at)
                    logger.info(f"✅ Access token Sheets'e yazildi: {user_id}")
                except Exception as e:
                    logger.warning(f"⚠️ Access token Sheets yazma hatasi: {e}")

    def _get_access_token(self, user_id: str = None, sheets_client=None):
        """
        Access token öncelik sırası:
          1. Bellek cache'i (en hızlı)
          2. Sheets (server restart sonrası kurtarma)
          3. Refresh token ile yenile → hem cache'e hem Sheets'e yaz
        """
        in_req = has_request_context()

        uid = user_id
        if not uid and in_req:
            uid = session.get("user_id")

        # 1) Bellek cache'i
        if uid and uid in _access_token_cache:
            cached = _access_token_cache[uid]
            if time.time() < cached["expires_at"] - 60:
                return cached["access_token"]

        # 2) Instance değişkeni (background sync)
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        # 3) Sheets'ten oku (server restart sonrası)
        if uid and sheets_client:
            try:
                saved = sheets_client.get_access_token(uid)
                if saved and time.time() < saved["expires_at"] - 60:
                    # Sheets'teki token hâlâ geçerli → cache'e al, kullan
                    _access_token_cache[uid] = saved
                    self._access_token     = saved["access_token"]
                    self._token_expires_at = saved["expires_at"]
                    logger.info(f"✅ Access token Sheets'ten yüklendi: {uid}")
                    return saved["access_token"]
            except Exception as e:
                logger.warning(f"⚠️ Sheets'ten token okuma hatasi: {e}")

        # 4) Refresh token ile yenile
        r_token = self.refresh_token
        if not r_token and in_req:
            r_token = session.get("refresh_token")
        if not r_token:
            r_token = os.environ.get("SPOTIFY_REFRESH_TOKEN", "")

        if not r_token:
            raise Exception("Oturum bulunamadi. Lutfen giris yapin.")

        return self._do_refresh(r_token, uid=uid, in_req=in_req, sheets_client=sheets_client)

    def _do_refresh(self, r_token: str, uid: str = None, in_req: bool = False, sheets_client=None):
        """Refresh token ile yeni access token alır; cache'e ve Sheets'e yazar."""
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        resp = requests.post(TOKEN_URL, headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        }, data={
            "grant_type":    "refresh_token",
            "refresh_token":  r_token,
        })

        if resp.status_code != 200:
            logger.error(f"Token yenileme hatasi: {resp.text}")
            if "invalid_grant" in resp.text:
                logger.error("❌ Refresh token gecersiz veya iptal edilmis!")
                if in_req:
                    session.clear()
                # Cache'den temizle
                if uid and uid in _access_token_cache:
                    del _access_token_cache[uid]
        resp.raise_for_status()

        d           = resp.json()
        new_token   = d["access_token"]
        new_expires = time.time() + d["expires_in"]
        new_refresh = d.get("refresh_token", r_token)

        # Instance'a yaz
        self._access_token     = new_token
        self._token_expires_at = new_expires
        self.refresh_token     = new_refresh

        # Bellek cache'ine yaz
        if uid:
            _access_token_cache[uid] = {
                "access_token": new_token,
                "expires_at":   new_expires,
            }

        # Sheets'e yaz (hem access token hem expires_at)
        if uid and sheets_client:
            try:
                sheets_client.save_access_token(uid, new_token, new_expires)
                logger.info(f"✅ Yenilenen access token Sheets'e yazildi: {uid}")
            except Exception as e:
                logger.warning(f"⚠️ Sheets access token yazma hatasi: {e}")

        # Session'a sadece refresh token değiştiyse yaz
        if in_req and new_refresh and new_refresh != r_token:
            session["refresh_token"] = new_refresh

        # Token rotasyonu
        if new_refresh and new_refresh != r_token:
            logger.info("🔄 Spotify yeni refresh token verdi (token rotasyonu).")
            if self._token_refresh_callback:
                try:
                    self._token_refresh_callback(new_refresh)
                except Exception as cb_err:
                    logger.warning(f"⚠️ Token refresh callback hatasi: {cb_err}")

        return new_token

    # ------------------------------------------------------------------ #
    #  CORE HTTP                                                           #
    # ------------------------------------------------------------------ #

    def _req(self, method, endpoint, **kwargs):
        # _sheets_client instance'a bağlanmışsa otomatik kullan
        sc = getattr(self, "_sheets_client", None)
        token = self._get_access_token(sheets_client=sc)
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        if "json" in kwargs:
            headers["Content-Type"] = "application/json"

        url = f"{API_BASE}{endpoint}" if not endpoint.startswith("http") else endpoint
        resp = requests.request(method, url, headers=headers, **kwargs)

        if resp.status_code >= 400:
            logger.error(
                f"❌ Spotify {resp.status_code} | {method} {endpoint} | {resp.text[:400]}"
            )

        if resp.status_code == 403:
            raise Exception(
                "403 Yasaklandi — Bu playlist'e erisim yetkiniz yok."
            )

        resp.raise_for_status()

        if resp.text and resp.text.strip():
            return resp.json()
        return {}

    # ------------------------------------------------------------------ #
    #  PLAYER                                                              #
    # ------------------------------------------------------------------ #

    def get_now_playing(self):
        try:
            data = self._req("GET", "/me/player/currently-playing")
        except Exception:
            return {"playing": False}

        if not data or data.get("currently_playing_type") != "track":
            return {"playing": False}

        item = data.get("item") or data.get("track")
        if not item:
            return {"playing": False}

        progress_ms = data.get("progress_ms", 0)
        duration_ms = item.get("duration_ms", 1)
        images      = item.get("album", {}).get("images", [])

        return {
            "playing":      True,
            "is_playing":   data.get("is_playing", False),
            "track_name":   item.get("name", ""),
            "artist_name":  ", ".join(a["name"] for a in item.get("artists", [])),
            "album_name":   item.get("album", {}).get("name", ""),
            "art_url":      images[0]["url"] if images else None,
            "progress_ms":  progress_ms,
            "duration_ms":  duration_ms,
            "progress_pct": round(progress_ms / duration_ms * 100, 1) if duration_ms else 0,
        }

    def play(self):
        return self._req("PUT", "/me/player/play")

    def pause(self):
        return self._req("PUT", "/me/player/pause")

    def next_track(self):
        return self._req("POST", "/me/player/next")

    def previous_track(self):
        return self._req("POST", "/me/player/previous")

    def get_liked_songs(self, limit=50):
        items = []
        url = "/me/tracks?limit=50"
        while url and len(items) < limit:
            data = self._req("GET", url)
            if not data or "items" not in data:
                break
            for it in data["items"]:
                t = it.get("track")
                if t:
                    items.append({
                        "id":          t["id"],
                        "name":        t["name"],
                        "artist":      ", ".join(a["name"] for a in t.get("artists", [])),
                        "duration_ms": t.get("duration_ms", 0),
                        "uri":         t["uri"],
                    })
                if len(items) >= limit:
                    break
            next_href = data.get("next")
            url = next_href.replace("https://api.spotify.com/v1", "") if next_href else None
        return items[:limit]

    def get_all_user_playlists(self):
        items = []
        url = "/me/playlists?limit=50"
        while url:
            data = self._req("GET", url)
            if not data or "items" not in data:
                break
            items.extend(data["items"] or [])
            next_href = data.get("next")
            url = next_href.replace("https://api.spotify.com/v1", "") if next_href else None
        return items

    def get_recently_played(self, limit=50, after_ms: int = None):
        params = {"limit": min(limit, 50)}
        if after_ms:
            params["after"] = after_ms

        data   = self._req("GET", "/me/player/recently-played", params=params)
        tracks = []
        for item in data.get("items", []):
            track = item.get("track", {})
            if not track:
                continue

            raw_played_at = item["played_at"]
            try:
                from datetime import datetime, timezone
                dt = datetime.strptime(raw_played_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                    microsecond=0, tzinfo=timezone.utc
                )
                normalized_played_at = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            except Exception:
                normalized_played_at = raw_played_at

            tracks.append({
                "played_at":    normalized_played_at,
                "track_id":     track.get("id", ""),
                "track_name":   track.get("name", ""),
                "artist_name":  ", ".join(a["name"] for a in track.get("artists", [])),
                "album_name":   track.get("album", {}).get("name", ""),
                "duration_ms":  track.get("duration_ms", 0),
                "duration_sec": round(track.get("duration_ms", 0) / 1000),
            })
        return tracks

    def _get_user_id(self):
        if not self._user_id:
            self._user_id = self._req("GET", "/me")["id"]
        return self._user_id

    # ------------------------------------------------------------------ #
    #  PLAYLISTS — READ                                                    #
    # ------------------------------------------------------------------ #

    def get_playlists(self):
        all_playlists = []
        url    = "/me/playlists"
        params = {"limit": 50, "offset": 0}
        while url:
            data = self._req("GET", url, params=params)
            for p in data.get("items", []):
                if not p:
                    continue
                image_url   = p["images"][0]["url"] if p.get("images") else None
                track_count = (p.get("items") or p.get("tracks") or {}).get("total", 0)
                all_playlists.append({
                    "id":          p["id"],
                    "name":        p["name"],
                    "track_count": track_count,
                    "image_url":   image_url,
                    "owner":       p["owner"]["display_name"],
                })
            url    = data.get("next")
            params = {}
        return all_playlists

    def _get_playlist_tracks(self, playlist_id):
        tracks = []
        url    = f"/playlists/{playlist_id}/items"
        params = {"limit": 50, "offset": 0}
        while url:
            data = self._req("GET", url, params=params)
            for entry in data.get("items", []):
                if not entry:
                    continue
                track = entry.get("item") or entry.get("track")
                if not track:
                    continue
                if track.get("type") != "track":
                    continue
                if not track.get("id"):
                    continue
                tracks.append(track)
            url    = data.get("next")
            params = {}
        logger.info(f"Playlist {playlist_id}: {len(tracks)} sarki yuklendi")
        return tracks

    # ------------------------------------------------------------------ #
    #  PLAYLISTS — WRITE                                                   #
    # ------------------------------------------------------------------ #

    def _search_track(self, query):
        try:
            data  = self._req("GET", "/search",
                              params={"q": query, "type": "track", "limit": 1})
            items = data.get("tracks", {}).get("items", [])
            return items[0]["id"] if items else None
        except Exception as e:
            logger.warning(f"Arama hatasi '{query}': {e}")
            return None

    def create_playlist_from_track_names(self, name, track_names, description=""):
        pl = self._req("POST", "/me/playlists", json={
            "name":        name,
            "public":      False,
            "description": description,
        })
        playlist_id = pl["id"]
        uris = []
        for track_name in track_names:
            tid = self._search_track(track_name)
            if tid:
                uris.append(f"spotify:track:{tid}")
        for i in range(0, len(uris), 100):
            self._req("POST", f"/playlists/{playlist_id}/items", json={"uris": uris[i:i + 100]})
        logger.info(f"Playlist olusturuldu: {playlist_id}, {len(uris)} sarki eklendi")
        return playlist_id

    def shuffle_playlist(self, playlist_id):
        import random
        tracks = self._get_playlist_tracks(playlist_id)
        if not tracks:
            raise Exception("Playlist bos veya erisim yetkiniz yok.")
        track_ids  = [t["id"] for t in tracks if t.get("id")]
        random.shuffle(track_ids)
        track_uris = [f"spotify:track:{tid}" for tid in track_ids]
        self._req("PUT", f"/playlists/{playlist_id}/items", json={"uris": track_uris[:100]})
        for i in range(100, len(track_uris), 100):
            self._req("POST", f"/playlists/{playlist_id}/items", json={"uris": track_uris[i:i + 100]})
        logger.info(f"Playlist karistirildi: {len(track_uris)} sarki")

    def _remove_tracks_from_playlist(self, playlist_id, track_ids):
        if not track_ids:
            return 0
        uris = [f"spotify:track:{tid}" for tid in track_ids]
        for i in range(0, len(uris), 100):
            chunk = [{"uri": u} for u in uris[i:i + 100]]
            self._req("DELETE", f"/playlists/{playlist_id}/items", json={"items": chunk})
        return len(uris)

    # ------------------------------------------------------------------ #
    #  LIBRARY — LIKE / UNLIKE                                            #
    # ------------------------------------------------------------------ #

    def _get_liked_track_ids(self, track_ids):
        liked = set()
        for i in range(0, len(track_ids), 40):
            chunk = track_ids[i:i + 40]
            try:
                uris = [f"spotify:track:{tid}" for tid in chunk]
                data = self._req("GET", "/me/library/contains",
                                 params={"uris": ",".join(uris)})
                if isinstance(data, list):
                    for j, is_liked in enumerate(data):
                        if is_liked:
                            liked.add(chunk[j])
            except Exception as e:
                logger.warning(f"library/contains hatasi: {e}")
        return liked

    def like_all_tracks_in_playlist(self, playlist_id):
        tracks = self._get_playlist_tracks(playlist_id)
        uris   = [f"spotify:track:{t['id']}" for t in tracks if t.get("id")]
        for i in range(0, len(uris), 40):
            self._req("PUT", "/me/library", params={"uris": ",".join(uris[i:i + 40])})
        return len(uris)

    def unlike_all_tracks_in_playlist(self, playlist_id):
        tracks = self._get_playlist_tracks(playlist_id)
        uris   = [f"spotify:track:{t['id']}" for t in tracks if t.get("id")]
        for i in range(0, len(uris), 40):
            self._req("DELETE", "/me/library", params={"uris": ",".join(uris[i:i + 40])})
        return len(uris)

    def remove_liked_tracks_from_playlist(self, playlist_id):
        tracks    = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        liked_ids = self._get_liked_track_ids(track_ids)
        return self._remove_tracks_from_playlist(playlist_id, list(liked_ids))

    def remove_unliked_tracks_from_playlist(self, playlist_id):
        tracks    = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        liked_ids = self._get_liked_track_ids(track_ids)
        unliked   = [tid for tid in track_ids if tid not in liked_ids]
        return self._remove_tracks_from_playlist(playlist_id, unliked)

    # ------------------------------------------------------------------ #
    #  FOLLOW / UNFOLLOW ARTISTS                                          #
    # ------------------------------------------------------------------ #

    def follow_all_artists_in_playlist(self, playlist_id):
        tracks     = self._get_playlist_tracks(playlist_id)
        artist_ids = list({a["id"] for t in tracks for a in t.get("artists", []) if a.get("id")})
        uris = [f"spotify:artist:{aid}" for aid in artist_ids]
        for i in range(0, len(uris), 40):
            self._req("PUT", "/me/library", params={"uris": ",".join(uris[i:i + 40])})
        return len(artist_ids)

    def unfollow_all_artists_in_playlist(self, playlist_id):
        tracks     = self._get_playlist_tracks(playlist_id)
        artist_ids = list({a["id"] for t in tracks for a in t.get("artists", []) if a.get("id")})
        uris = [f"spotify:artist:{aid}" for aid in artist_ids]
        for i in range(0, len(uris), 40):
            self._req("DELETE", "/me/library", params={"uris": ",".join(uris[i:i + 40])})
        return len(artist_ids)

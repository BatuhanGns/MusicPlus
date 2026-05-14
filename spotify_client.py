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


class SpotifyClient:
    def __init__(self):
        self.client_id        = os.environ.get("SPOTIFY_CLIENT_ID", "")
        self.client_secret    = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        self.refresh_token    = os.environ.get("SPOTIFY_REFRESH_TOKEN", "")
        self._access_token    = None
        self._token_expires_at = 0
        self._user_id         = None

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

        if has_request_context():
            session["access_token"]    = new_token
            session["token_expires_at"] = new_expires
            if new_refresh:
                session["refresh_token"] = new_refresh

        self._access_token     = new_token
        self._token_expires_at = new_expires
        if new_refresh:
            self.refresh_token = new_refresh
            logger.info("✅ Yeni refresh token alindi.")
        return True

    def _get_access_token(self):
        in_req = has_request_context()

        if in_req:
            access_token = session.get("access_token")
            expires_at   = session.get("token_expires_at", 0)
            r_token      = (session.get("refresh_token")
                            or self.refresh_token
                            or os.environ.get("SPOTIFY_REFRESH_TOKEN", ""))
        else:
            access_token = self._access_token
            expires_at   = self._token_expires_at
            r_token      = (self.refresh_token
                            or os.environ.get("SPOTIFY_REFRESH_TOKEN", ""))

        if access_token and time.time() < expires_at - 60:
            return access_token

        if not r_token:
            raise Exception("Oturum bulunamadi. Lutfen giris yapin.")

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
            if in_req and "invalid_grant" in resp.text:
                session.clear()
        resp.raise_for_status()

        d           = resp.json()
        new_token   = d["access_token"]
        new_expires = time.time() + d["expires_in"]
        new_refresh = d.get("refresh_token", r_token)

        if in_req:
            session["access_token"]    = new_token
            session["token_expires_at"] = new_expires
            session["refresh_token"]   = new_refresh

        self._access_token     = new_token
        self._token_expires_at = new_expires
        self.refresh_token     = new_refresh
        return new_token

    # ------------------------------------------------------------------ #
    #  CORE HTTP                                                           #
    # ------------------------------------------------------------------ #

    def _req(self, method, endpoint, **kwargs):
        token = self._get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        # Spotify JSON body isteklerinde Content-Type zorunlu
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
                "403 Yasaklandi — Bu playlist'e erisim yetkiniz yok. "
                "Spotify Developer Mode'da yalnizca hesap sahibi ve "
                "en fazla 5 whitelisted kullanici islem yapabilir."
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

        # Player response'da alan adi "item" dir
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

    def get_recently_played(self, limit=50):
        data   = self._req("GET", "/me/player/recently-played", params={"limit": limit})
        tracks = []
        for item in data.get("items", []):
            track = item.get("track", {})
            if not track:
                continue
            tracks.append({
                "played_at":   item["played_at"],
                "track_id":    track.get("id", ""),
                "track_name":  track.get("name", ""),
                "artist_name": ", ".join(a["name"] for a in track.get("artists", [])),
                "album_name":  track.get("album", {}).get("name", ""),
                "duration_ms": track.get("duration_ms", 0),
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
        """
        Spotify Subat 2026 degisiklikleri:
          - Endpoint: GET /playlists/{id}/tracks  ->  GET /playlists/{id}/items
          - Her ogrenin icindeki alan adi: "track"  ->  "item"
            (Geriye donuk uyum icin ikisi birden denenir)
          - Sadece kullanicinin sahip oldugu veya ortak calistigi
            playlist'ler erisime acik; digerlerinde 403 doner.
        """
        tracks = []
        url    = f"/playlists/{playlist_id}/items"
        params = {"limit": 50, "offset": 0}
        while url:
            data  = self._req("GET", url, params=params)
            for entry in data.get("items", []):
                if not entry:
                    continue
                # 2026: alan adi "item" oldu; eski "track" de denenir
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
        """Sarki adina gore Spotify track ID dondurur."""
        try:
            data  = self._req("GET", "/search",
                              params={"q": query, "type": "track", "limit": 1})
            items = data.get("tracks", {}).get("items", [])
            return items[0]["id"] if items else None
        except Exception as e:
            logger.warning(f"Arama hatasi '{query}': {e}")
            return None

    def create_playlist_from_track_names(self, name, track_names, description=""):
        """
        Subat 2026: POST /users/{id}/playlists  KALDIRILDI
                    POST /me/playlists  kullan
        """
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

        # POST /playlists/{id}/items  body: {"uris": [...]}
        for i in range(0, len(uris), 100):
            self._req("POST", f"/playlists/{playlist_id}/items", json={
                "uris": uris[i:i + 100]
            })

        logger.info(f"Playlist olusturuldu: {playlist_id}, {len(uris)} sarki eklendi")
        return playlist_id

    def shuffle_playlist(self, playlist_id):
        """
        Playlist'i karistirir.

        Adimlar:
          1. GET /playlists/{id}/items  ile tum track ID'leri al
          2. ID listesini bellekte karistir
          3. PUT /playlists/{id}/items  ile ilk 100'u replace et
             (bu islem playlist'in tum icerigini sifirlar)
          4. POST /playlists/{id}/items ile kalan sarkilari ekle

        DELETE kullanilmaz; 2026 API ile tam uyumlu.
        """
        import random

        tracks = self._get_playlist_tracks(playlist_id)
        if not tracks:
            raise Exception(
                "Playlist bos veya erisim yetkiniz yok. "
                "Yalnizca kendi sahip oldugunuz playlist'leri karistirabiliriniz."
            )

        track_ids = [t["id"] for t in tracks if t.get("id")]
        random.shuffle(track_ids)
        track_uris = [f"spotify:track:{tid}" for tid in track_ids]

        logger.info(f"{len(track_uris)} sarki karistiriliyor...")

        # PUT ile ilk 100 -> playlist icerigini tamamen replace eder
        self._req("PUT", f"/playlists/{playlist_id}/items", json={
            "uris": track_uris[:100]
        })

        # Kalan sarkilari POST ile ekle
        for i in range(100, len(track_uris), 100):
            self._req("POST", f"/playlists/{playlist_id}/items", json={
                "uris": track_uris[i:i + 100]
            })

        logger.info(f"Playlist karistirildi: {len(track_uris)} sarki")

    def _remove_tracks_from_playlist(self, playlist_id, track_ids):
        """
        Subat 2026: DELETE /playlists/{id}/tracks  KALDIRILDI
                    DELETE /playlists/{id}/items   kullan
                    Body: {"items": [{"uri": "spotify:track:..."}, ...]}
        """
        if not track_ids:
            return 0
        uris = [f"spotify:track:{tid}" for tid in track_ids]
        for i in range(0, len(uris), 100):
            chunk = [{"uri": u} for u in uris[i:i + 100]]
            self._req("DELETE", f"/playlists/{playlist_id}/items", json={
                "items": chunk
            })
        return len(uris)

    # ------------------------------------------------------------------ #
    #  LIBRARY — LIKE / UNLIKE                                            #
    # ------------------------------------------------------------------ #

    def _get_liked_track_ids(self, track_ids):
        """
        Subat 2026: GET /me/tracks/contains   KALDIRILDI
                    GET /me/library/contains  kullan
                    Parametre: uris  (ID degil, spotify:track:... formati)
        """
        liked = set()
        for i in range(0, len(track_ids), 50):
            chunk = track_ids[i:i + 50]
            uris  = [f"spotify:track:{tid}" for tid in chunk]
            try:
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
        """
        Subat 2026: PUT /me/tracks   KALDIRILDI
                    PUT /me/library  kullan
                    Body: {"uris": ["spotify:track:...", ...]}
        """
        tracks    = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        uris      = [f"spotify:track:{tid}" for tid in track_ids]
        for i in range(0, len(uris), 50):
            self._req("PUT", "/me/library", json={"uris": uris[i:i + 50]})
        return len(uris)

    def unlike_all_tracks_in_playlist(self, playlist_id):
        """
        Subat 2026: DELETE /me/tracks   KALDIRILDI
                    DELETE /me/library  kullan
                    Body: {"uris": ["spotify:track:...", ...]}
        """
        tracks    = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        uris      = [f"spotify:track:{tid}" for tid in track_ids]
        for i in range(0, len(uris), 50):
            self._req("DELETE", "/me/library", json={"uris": uris[i:i + 50]})
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
        """
        Subat 2026: PUT /me/following   KALDIRILDI
                    PUT /me/library     kullan
                    Body: {"uris": ["spotify:artist:...", ...]}
        """
        tracks     = self._get_playlist_tracks(playlist_id)
        artist_ids = list({
            a["id"]
            for t in tracks
            for a in t.get("artists", [])
            if a.get("id")
        })
        uris = [f"spotify:artist:{aid}" for aid in artist_ids]
        for i in range(0, len(uris), 50):
            self._req("PUT", "/me/library", json={"uris": uris[i:i + 50]})
        return len(artist_ids)

    def unfollow_all_artists_in_playlist(self, playlist_id):
        """
        Subat 2026: DELETE /me/following   KALDIRILDI
                    DELETE /me/library     kullan
                    Body: {"uris": ["spotify:artist:...", ...]}
        """
        tracks     = self._get_playlist_tracks(playlist_id)
        artist_ids = list({
            a["id"]
            for t in tracks
            for a in t.get("artists", [])
            if a.get("id")
        })
        uris = [f"spotify:artist:{aid}" for aid in artist_ids]
        for i in range(0, len(uris), 50):
            self._req("DELETE", "/me/library", json={"uris": uris[i:i + 50]})
        return len(artist_ids)

import os
import time
import logging
import requests
import base64
import urllib.parse
from flask import session, has_request_context

logger = logging.getLogger(__name__)

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

class SpotifyClient:
    def __init__(self):
        self.client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
        self.client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        self.refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN", "")
        self._access_token = None
        self._token_expires_at = 0
        self._user_id = None

    def get_auth_url(self, redirect_uri):
        scopes = " ".join([
            "user-read-recently-played",
            "user-read-currently-playing",
            "playlist-read-private",
            "playlist-modify-public",
            "playlist-modify-private",
        ])
        encoded_scopes   = urllib.parse.quote(scopes)
        encoded_redirect = urllib.parse.quote(redirect_uri)
        return f"https://accounts.spotify.com/authorize?client_id={self.client_id}&response_type=code&redirect_uri={encoded_redirect}&scope={encoded_scopes}&show_dialog=true"

    def exchange_code(self, code, redirect_uri):
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        resp = requests.post(TOKEN_URL, headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri
        })
        
        if resp.status_code != 200:
            logger.error(f"Code Exchange Hatası: {resp.text}")
        resp.raise_for_status()
        data = resp.json()
        
        new_access_token = data["access_token"]
        new_expires_at = time.time() + data["expires_in"]
        new_refresh_token = data.get("refresh_token")

        if has_request_context():
            session["access_token"] = new_access_token
            session["token_expires_at"] = new_expires_at
            if new_refresh_token:
                session["refresh_token"] = new_refresh_token
        
        self._access_token = new_access_token
        self._token_expires_at = new_expires_at
        if new_refresh_token:
            self.refresh_token = new_refresh_token
            logger.info("✅ YENİ REFRESH TOKEN ALINDI VE SESSION'A KAYDEDİLDİ.")
            
        return True

    def _get_access_token(self):
        in_req = has_request_context()
        
        if in_req:
            access_token = session.get("access_token")
            expires_at = session.get("token_expires_at", 0)
            r_token = session.get("refresh_token") or self.refresh_token or os.environ.get("SPOTIFY_REFRESH_TOKEN", "")
        else:
            access_token = self._access_token
            expires_at = self._token_expires_at
            r_token = self.refresh_token or os.environ.get("SPOTIFY_REFRESH_TOKEN", "")

        if access_token and time.time() < expires_at - 60:
            return access_token

        if not r_token:
            raise Exception("Geçerli bir oturum bulunamadı. Lütfen giriş yapın.")

        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = requests.post(TOKEN_URL, headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }, data={
            "grant_type": "refresh_token",
            "refresh_token": r_token
        })
        
        if resp.status_code != 200:
            logger.error(f"Token Yenileme (Refresh) Hatası: {resp.text}")
            if in_req and "invalid_grant" in resp.text:
                session.clear()
        resp.raise_for_status()
        
        data = resp.json()
        new_access_token = data["access_token"]
        new_expires_at = time.time() + data["expires_in"]
        new_refresh_token = data.get("refresh_token", r_token)

        if in_req:
            session["access_token"] = new_access_token
            session["token_expires_at"] = new_expires_at
            session["refresh_token"] = new_refresh_token
        
        self._access_token = new_access_token
        self._token_expires_at = new_expires_at
        self.refresh_token = new_refresh_token

        return new_access_token

    def _req(self, method, endpoint, **kwargs):
        token = self._get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        url = f"{API_BASE}{endpoint}" if not endpoint.startswith("http") else endpoint
        
        resp = requests.request(method, url, headers=headers, **kwargs)
        resp.raise_for_status()
        if resp.text:
            return resp.json()
        return {}

    def get_now_playing(self):
        """Şu an çalan şarkıyı döndürür"""
        try:
            data = self._req("GET", "/me/player/currently-playing")
        except Exception:
            return {"playing": False}

        if not data or data.get("currently_playing_type") != "track":
            return {"playing": False}

        item = data.get("item") or data.get("track")
        if not item:
            return {"playing": False}

        is_playing  = data.get("is_playing", False)
        progress_ms = data.get("progress_ms", 0)
        duration_ms = item.get("duration_ms", 1)
        images  = item.get("album", {}).get("images", [])
        # En yüksek kaliteli resmi seç (images genelde büyükten küçüğe sıralı)
        art_url = images[0]["url"] if images else None

        return {
            "playing":     True,
            "is_playing":  is_playing,
            "track_name":  item.get("name", ""),
            "artist_name": ", ".join(a["name"] for a in item.get("artists", [])),
            "album_name":  item.get("album", {}).get("name", ""),
            "art_url":     art_url,
            "progress_ms": progress_ms,
            "duration_ms": duration_ms,
            "progress_pct": round(progress_ms / duration_ms * 100, 1) if duration_ms else 0,
        }

    def get_recently_played(self, limit=50):
        data = self._req("GET", "/me/player/recently-played", params={"limit": limit})
        tracks = []
        for item in data.get("items", []):
            track = item["track"]
            tracks.append({
                "played_at": item["played_at"],
                "track_id": track["id"],
                "track_name": track["name"],
                "artist_name": ", ".join(a["name"] for a in track["artists"]),
                "album_name": track["album"]["name"],
                "duration_ms": track["duration_ms"],
                "duration_sec": round(track["duration_ms"] / 1000),
            })
        return tracks

    def _get_user_id(self):
        if not self._user_id:
            data = self._req("GET", "/me")
            self._user_id = data["id"]
        return self._user_id

    def get_playlists(self):
        """Kullanıcının tüm playlistlerini döndürür (resimli)"""
        all_playlists = []
        url = "/me/playlists"
        params = {"limit": 50, "offset": 0}
        while url:
            data = self._req("GET", url, params=params)
            for p in data.get("items", []):
                if not p:
                    continue
                image_url = p["images"][0]["url"] if p.get("images") else None
                track_count = (p.get("items") or p.get("tracks") or {}).get("total", 0)
                all_playlists.append({
                    "id": p["id"],
                    "name": p["name"],
                    "track_count": track_count,
                    "image_url": image_url,
                    "owner": p["owner"]["display_name"],
                })
            if data.get("next"):
                url = data["next"]
                params = {}
            else:
                url = None
        return all_playlists

    def _get_playlist_tracks(self, playlist_id):
        """Bir playlistin tüm parçalarını döndürür"""
        tracks = []
        url = f"/playlists/{playlist_id}/items"
        # fields parametresi kaldırıldı - API artık item key'i kullanıyor, fields filtresi karıştırıyor
        params = {"limit": 100, "offset": 0}
        while url:
            data = self._req("GET", url, params=params)
            for item in data.get("items", []):
                if not item:
                    continue
                # API artık track yerine item key'i döndürüyor
                track = item.get("item") or item.get("track")
                if track and track.get("id") and track.get("type") == "track":
                    tracks.append(track)
            if data.get("next"):
                url = data["next"]
                params = {}
            else:
                url = None
        return tracks

    def _search_track(self, track_name):
        """Şarkı adına göre Spotify'da arama yapar"""
        data = self._req("GET", "/search", params={"q": track_name, "type": "track", "limit": 1})
        items = data.get("tracks", {}).get("items", [])
        return items[0]["id"] if items else None

    def create_playlist_from_track_names(self, name, track_names, description=""):
        """Şarkı adları listesinden yeni bir playlist oluşturur"""
        user_id = self._get_user_id()
        pl = self._req("POST", f"/users/{user_id}/playlists", json={
            "name": name,
            "public": False,
            "description": description
        })
        playlist_id = pl["id"]

        track_ids = []
        for track_name in track_names:
            tid = self._search_track(track_name)
            if tid:
                track_ids.append(f"spotify:track:{tid}")

        for i in range(0, len(track_ids), 100):
            self._req("POST", f"/playlists/{playlist_id}/items", json={
                "uris": track_ids[i:i+100]
            })
        return playlist_id

    def shuffle_playlist(self, playlist_id):
        """Bir playlistin şarkılarını rastgele karıştırır"""
        import random
        tracks = self._get_playlist_tracks(playlist_id)
        track_uris = [f"spotify:track:{t['id']}" for t in tracks if t.get("id")]
        random.shuffle(track_uris)
        # Önce playlist'i boşalt - DELETE body formatı: {"uris": [...]}
        for i in range(0, len(track_uris), 100):
            self._req("DELETE", f"/playlists/{playlist_id}/items", json={
                "uris": track_uris[i:i+100]
            })
        # Karışık olarak ekle
        for i in range(0, len(track_uris), 100):
            self._req("POST", f"/playlists/{playlist_id}/items", json={
                "uris": track_uris[i:i+100]
            })

    def follow_all_artists_in_playlist(self, playlist_id):
        """Playlistteki tüm sanatçıları takip eder"""
        tracks = self._get_playlist_tracks(playlist_id)
        artist_ids = list({a["id"] for t in tracks for a in t.get("artists", []) if a.get("id")})
        for i in range(0, len(artist_ids), 50):
            ids_chunk = artist_ids[i:i+50]
            self._req("PUT", "/me/following", params={"type": "artist", "ids": ",".join(ids_chunk)})
        return len(artist_ids)

    def unfollow_all_artists_in_playlist(self, playlist_id):
        """Playlistteki tüm sanatçıların takibini bırakır"""
        tracks = self._get_playlist_tracks(playlist_id)
        artist_ids = list({a["id"] for t in tracks for a in t.get("artists", []) if a.get("id")})
        for i in range(0, len(artist_ids), 50):
            ids_chunk = artist_ids[i:i+50]
            self._req("DELETE", "/me/following", params={"type": "artist", "ids": ",".join(ids_chunk)})
        return len(artist_ids)

    def _get_liked_track_ids(self, track_ids):
        """Verilen track_id'lerden beğenilenleri döndürür"""
        liked = set()
        for i in range(0, len(track_ids), 50):
            chunk = track_ids[i:i+50]
            data = self._req("GET", "/me/tracks/contains", params={"ids": ",".join(chunk)})
            if isinstance(data, list):
                for j, is_liked in enumerate(data):
                    if is_liked:
                        liked.add(chunk[j])
        return liked

    def like_all_tracks_in_playlist(self, playlist_id):
        """Playlistteki tüm şarkıları beğenir"""
        tracks = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        for i in range(0, len(track_ids), 50):
            self._req("PUT", "/me/tracks", params={"ids": ",".join(track_ids[i:i+50])})
        return len(track_ids)

    def unlike_all_tracks_in_playlist(self, playlist_id):
        """Playlistteki tüm şarkıların beğenisini kaldırır"""
        tracks = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        for i in range(0, len(track_ids), 50):
            self._req("DELETE", "/me/tracks", params={"ids": ",".join(track_ids[i:i+50])})
        return len(track_ids)

    def remove_liked_tracks_from_playlist(self, playlist_id):
        """Playlistten beğenilen şarkıları çıkarır"""
        tracks = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        liked_ids = self._get_liked_track_ids(track_ids)
        liked_uris = [f"spotify:track:{tid}" for tid in liked_ids]
        for i in range(0, len(liked_uris), 100):
            self._req("DELETE", f"/playlists/{playlist_id}/items", json={
                "uris": liked_uris[i:i+100]
            })
        return len(liked_uris)

    def remove_unliked_tracks_from_playlist(self, playlist_id):
        """Playlistten beğenilmeyen şarkıları çıkarır"""
        tracks = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        liked_ids = self._get_liked_track_ids(track_ids)
        unliked_uris = [f"spotify:track:{tid}" for tid in track_ids if tid not in liked_ids]
        for i in range(0, len(unliked_uris), 100):
            self._req("DELETE", f"/playlists/{playlist_id}/items", json={
                "uris": unliked_uris[i:i+100]
            })
        return len(unliked_uris)
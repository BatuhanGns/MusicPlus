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
        # 2026 GÜNCELLEMESİ: İşlem yapabilmek için kapsamlar genişletildi
        scopes = " ".join([
            "user-read-recently-played",
            "user-read-currently-playing",
            "playlist-read-private",
            "playlist-modify-public",
            "playlist-modify-private",
            "user-follow-modify",       # Sanatçı takip etmek için
            "user-library-modify",      # Şarkı beğenmek/çıkarmak için
            "user-library-read"
        ])
        encoded_scopes   = urllib.parse.quote(scopes)
        encoded_redirect = urllib.parse.quote(redirect_uri)

        # 2026 GÜNCELLEMESİ: PKCE (Proof Key for Code Exchange) Zorunluluğu
        # Güvenlik kodunu oluştur ve session içine kaydet
        verifier = secrets.token_urlsafe(64)
        if has_request_context():
            session["pkce_verifier"] = verifier

        # Challenge (Meydan Okuma) kodunu SHA256 ile şifrele
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode('utf-8')).digest()
        ).decode('utf-8').rstrip('=')

        # code_challenge ve methodunu URL'ye ekle
        return f"https://accounts.spotify.com/authorize?client_id={self.client_id}&response_type=code&redirect_uri={encoded_redirect}&scope={encoded_scopes}&code_challenge_method=S256&code_challenge={challenge}&show_dialog=true"

    def exchange_code(self, code, redirect_uri):
        # 2026 GÜNCELLEMESİ: PKCE doğrulama kodunu session'dan geri al
        verifier = None
        if has_request_context():
            verifier = session.pop("pkce_verifier", None)

        if not verifier:
            logger.warning("⚠️ PKCE verifier bulunamadı! Tarayıcı çerezleri veya oturum süresi dolmuş olabilir.")

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id
        }
        
        # PKCE doğrulaması body içerisinde gönderilmek zorunda
        if verifier:
            data["code_verifier"] = verifier

        resp = requests.post(TOKEN_URL, headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }, data=data)
        
        if resp.status_code != 200:
            logger.error(f"Code Exchange Hatası (PKCE): {resp.text}")
        resp.raise_for_status()
        data_resp = resp.json()
        
        new_access_token = data_resp["access_token"]
        new_expires_at = time.time() + data_resp["expires_in"]
        new_refresh_token = data_resp.get("refresh_token")

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
        
        # 2026 GÜNCELLEMESİ: Geliştirici kotası (Premium ve 5 Kullanıcı Kısıtı) hatası yakalama
        if resp.status_code == 403:
            logger.error(f"403 Yasaklandı - {endpoint} - Premium hesap veya Whitelist gereksinimi olabilir.")
            raise Exception("403 Yasaklandı - Spotify 2026 API Kuralları: Bu işlem Geliştirici Modunda yalnızca Premium hesaba sahip Whitelist (ilk 5) kullanıcılar için çalışır.")

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
        tracks = []
        url = f"/playlists/{playlist_id}/items"
        params = {"limit": 100, "offset": 0}
        while url:
            data = self._req("GET", url, params=params)
            for item in data.get("items", []):
                if not item:
                    continue
                track = item.get("track")
                if track and track.get("id") and track.get("type") == "track":
                    tracks.append(track)
            if data.get("next"):
                url = data["next"]
                params = {}
            else:
                url = None
        return tracks

    def _search_track(self, track_name):
        data = self._req("GET", "/search", params={"q": track_name, "type": "track", "limit": 1})
        items = data.get("tracks", {}).get("items", [])
        return items[0]["id"] if items else None

    def create_playlist_from_track_names(self, name, track_names, description=""):
        # 2026 API: POST /users/{id}/playlists kaldırıldı → POST /me/playlists kullan
        pl = self._req("POST", "/me/playlists", json={
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
        import random
        tracks = self._get_playlist_tracks(playlist_id)
        track_uris = [f"spotify:track:{t['id']}" for t in tracks if t.get("id")]
        random.shuffle(track_uris)
        # 2026 API: DELETE /playlists/{id}/items → body: {"items": [{"uri": "..."}]}
        for i in range(0, len(track_uris), 100):
            chunk = [{"uri": u} for u in track_uris[i:i+100]]
            self._req("DELETE", f"/playlists/{playlist_id}/items", json={
                "items": chunk
            })
        for i in range(0, len(track_uris), 100):
            self._req("POST", f"/playlists/{playlist_id}/items", json={
                "uris": track_uris[i:i+100]
            })

    def follow_all_artists_in_playlist(self, playlist_id):
        tracks = self._get_playlist_tracks(playlist_id)
        artist_ids = list({a["id"] for t in tracks for a in t.get("artists", []) if a.get("id")})
        for i in range(0, len(artist_ids), 50):
            ids_chunk = artist_ids[i:i+50]
            self._req("PUT", "/me/following", params={"type": "artist", "ids": ",".join(ids_chunk)})
        return len(artist_ids)

    def unfollow_all_artists_in_playlist(self, playlist_id):
        tracks = self._get_playlist_tracks(playlist_id)
        artist_ids = list({a["id"] for t in tracks for a in t.get("artists", []) if a.get("id")})
        for i in range(0, len(artist_ids), 50):
            ids_chunk = artist_ids[i:i+50]
            self._req("DELETE", "/me/following", params={"type": "artist", "ids": ",".join(ids_chunk)})
        return len(artist_ids)

    def _get_liked_track_ids(self, track_ids):
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
        tracks = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        # 2026 API: PUT /me/tracks → JSON body {"ids": [...]}
        for i in range(0, len(track_ids), 50):
            self._req("PUT", "/me/tracks", json={"ids": track_ids[i:i+50]})
        return len(track_ids)

    def unlike_all_tracks_in_playlist(self, playlist_id):
        tracks = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        # 2026 API: DELETE /me/tracks → JSON body {"ids": [...]}
        for i in range(0, len(track_ids), 50):
            self._req("DELETE", "/me/tracks", json={"ids": track_ids[i:i+50]})
        return len(track_ids)

    def remove_liked_tracks_from_playlist(self, playlist_id):
        tracks = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        liked_ids = self._get_liked_track_ids(track_ids)
        liked_uris = [f"spotify:track:{tid}" for tid in liked_ids]
        # 2026 API: DELETE /playlists/{id}/items → body: {"items": [{"uri": "..."}]}
        for i in range(0, len(liked_uris), 100):
            chunk = [{"uri": u} for u in liked_uris[i:i+100]]
            self._req("DELETE", f"/playlists/{playlist_id}/items", json={
                "items": chunk
            })
        return len(liked_uris)

    def remove_unliked_tracks_from_playlist(self, playlist_id):
        tracks = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        liked_ids = self._get_liked_track_ids(track_ids)
        unliked_uris = [f"spotify:track:{tid}" for tid in track_ids if tid not in liked_ids]
        # 2026 API: DELETE /playlists/{id}/items → body: {"items": [{"uri": "..."}]}
        for i in range(0, len(unliked_uris), 100):
            chunk = [{"uri": u} for u in unliked_uris[i:i+100]]
            self._req("DELETE", f"/playlists/{playlist_id}/items", json={
                "items": chunk
            })
        return len(unliked_uris)
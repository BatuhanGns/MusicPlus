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
        scopes = "playlist-read-private playlist-read-collaborative playlist-modify-public playlist-modify-private user-library-modify user-follow-modify user-read-recently-played"
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": scopes
        }
        url_params = urllib.parse.urlencode(params)
        return f"https://accounts.spotify.com/authorize?{url_params}"

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

        # Vercel Serverless durumu için verileri tarayıcı çerezine (session) kaydediyoruz
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
        # 1. Ortam kontrolü: Vercel üzerinde Flask isteği içinde miyiz?
        in_req = has_request_context()
        
        # 2. Tokenları çerezden (session) al. Vercel unutsun, çerez unutmaz.
        if in_req:
            access_token = session.get("access_token", self._access_token)
            expires_at = session.get("token_expires_at", self._token_expires_at)
            r_token = session.get("refresh_token") or self.refresh_token or os.environ.get("SPOTIFY_REFRESH_TOKEN", "")
        else:
            access_token = self._access_token
            expires_at = self._token_expires_at
            r_token = self.refresh_token or os.environ.get("SPOTIFY_REFRESH_TOKEN", "")

        # 3. Access token süresi geçerli mi?
        if access_token and time.time() < expires_at - 60:
            return access_token

        # 4. Yenilemek için Refresh Token zorunlu
        if not r_token:
            logger.error("Token yenilemek için Refresh token bulunamadı.")
            raise Exception("Geçerli bir oturum bulunamadı, lütfen çıkış yapıp tekrar Spotify ile giriş yapın.")

        # 5. Spotify API'den yeni yetki isteği
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
            # Eğer token tamamen patlamışsa kullanıcıyı sistemden çıkartıyoruz ki tekrar giriş yapsın
            if in_req and "invalid_grant" in resp.text:
                session.clear()
        resp.raise_for_status()
        
        data = resp.json()
        new_access_token = data["access_token"]
        new_expires_at = time.time() + data["expires_in"]
        new_refresh_token = data.get("refresh_token", r_token)

        # 6. Yeni verileri tekrar hafızaya al
        if in_req:
            session["access_token"] = new_access_token
            session["token_expires_at"] = new_expires_at
            session["refresh_token"] = new_refresh_token
        
        self._access_token = new_access_token
        self._token_expires_at = new_expires_at
        self.refresh_token = new_refresh_token

        logger.info("🔑 Spotify token arka planda yenilendi ve Vercel engeli aşıldı.")
        return new_access_token

    def _req(self, method, endpoint, **kwargs):
        token = self._get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        url = f"{API_BASE}{endpoint}" if not endpoint.startswith("http") else endpoint
        
        resp = requests.request(method, url, headers=headers, **kwargs)
        
        if resp.status_code >= 400:
            logger.error(f"Spotify API Error ({resp.status_code}) on {url}: {resp.text}")
        
        resp.raise_for_status()
        if resp.text:
            return resp.json()
        return {}

    def get_me(self):
        if not self._user_id:
            data = self._req("GET", "/me")
            self._user_id = data["id"]
        return self._user_id

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

    # --- PLAYLIST & EDIT İŞLEMLERİ ---
    
    def get_my_playlists(self):
        data = self._req("GET", "/me/playlists", params={"limit": 50})
        return [{"id": p["id"], "name": p["name"], "count": p["tracks"]["total"]} for p in data.get("items", [])]

    def create_playlist(self, name, description="Müzik İstatistiklerin tarafından oluşturuldu."):
        user_id = self.get_me()
        payload = {"name": name, "description": description, "public": False}
        return self._req("POST", f"/users/{user_id}/playlists", json=payload)

    def add_to_playlist(self, playlist_id, track_ids):
        uris = [f"spotify:track:{tid}" for tid in track_ids if tid]
        for i in range(0, len(uris), 100):
            chunk = uris[i:i+100]
            self._req("POST", f"/playlists/{playlist_id}/tracks", json={"uris": chunk})

    def get_playlist_tracks(self, playlist_id):
        tracks = []
        url = f"/playlists/{playlist_id}/tracks?limit=100"
        while url:
            data = self._req("GET", url)
            for item in data.get("items", []):
                if item.get("track") and item["track"].get("id"):
                    tracks.append(item["track"])
            url = data.get("next")
        return tracks

    def replace_playlist_tracks(self, playlist_id, track_ids):
        uris = [f"spotify:track:{tid}" for tid in track_ids if tid]
        if not uris:
            return
        self._req("PUT", f"/playlists/{playlist_id}/tracks", json={"uris": uris[:100]})
        if len(uris) > 100:
            for i in range(100, len(uris), 100):
                self._req("POST", f"/playlists/{playlist_id}/tracks", json={"uris": uris[i:i+100]})

    def get_audio_features(self, track_ids):
        features = {}
        for i in range(0, len(track_ids), 100):
            chunk = track_ids[i:i+100]
            data = self._req("GET", "/audio-features", params={"ids": ",".join(chunk)})
            for feat in data.get("audio_features", []):
                if feat:
                    features[feat["id"]] = feat
        return features

    # --- TOPLU TAKİP / BEĞENİ İŞLEMLERİ ---

    def modify_following(self, action, artist_ids):
        artist_ids = list(set(artist_ids))
        for i in range(0, len(artist_ids), 50):
            chunk = artist_ids[i:i+50]
            self._req(action, "/me/following", params={"type": "artist", "ids": ",".join(chunk)})

    def modify_saved_tracks(self, action, track_ids):
        track_ids = list(set(track_ids))
        for i in range(0, len(track_ids), 50):
            chunk = track_ids[i:i+50]
            self._req(action, "/me/tracks", json={"ids": chunk})

    def check_saved_tracks(self, track_ids):
        results = {}
        for i in range(0, len(track_ids), 50):
            chunk = track_ids[i:i+50]
            data = self._req("GET", "/me/tracks/contains", params={"ids": ",".join(chunk)})
            for tid, is_saved in zip(chunk, data):
                results[tid] = is_saved
        return results
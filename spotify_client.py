import os
import time
import logging
import requests
import base64
import random

logger = logging.getLogger(__name__)

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

class SpotifyClient:
    def __init__(self):
        self.client_id = os.environ["SPOTIFY_CLIENT_ID"]
        self.client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]
        self.refresh_token = os.environ["SPOTIFY_REFRESH_TOKEN"]
        self._access_token = None
        self._token_expires_at = 0
        self._user_id = None

    def _get_access_token(self):
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        resp = requests.post(TOKEN_URL, headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }, data={
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token
        })
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + data["expires_in"]
        logger.info("🔑 Spotify token yenilendi.")
        return self._access_token

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
        # 100'erli paketler halinde ekleme yapılmalı
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
        # İlk 100'ü PUT ile değiştir, kalanları POST ile ekle
        self._req("PUT", f"/playlists/{playlist_id}/tracks", json={"uris": uris[:100]})
        if len(uris) > 100:
            for i in range(100, len(uris), 100):
                self._req("POST", f"/playlists/{playlist_id}/tracks", json={"uris": uris[i:i+100]})

    def get_audio_features(self, track_ids):
        features = {}
        # 100'erli sorgulanabilir
        for i in range(0, len(track_ids), 100):
            chunk = track_ids[i:i+100]
            data = self._req("GET", "/audio-features", params={"ids": ",".join(chunk)})
            for feat in data.get("audio_features", []):
                if feat:
                    features[feat["id"]] = feat
        return features

    # --- TOPLU TAKİP / BEĞENİ İŞLEMLERİ ---

    def modify_following(self, action, artist_ids):
        # action: "PUT" (Takip et) veya "DELETE" (Takipten çık)
        # Max 50 per request
        artist_ids = list(set(artist_ids))
        for i in range(0, len(artist_ids), 50):
            chunk = artist_ids[i:i+50]
            self._req(action, "/me/following", params={"type": "artist", "ids": ",".join(chunk)})

    def modify_saved_tracks(self, action, track_ids):
        # action: "PUT" (Beğen) veya "DELETE" (Beğenme)
        # Max 50 per request
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
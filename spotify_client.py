import os
import time
import logging
import requests
import base64

logger = logging.getLogger(__name__)

TOKEN_URL = "https://accounts.spotify.com/api/token"
RECENTLY_PLAYED_URL = "https://api.spotify.com/v1/me/player/recently-played"

class SpotifyClient:
    def __init__(self):
        self.client_id = os.environ["SPOTIFY_CLIENT_ID"]
        self.client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]
        self.refresh_token = os.environ["SPOTIFY_REFRESH_TOKEN"]
        self._access_token = None
        self._token_expires_at = 0

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

    def get_recently_played(self, limit=50):
        token = self._get_access_token()
        resp = requests.get(RECENTLY_PLAYED_URL, headers={
            "Authorization": f"Bearer {token}"
        }, params={"limit": limit})
        resp.raise_for_status()
        items = resp.json().get("items", [])

        tracks = []
        for item in items:
            track = item["track"]
            played_at = item["played_at"]  # ISO 8601 string
            duration_ms = track["duration_ms"]
            tracks.append({
                "played_at": played_at,
                "track_id": track["id"],
                "track_name": track["name"],
                "artist_name": ", ".join(a["name"] for a in track["artists"]),
                "album_name": track["album"]["name"],
                "duration_ms": duration_ms,
                "duration_sec": round(duration_ms / 1000),
            })
        return tracks

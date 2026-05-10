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
        # ÖNEMLİ DEĞİŞİKLİK: Sadece API ayarlarını tutuyoruz.
        # self._access_token veya self._user_id gibi kişiye özel veriler
        # ASLA bu sınıfta tutulmamalı, yoksa kullanıcılar birbirine girer.
        self.client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
        self.client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

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
        
        # Tokenları DİREKT olarak benzersiz oturuma (session) yazıyoruz
        if has_request_context():
            session["access_token"] = data["access_token"]
            session["token_expires_at"] = time.time() + data["expires_in"]
            if data.get("refresh_token"):
                session["refresh_token"] = data["refresh_token"]
            logger.info("✅ YENİ TOKEN ALINDI VE SESSION'A KAYDEDİLDİ.")
            
        return True

    def _get_access_token(self):
        # Tokenlar sadece ve sadece request yapılan güvenli oturumdan çekilir
        if not has_request_context():
            raise Exception("Güvenlik İhlali: Session bağlamı dışında Spotify API isteği yapıldı.")

        access_token = session.get("access_token")
        expires_at = session.get("token_expires_at", 0)
        r_token = session.get("refresh_token")

        # Hala geçerliyse kullan
        if access_token and time.time() < expires_at - 60:
            return access_token

        # Token yoksa kullanıcı çıkış yapmış demektir
        if not r_token:
            raise Exception("Geçerli bir oturum bulunamadı. Lütfen giriş yapın.")

        # Süresi dolmuş, yenileme yap
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = requests.post(TOKEN_URL, headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }, data={
            "grant_type": "refresh_token",
            "refresh_token": r_token
        })
        
        if resp.status_code != 200:
            logger.error(f"Token Yenileme Hatası: {resp.text}")
            if "invalid_grant" in resp.text:
                session.clear()
            resp.raise_for_status()
        
        data = resp.json()
        
        # Yenilenen tokenları da sadece oturuma yaz
        session["access_token"] = data["access_token"]
        session["token_expires_at"] = time.time() + data["expires_in"]
        if data.get("refresh_token"):
            session["refresh_token"] = data["refresh_token"]

        return data["access_token"]

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
        data = self._req("GET", "/search", params={"q": track_name, "type": "track", "limit": 1})
        items = data.get("tracks", {}).get("items", [])
        return items[0]["id"] if items else None

    def shuffle_playlist(self, playlist_id):
        import random
        tracks = self._get_playlist_tracks(playlist_id)
        track_uris = [f"spotify:track:{t['id']}" for t in tracks if t.get("id")]
        random.shuffle(track_uris)
        for i in range(0, len(track_uris), 100):
            self._req("DELETE", f"/playlists/{playlist_id}/items", json={
                "uris": track_uris[i:i+100]
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
        for i in range(0, len(track_ids), 50):
            self._req("PUT", "/me/tracks", params={"ids": ",".join(track_ids[i:i+50])})
        return len(track_ids)

    def unlike_all_tracks_in_playlist(self, playlist_id):
        tracks = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        for i in range(0, len(track_ids), 50):
            self._req("DELETE", "/me/tracks", params={"ids": ",".join(track_ids[i:i+50])})
        return len(track_ids)

    def remove_liked_tracks_from_playlist(self, playlist_id):
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
        tracks = self._get_playlist_tracks(playlist_id)
        track_ids = [t["id"] for t in tracks if t.get("id")]
        liked_ids = self._get_liked_track_ids(track_ids)
        unliked_uris = [f"spotify:track:{tid}" for tid in track_ids if tid not in liked_ids]
        for i in range(0, len(unliked_uris), 100):
            self._req("DELETE", f"/playlists/{playlist_id}/items", json={
                "uris": unliked_uris[i:i+100]
            })
        return len(unliked_uris)
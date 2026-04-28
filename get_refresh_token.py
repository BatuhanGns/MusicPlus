"""
Bu script'i BİR KERE local'de çalıştır → refresh_token'ı al → Render env'e ekle.
Kullanım:
    pip install requests
    python get_refresh_token.py
"""
import os
import requests
import base64
import urllib.parse
import http.server
import threading
import webbrowser

CLIENT_ID = input("Spotify Client ID: ").strip()
CLIENT_SECRET = input("Spotify Client Secret: ").strip()
REDIRECT_URI = "http://localhost:8888/callback"

SCOPES = "user-read-recently-played user-top-read"

auth_url = (
    "https://accounts.spotify.com/authorize"
    f"?client_id={CLIENT_ID}"
    f"&response_type=code"
    f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
    f"&scope={urllib.parse.quote(SCOPES)}"
)

auth_code = None

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Auth tamam! Terminal'e don.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
    def log_message(self, *args):
        pass

server = http.server.HTTPServer(("localhost", 8888), Handler)
t = threading.Thread(target=server.handle_request)
t.start()

print(f"\nTarayici aciliyor... Spotify hesabinla giris yap.\n{auth_url}\n")
webbrowser.open(auth_url)
t.join()

credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
resp = requests.post("https://accounts.spotify.com/api/token", headers={
    "Authorization": f"Basic {credentials}",
    "Content-Type": "application/x-www-form-urlencoded"
}, data={
    "grant_type": "authorization_code",
    "code": auth_code,
    "redirect_uri": REDIRECT_URI
})

data = resp.json()
if "refresh_token" in data:
    print("\n✅ REFRESH TOKEN (Render env'e ekle):")
    print(data["refresh_token"])
else:
    print("❌ Hata:", data)

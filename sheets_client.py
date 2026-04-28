import os
import json
import logging
from collections import defaultdict
import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

SPREADSHEET_ID = "1Jk7kUkSzaFgZJ89wZUQOootRrxnDXg3T0yMwLrCWaVE"

HAM_HEADERS = [
    "played_at", "track_id", "track_name", "artist_name",
    "album_name", "duration_ms", "duration_sec"
]

class SheetsClient:
    def __init__(self):
        creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(SPREADSHEET_ID)
        self._ensure_sheets()

    def _ensure_sheets(self):
        existing = [ws.title for ws in self.sh.worksheets()]

        if "Ham" not in existing:
            ws = self.sh.add_worksheet(title="Ham", rows=10000, cols=10)
            ws.append_row(HAM_HEADERS, value_input_option="RAW")
            logger.info("✅ Ham sayfası oluşturuldu.")

        if "Özet" not in existing:
            ws = self.sh.add_worksheet(title="Özet", rows=1000, cols=5)
            ws.append_row(["Sanatçı / Şarkı", "Dinlenme Sayısı", "Toplam Süre (dk)"], value_input_option="RAW")
            logger.info("✅ Özet sayfası oluşturuldu.")

    def _get_existing_played_ats(self):
        ws = self.sh.worksheet("Ham")
        records = ws.col_values(1)  # played_at sütunu (A)
        return set(records[1:])  # header'ı atla

    def append_ham(self, tracks: list) -> int:
        ws = self.sh.worksheet("Ham")
        existing = self._get_existing_played_ats()

        new_rows = []
        for t in tracks:
            if t["played_at"] not in existing:
                new_rows.append([
                    t["played_at"],
                    t["track_id"],
                    t["track_name"],
                    t["artist_name"],
                    t["album_name"],
                    t["duration_ms"],
                    t["duration_sec"],
                ])

        if new_rows:
            ws.append_rows(new_rows, value_input_option="RAW")

        return len(new_rows)

    def update_ozet(self):
        ham_ws = self.sh.worksheet("Ham")
        records = ham_ws.get_all_records()

        # Şarkı bazında sayaç
        track_stats = defaultdict(lambda: {"count": 0, "duration_sec": 0, "artist": ""})
        for r in records:
            key = r.get("track_name", "").strip()
            if not key:
                continue
            track_stats[key]["count"] += 1
            track_stats[key]["duration_sec"] += int(r.get("duration_sec", 0))
            track_stats[key]["artist"] = r.get("artist_name", "")

        # Sanatçı bazında sayaç
        artist_stats = defaultdict(lambda: {"count": 0, "duration_sec": 0})
        for r in records:
            artist = r.get("artist_name", "").strip()
            if not artist:
                continue
            artist_stats[artist]["count"] += 1
            artist_stats[artist]["duration_sec"] += int(r.get("duration_sec", 0))

        # Özet sayfasını temizle ve yeniden yaz
        ozet_ws = self.sh.worksheet("Özet")
        ozet_ws.clear()

        rows = []
        rows.append(["📊 ÖZET - SANATÇILAR"])
        rows.append(["Sanatçı", "Dinlenme Sayısı", "Toplam Süre (dk)"])

        for artist, stats in sorted(artist_stats.items(), key=lambda x: -x[1]["count"]):
            rows.append([
                artist,
                stats["count"],
                round(stats["duration_sec"] / 60, 1)
            ])

        rows.append([])
        rows.append(["📊 ÖZET - ŞARKILAR"])
        rows.append(["Şarkı", "Sanatçı", "Dinlenme Sayısı", "Toplam Süre (dk)"])

        for track, stats in sorted(track_stats.items(), key=lambda x: -x[1]["count"]):
            rows.append([
                track,
                stats["artist"],
                stats["count"],
                round(stats["duration_sec"] / 60, 1)
            ])

        ozet_ws.update("A1", rows, value_input_option="RAW")
        logger.info(f"📊 Özet güncellendi: {len(artist_stats)} sanatçı, {len(track_stats)} şarkı")

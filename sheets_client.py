import os
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

HAM_HEADERS = [
    "Dinlenme Tarihi", "Şarkı ID", "Şarkı Adı", "Sanatçı",
    "Albüm", "Süre (ms)", "Süre (sn)", "_played_at_iso"
]

TR_AYLAR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan",
    5: "Mayıs", 6: "Haziran", 7: "Temmuz", 8: "Ağustos",
    9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
}

def format_sure(toplam_sn):
    saat = toplam_sn // 3600
    dakika = (toplam_sn % 3600) // 60
    if saat > 0:
        return f"{saat} Saat {dakika} Dakika"
    return f"{dakika} Dakika"

def parse_spotify_date(played_at_str):
    return datetime.strptime(played_at_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)

def format_tarih(played_at_str):
    return parse_spotify_date(played_at_str).strftime("%d.%m.%Y")


class SheetsClient:
    def __init__(self):
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "{}")
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        sid = os.environ.get("GOOGLE_SHEETS_ID", "")
        self.sh = self.gc.open_by_key(sid)
        self._ensure_settings_sheet()

    def _find_sheet(self, name):
        for ws in self.sh.worksheets():
            if ws.title.strip() == name.strip():
                return ws
        return None

    # ─── Settings sayfası ───────────────────────────────────────────────────

    def _ensure_settings_sheet(self):
        ws = self._find_sheet("Settings")
        if not ws:
            ws = self.sh.add_worksheet(title="Settings", rows=100, cols=5)
            ws.append_row(["user_id", "display_name", "stats_permission", "last_sync"], value_input_option="RAW")
            logger.info("✅ Settings sayfası oluşturuldu.")
        return ws

    def get_user_permission(self, user_id: str) -> bool:
        """Kullanıcının istatistikler sayfası izni var mı?"""
        ws = self._find_sheet("Settings")
        if not ws:
            return False
        for row in ws.get_all_values()[1:]:
            if row and row[0] == user_id:
                return len(row) > 2 and row[2].lower() == "true"
        return False

    def set_user_permission(self, user_id: str, display_name: str, allowed: bool):
        """Kullanıcının istatistikler sayfası iznini günceller"""
        ws = self._find_sheet("Settings") or self._ensure_settings_sheet()
        now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
        records = ws.get_all_values()
        for i, row in enumerate(records[1:], start=2):
            if row and row[0] == user_id:
                ws.update(f"A{i}:D{i}", [[user_id, display_name, str(allowed), now_str]])
                return
        ws.append_row([user_id, display_name, str(allowed), now_str], value_input_option="RAW")

    def get_all_permitted_users(self) -> list:
        """İzin vermiş tüm kullanıcı id'lerini döndürür"""
        ws = self._find_sheet("Settings")
        if not ws:
            return []
        return [
            row[0] for row in ws.get_all_values()[1:]
            if row and len(row) > 2 and row[2].lower() == "true"
        ]

    def update_last_sync(self, user_id: str):
        ws = self._find_sheet("Settings")
        if not ws:
            return
        now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
        for i, row in enumerate(ws.get_all_values()[1:], start=2):
            if row and row[0] == user_id:
                ws.update(f"D{i}", [[now_str]])
                return

    # ─── Kullanıcı veri sayfası ─────────────────────────────────────────────

    def _ensure_user_sheet(self, user_id: str):
        ws = self._find_sheet(user_id)
        if not ws:
            ws = self.sh.add_worksheet(title=user_id, rows=50000, cols=10)
            ws.append_row(HAM_HEADERS, value_input_option="RAW")
            logger.info(f"✅ Kullanıcı sayfası oluşturuldu: {user_id}")
        return ws

    def _get_existing_played_ats(self, user_id: str) -> set:
        ws = self._find_sheet(user_id)
        if not ws:
            return set()
        col = ws.col_values(8)  # _played_at_iso sütunu
        return set(col[1:])

    def append_tracks(self, user_id: str, tracks: list) -> int:
        """Kullanıcının kendi sayfasına yeni şarkıları ekler"""
        ws = self._ensure_user_sheet(user_id)
        existing = self._get_existing_played_ats(user_id)
        new_rows = []
        for t in tracks:
            iso = t["played_at"]
            if iso not in existing:
                new_rows.append([
                    format_tarih(iso),
                    t["track_id"],
                    t["track_name"],
                    t["artist_name"],
                    t["album_name"],
                    t["duration_ms"],
                    t["duration_sec"],
                    iso,
                ])
                existing.add(iso)
        if new_rows:
            ws.append_rows(new_rows, value_input_option="RAW")
        return len(new_rows)

    def get_user_data(self, user_id: str):
        """Kullanıcının tüm verisini döndürür → (headers, rows)"""
        ws = self._find_sheet(user_id)
        if not ws:
            return HAM_HEADERS, []
        all_values = ws.get_all_values()
        if len(all_values) < 2:
            return HAM_HEADERS, []
        return all_values[0], all_values[1:]

    def get_combined_data(self, user_ids: list):
        """İzin veren tüm kullanıcıların verisini birleştirerek döndürür"""
        all_rows = []
        for uid in user_ids:
            _, rows = self.get_user_data(uid)
            all_rows.extend(rows)
        return HAM_HEADERS, all_rows

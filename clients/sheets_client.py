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
        sid = os.environ.get("GOOGLE_SHEETS_ID", "")
        if not sid or not creds_json or creds_json == "{}":
            logger.error("❌ GOOGLE_SHEETS_ID veya GOOGLE_CREDENTIALS_JSON eksik!")
            self.gc = None
            self.sh = None
            return
        try:
            creds_dict = json.loads(creds_json)
            creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
            self.gc = gspread.authorize(creds)
            self.sh = self.gc.open_by_key(sid)
            self._ensure_settings_sheet()
        except Exception as e:
            logger.error(f"❌ SheetsClient init hatası: {e}")
            self.gc = None
            self.sh = None

    def _find_sheet(self, name):
        if not self.sh:
            return None
        for ws in self.sh.worksheets():
            if ws.title.strip() == name.strip():
                return ws
        return None

    # ─── Settings sayfası ───────────────────────────────────────────────────

    def _ensure_settings_sheet(self):
        if not self.sh:
            return None
        ws = self._find_sheet("Settings")
        if not ws:
            ws = self.sh.add_worksheet(title="Settings", rows=100, cols=8)
            ws.append_row(["user_id", "display_name", "stats_permission", "last_sync", "refresh_token", "coins", "xp"], value_input_option="RAW")
            logger.info("✅ Settings sayfası oluşturuldu.")
        self._ensure_limits_sheet()
        return ws

    def _ensure_limits_sheet(self):
        """Limits sayfasını oluşturur — yoksa.
        Sütunlar: user_id | display_name | model | today_used | total_used | last_used | reset_date
        """
        if not self.sh:
            return None
        ws = self._find_sheet("Limits")
        if not ws:
            ws = self.sh.add_worksheet(title="Limits", rows=500, cols=7)
            ws.append_row(
                ["user_id", "display_name", "model", "today_used", "total_used", "last_used", "reset_date"],
                value_input_option="RAW"
            )
            logger.info("✅ Limits sayfası oluşturuldu.")
        return ws

    def _ensure_monthly_archive_sheet(self):
        """Aylık_Arsiv sayfasını oluşturur — yoksa.
        Sütunlar: ay | user_id | display_name | model | requests
        """
        if not self.sh:
            return None
        ws = self._find_sheet("Aylık_Arşiv")
        if not ws:
            ws = self.sh.add_worksheet(title="Aylık_Arşiv", rows=2000, cols=5)
            ws.append_row(
                ["ay", "user_id", "display_name", "model", "requests"],
                value_input_option="RAW"
            )
            logger.info("✅ Aylık_Arşiv sayfası oluşturuldu.")
        return ws

    @staticmethod
    def _pretty_model(model: str) -> str:
        """Ham model adını okunabilir hale getirir."""
        m = model.lower()
        if "31b" in m:
            return "Gemma 4 31B"
        if "26b" in m or "27b" in m:
            return "Gemma 4 26B"
        if "gemma" in m:
            return "Gemma 4"
        return model  # bilinmeyen → olduğu gibi

    def log_ai_request(self, user_id: str, display_name: str, model: str):
        """Kullanıcının AI isteğini Limits sayfasına kaydeder.
        - today_used: bugünkü UTC güne ait istek sayısı (sıfırlanabilir)
        - total_used: hiç sıfırlanmayan kümülatif toplam
        """
        try:
            ws = self._find_sheet("Limits") or self._ensure_limits_sheet()
            if not ws:
                return
            pretty  = self._pretty_model(model)
            today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
            records = ws.get_all_values()
            # user_id + pretty_model kombinasyonunu ara
            for i, row in enumerate(records[1:], start=2):
                if len(row) >= 3 and row[0] == user_id and row[2] == pretty:
                    prev_today = int(row[3]) if len(row) > 3 and row[3].isdigit() else 0
                    prev_total = int(row[4]) if len(row) > 4 and row[4].isdigit() else 0
                    reset_date = row[6] if len(row) > 6 else ""
                    # Gün değiştiyse today_used sıfırla
                    if reset_date != today:
                        prev_today = 0
                    ws.update(
                        f"A{i}:G{i}",
                        [[user_id, display_name, pretty, prev_today + 1, prev_total + 1, now_str, today]]
                    )
                    return
            # Yeni satır
            ws.append_row(
                [user_id, display_name, pretty, 1, 1, now_str, today],
                value_input_option="RAW"
            )
        except Exception as e:
            logger.warning(f"⚠️ Limits log hatası: {e}")

    def reset_daily_limits(self):
        """Her gün 00:00 UTC'de today_used sütununu sıfırlar.
        Sıfırlamadan önce geçen günün verisini Aylık_Arşiv'e yazar.
        """
        try:
            ws = self._find_sheet("Limits") or self._ensure_limits_sheet()
            if not ws:
                return
            archive_ws = self._find_sheet("Aylık_Arşiv") or self._ensure_monthly_archive_sheet()
            records    = ws.get_all_values()
            if len(records) < 2:
                return

            yesterday  = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0) 
                          - __import__('datetime').timedelta(days=1))
            ay_label   = yesterday.strftime("%Y-%m")  # örn. "2026-05"
            now_str    = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
            today      = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            archive_rows = []
            updates      = []

            for i, row in enumerate(records[1:], start=2):
                if not row or not row[0]:
                    continue
                uid  = row[0]
                dn   = row[1] if len(row) > 1 else uid
                mdl  = row[2] if len(row) > 2 else ""
                td   = int(row[3]) if len(row) > 3 and row[3].isdigit() else 0
                tot  = int(row[4]) if len(row) > 4 and row[4].isdigit() else 0

                if td > 0:
                    archive_rows.append([ay_label, uid, dn, mdl, td])

                updates.append({
                    "range": f"D{i}:G{i}",
                    "values": [[0, tot, now_str, today]]
                })

            # Arşive yaz
            if archive_rows and archive_ws:
                archive_ws.append_rows(archive_rows, value_input_option="RAW")
                logger.info(f"📦 Aylık arşive {len(archive_rows)} satır yazıldı ({ay_label})")

            # Limits sayfasında today_used sıfırla
            if updates:
                ws.batch_update(updates)
                logger.info(f"🔄 Günlük limit sıfırlandı ({len(updates)} satır)")

        except Exception as e:
            logger.warning(f"⚠️ Günlük sıfırlama hatası: {e}")

    def get_limits_summary(self) -> list:
        """Limits sayfasındaki güncel kayıtları döndürür."""
        try:
            ws = self._find_sheet("Limits")
            if not ws:
                return []
            rows = ws.get_all_values()
            if len(rows) < 2:
                return []
            headers = rows[0]
            return [dict(zip(headers, row)) for row in rows[1:] if row and row[0]]
        except Exception as e:
            logger.warning(f"⚠️ Limits okuma hatası: {e}")
            return []

    def get_total_used_from_sheets(self) -> int:
        """Sheets'teki gerçek toplam kullanımı döndürür.
        Hem yeni format (total_used) hem eski format (requests_used) desteklenir."""
        try:
            summary = self.get_limits_summary()
            if not summary:
                return 0
            total = 0
            for r in summary:
                # Yeni format
                v = r.get("total_used", "")
                if str(v).isdigit():
                    total += int(v)
                    continue
                # Eski format fallback
                v2 = r.get("requests_used", "")
                if str(v2).isdigit():
                    total += int(v2)
            return total
        except Exception:
            return 0

    def get_user_permission(self, user_id: str) -> bool:
        """Kullanıcının istatistikler sayfası izni var mı?"""
        ws = self._find_sheet("Settings")
        if not ws:
            return False
        for row in ws.get_all_values()[1:]:
            if row and row[0] == user_id:
                return len(row) > 2 and row[2].lower() == "true"
        return False

    def set_user_permission(self, user_id: str, display_name: str, allowed: bool, refresh_token: str = ""):
        """Kullanıcının istatistikler sayfası iznini günceller"""
        ws = self._find_sheet("Settings") or self._ensure_settings_sheet()
        now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
        records = ws.get_all_values()
        for i, row in enumerate(records[1:], start=2):
            if row and row[0] == user_id:
                existing_token = row[4] if len(row) > 4 else ""
                token_to_save  = refresh_token or existing_token
                ws.update(f"A{i}:E{i}", [[user_id, display_name, str(allowed), now_str, token_to_save]])
                return
        ws.append_row([user_id, display_name, str(allowed), now_str, refresh_token], value_input_option="RAW")

    def save_refresh_token(self, user_id: str, refresh_token: str):
        """Kullanıcının refresh token'ını günceller"""
        ws = self._find_sheet("Settings")
        if not ws:
            return
        records = ws.get_all_values()
        for i, row in enumerate(records[1:], start=2):
            if row and row[0] == user_id:
                ws.update(f"E{i}", [[refresh_token]])
                return

    def get_all_users_with_tokens(self) -> list:
        """Tüm kullanıcıları token'larıyla döndürür — scheduled sync için"""
        ws = self._find_sheet("Settings")
        if not ws:
            return []
        users = []
        for row in ws.get_all_values()[1:]:
            if row and row[0]:
                users.append({
                    "user_id": row[0],
                    "refresh_token": row[4] if len(row) > 4 else ""
                })
        return users

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

    def save_gamification_cache(self, user_id: str, coins: int, xp: int):
        """Hesaplanan coin ve XP degerlerini Settings sayfasina yazar (F ve G sutunlari)."""
        ws = self._find_sheet("Settings") or self._ensure_settings_sheet()
        if not ws:
            return
        try:
            records = ws.get_all_values()
            for i, row in enumerate(records[1:], start=2):
                if row and row[0] == user_id:
                    ws.update(f"F{i}:G{i}", [[coins, xp]])
                    return
            ws.append_row([user_id, "", "False", "", "", coins, xp], value_input_option="RAW")
        except Exception as e:
            logger.warning(f"Gamification cache kaydetme hatasi: {e}")

    def get_gamification_cache(self, user_id: str) -> dict:
        """Settings sayfasindan onceden hesaplanmis coin ve XP degerlerini okur."""
        ws = self._find_sheet("Settings")
        if not ws:
            return {}
        try:
            for row in ws.get_all_values()[1:]:
                if row and row[0] == user_id:
                    coins_val = row[5] if len(row) > 5 else ""
                    xp_val    = row[6] if len(row) > 6 else ""
                    if coins_val and xp_val and str(coins_val).lstrip("-").isdigit() and str(xp_val).lstrip("-").isdigit():
                        return {"coins": int(coins_val), "xp": int(xp_val), "cached": True}
        except Exception as e:
            logger.warning(f"Gamification cache okuma hatasi: {e}")
        return {}

    # ─── Kullanıcı veri sayfası ─────────────────────────────────────────────

    def _ensure_user_sheet(self, user_id: str):
        if not self.sh:
            return None
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

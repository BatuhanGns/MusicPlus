import os
import json
import logging
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials

import config  # HAM_HEADERS tek kaynaktan okunur

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Sütun indeksleri — config.HAM_HEADERS ile eşleşmeli
# ["Dinlenme Tarihi", "Şarkı ID", "Şarkı Adı", "Sanatçı",
#  "Sanatçı ID", "Albüm", "Süre (ms)", "_played_at_iso"]
COL_TARIH      = 0
COL_SARKI_ID   = 1
COL_SARKI_ADI  = 2
COL_SANATCI    = 3
COL_SANATCI_ID = 4
COL_ALBUM      = 5
COL_SURE_MS    = 6
COL_ISO        = 7

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
            ws = self.sh.add_worksheet(title="Settings", rows=100, cols=16)
            ws.append_row([
                "user_id", "display_name", "stats_permission", "last_sync",
                "refresh_token", "coins", "xp", "access_token", "expires_at",
                "email", "spotify_odeme_gunu", "streak_bildirimi",
                "ozet_bildirimi", "ozet_sikligi", "son_streak_bildirimi", "son_ozet_bildirimi"
            ], value_input_option="RAW")
            logger.info("✅ Settings sayfası oluşturuldu.")
        self._ensure_limits_sheet()
        return ws

    def _ensure_limits_sheet(self):
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
        m = model.lower()
        if "31b" in m:
            return "Gemma 4 31B"
        if "26b" in m or "27b" in m:
            return "Gemma 4 26B"
        if "gemma" in m:
            return "Gemma 4"
        return model

    def log_ai_request(self, user_id: str, display_name: str, model: str):
        try:
            ws = self._find_sheet("Limits") or self._ensure_limits_sheet()
            if not ws:
                return
            pretty  = self._pretty_model(model)
            today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
            records = ws.get_all_values()
            for i, row in enumerate(records[1:], start=2):
                if len(row) >= 3 and row[0] == user_id and row[2] == pretty:
                    prev_today = int(row[3]) if len(row) > 3 and row[3].isdigit() else 0
                    prev_total = int(row[4]) if len(row) > 4 and row[4].isdigit() else 0
                    reset_date = row[6] if len(row) > 6 else ""
                    if reset_date != today:
                        prev_today = 0
                    ws.update(
                        f"A{i}:G{i}",
                        [[user_id, display_name, pretty, prev_today + 1, prev_total + 1, now_str, today]]
                    )
                    return
            ws.append_row(
                [user_id, display_name, pretty, 1, 1, now_str, today],
                value_input_option="RAW"
            )
        except Exception as e:
            logger.warning(f"⚠️ Limits log hatası: {e}")

    def reset_daily_limits(self):
        try:
            ws = self._find_sheet("Limits") or self._ensure_limits_sheet()
            if not ws:
                return
            archive_ws = self._find_sheet("Aylık_Arşiv") or self._ensure_monthly_archive_sheet()
            records    = ws.get_all_values()
            if len(records) < 2:
                return

            # timedelta doğrudan import'tan kullanılır — __import__ antipattern kaldırıldı
            yesterday  = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=1)
            ay_label   = yesterday.strftime("%Y-%m")
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

            if archive_rows and archive_ws:
                archive_ws.append_rows(archive_rows, value_input_option="RAW")
                logger.info(f"📦 Aylık arşive {len(archive_rows)} satır yazıldı ({ay_label})")

            if updates:
                ws.batch_update(updates)
                logger.info(f"🔄 Günlük limit sıfırlandı ({len(updates)} satır)")

        except Exception as e:
            logger.warning(f"⚠️ Günlük sıfırlama hatası: {e}")

    def get_limits_summary(self) -> list:
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
        try:
            summary = self.get_limits_summary()
            if not summary:
                return 0
            total = 0
            for r in summary:
                v = r.get("total_used", "")
                if str(v).isdigit():
                    total += int(v)
                    continue
                v2 = r.get("requests_used", "")
                if str(v2).isdigit():
                    total += int(v2)
            return total
        except Exception:
            return 0

    def get_user_permission(self, user_id: str) -> bool:
        ws = self._find_sheet("Settings")
        if not ws:
            return False
        for row in ws.get_all_values()[1:]:
            if row and row[0] == user_id:
                return len(row) > 2 and row[2].lower() == "true"
        return False

    def set_user_permission(self, user_id: str, display_name: str, allowed: bool, refresh_token: str = ""):
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
        ws = self._find_sheet("Settings")
        if not ws:
            return
        records = ws.get_all_values()
        for i, row in enumerate(records[1:], start=2):
            if row and row[0] == user_id:
                ws.update(f"E{i}", [[refresh_token]])
                return

    def save_access_token(self, user_id: str, access_token: str, expires_at: float):
        ws = self._find_sheet("Settings")
        if not ws:
            return
        records = ws.get_all_values()
        for i, row in enumerate(records[1:], start=2):
            if row and row[0] == user_id:
                ws.update(f"H{i}:I{i}", [[access_token, str(expires_at)]])
                return

    def get_access_token(self, user_id: str) -> dict:
        ws = self._find_sheet("Settings")
        if not ws:
            return {}
        try:
            for row in ws.get_all_values()[1:]:
                if row and row[0] == user_id:
                    token   = row[7] if len(row) > 7 else ""
                    exp_str = row[8] if len(row) > 8 else ""
                    if token and exp_str:
                        try:
                            return {"access_token": token, "expires_at": float(exp_str)}
                        except ValueError:
                            return {}
        except Exception as e:
            logger.warning(f"get_access_token hatası ({user_id}): {e}")
        return {}

    def get_all_users_with_tokens(self) -> list:
        ws = self._find_sheet("Settings")
        if not ws:
            return []
        users = []
        for row in ws.get_all_values()[1:]:
            if row and row[0]:
                exp_str = row[8] if len(row) > 8 else ""
                try:
                    expires_at = float(exp_str) if exp_str else 0.0
                except ValueError:
                    expires_at = 0.0
                users.append({
                    "user_id":       row[0],
                    "refresh_token": row[4] if len(row) > 4 else "",
                    "access_token":  row[7] if len(row) > 7 else "",
                    "expires_at":    expires_at,
                })
        return users

    def get_all_permitted_users(self) -> list:
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

    # ─── Bildirim Ayarları ────────────────────────────────────────────────────
    # Settings sütunları:
    #   A=user_id, B=display_name, C=stats_permission, D=last_sync,
    #   E=refresh_token, F=coins, G=xp, H=access_token, I=expires_at,
    #   J=email, K=spotify_odeme_gunu, L=streak_bildirimi,
    #   M=ozet_bildirimi, N=ozet_sikligi,
    #   O=son_streak_bildirimi, P=son_ozet_bildirimi

    def get_notification_users(self) -> list:
        ws = self._find_sheet("Settings")
        if not ws:
            return []
        try:
            result = []
            for row in ws.get_all_values()[1:]:
                if not row or not row[0]:
                    continue
                def _get(idx, default=""):
                    return row[idx] if len(row) > idx else default
                result.append({
                    "user_id":              _get(0),
                    "display_name":         _get(1),
                    "email":                _get(9),
                    "spotify_odeme_gunu":   _get(10),
                    "streak_bildirimi":     _get(11),
                    "ozet_bildirimi":       _get(12),
                    "ozet_sikligi":         _get(13, "weekly"),
                    "son_streak_bildirimi": _get(14),
                    "son_ozet_bildirimi":   _get(15),
                })
            return result
        except Exception as e:
            logger.error(f"get_notification_users hatası: {e}")
            return []

    def get_notification_settings(self, user_id: str) -> dict:
        ws = self._find_sheet("Settings")
        if not ws:
            return {}
        try:
            for row in ws.get_all_values()[1:]:
                if row and row[0] == user_id:
                    def _get(idx, default=""):
                        return row[idx] if len(row) > idx else default
                    return {
                        "email":              _get(9),
                        "spotify_odeme_gunu": _get(10),
                        "streak_bildirimi":   _get(11, "false"),
                        "ozet_bildirimi":     _get(12, "false"),
                        "ozet_sikligi":       _get(13, "weekly"),
                    }
        except Exception as e:
            logger.error(f"get_notification_settings hatası ({user_id}): {e}")
        return {}

    def save_notification_settings(self, user_id: str, settings: dict):
        ws = self._find_sheet("Settings") or self._ensure_settings_sheet()
        if not ws:
            return
        try:
            records    = ws.get_all_values()
            email      = settings.get("email", "")
            odeme_gunu = settings.get("spotify_odeme_gunu", "")
            streak_bil = str(settings.get("streak_bildirimi", False)).capitalize()
            ozet_bil   = str(settings.get("ozet_bildirimi", False)).capitalize()
            ozet_sik   = settings.get("ozet_sikligi", "weekly")
            for i, row in enumerate(records[1:], start=2):
                if row and row[0] == user_id:
                    ws.update(
                        f"J{i}:N{i}",
                        [[email, odeme_gunu, streak_bil, ozet_bil, ozet_sik]],
                        value_input_option="RAW"
                    )
                    return
            logger.warning(f"save_notification_settings: kullanıcı bulunamadı ({user_id})")
        except Exception as e:
            logger.error(f"save_notification_settings hatası ({user_id}): {e}")

    def set_notification_field(self, user_id: str, field: str, value: str):
        col_map = {
            "son_streak_bildirimi": "O",
            "son_ozet_bildirimi":   "P",
        }
        col = col_map.get(field)
        if not col:
            return
        ws = self._find_sheet("Settings")
        if not ws:
            return
        try:
            for i, row in enumerate(ws.get_all_values()[1:], start=2):
                if row and row[0] == user_id:
                    ws.update(f"{col}{i}", [[value]])
                    return
        except Exception as e:
            logger.error(f"set_notification_field hatası ({user_id}, {field}): {e}")

    # ─── Kullanıcı veri sayfası ──────────────────────────────────────────────

    def _ensure_user_sheet(self, user_id: str):
        if not self.sh:
            return None
        ws = self._find_sheet(user_id)
        if not ws:
            ws = self.sh.add_worksheet(title=user_id, rows=50000, cols=9)
            ws.append_row(config.HAM_HEADERS, value_input_option="RAW")
            logger.info(f"✅ Kullanıcı sayfası oluşturuldu: {user_id}")
        return ws

    def _get_existing_played_ats(self, user_id: str) -> set:
        ws = self._find_sheet(user_id)
        if not ws:
            return set()
        # COL_ISO = 7 → 8. sütun (1-indexed)
        col = ws.col_values(COL_ISO + 1)
        return set(col[1:])

    def append_tracks(self, user_id: str, tracks: list):
        ws = self._ensure_user_sheet(user_id)
        existing   = self._get_existing_played_ats(user_id)
        new_rows   = []
        new_tracks = []
        for t in tracks:
            iso = t["played_at"]
            if iso not in existing:
                new_rows.append([
                    format_tarih(iso),          # Dinlenme Tarihi
                    t["track_id"],              # Şarkı ID
                    t["track_name"],            # Şarkı Adı
                    t["artist_name"],           # Sanatçı
                    t.get("artist_ids", ""),    # Sanatçı ID
                    t["album_name"],            # Albüm
                    t["duration_ms"],           # Süre (ms)
                    iso,                        # _played_at_iso
                ])
                new_tracks.append({
                    "track_name":   t["track_name"],
                    "artist_name":  t["artist_name"],
                    "album_name":   t["album_name"],
                    "duration_sec": t["duration_sec"],
                })
                existing.add(iso)
        if new_rows:
            ws.append_rows(new_rows, value_input_option="RAW")
        return len(new_rows), new_tracks

    def get_last_played_at_ms(self, user_id: str):
        ws = self._find_sheet(user_id)
        if not ws:
            return None
        try:
            col = ws.col_values(COL_ISO + 1)
            values = [v.strip() for v in col[1:] if v.strip() and v.strip() != "—"]
            if not values:
                return None
            latest_iso = max(values)
            dt = datetime.strptime(latest_iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000) + 1
        except Exception as e:
            logger.warning(f"get_last_played_at_ms hatası ({user_id}): {e}")
            return None

    def get_user_data(self, user_id: str):
        ws = self._find_sheet(user_id)
        if not ws:
            return config.HAM_HEADERS, []
        all_values = ws.get_all_values()
        if len(all_values) < 2:
            return config.HAM_HEADERS, []
        return all_values[0], all_values[1:]

    def get_combined_data(self, user_ids: list):
        all_rows = []
        for uid in user_ids:
            _, rows = self.get_user_data(uid)
            all_rows.extend(rows)
        return config.HAM_HEADERS, all_rows

    def migrate_user_sheet(self, user_id: str) -> dict:
        """
        Eski formattaki (Tür sütunlu) kullanıcı sayfasını 8 sütunlu yeni formata taşır.
        Tür sütunu artık kullanılmıyor.
        """
        ws = self._find_sheet(user_id)
        if not ws:
            return {"error": "Sayfa bulunamadı"}
        try:
            all_values = ws.get_all_values()
        except Exception as e:
            return {"error": str(e)}
        if not all_values:
            return {"migrated": 0, "skipped": 0, "already_ok": True}

        headers = all_values[0]

        # Zaten 8 sütunlu yeni formattaysa atla
        if headers == list(config.HAM_HEADERS):
            return {"migrated": 0, "skipped": 0, "already_ok": True}

        rows = all_values[1:]
        if not rows:
            ws.update("A1:H1", [list(config.HAM_HEADERS)], value_input_option="RAW")
            return {"migrated": 0, "skipped": 0, "already_ok": False}

        def idx(col_name, fallback=-1):
            try:
                return headers.index(col_name)
            except ValueError:
                return fallback

        old_tarih      = idx("Dinlenme Tarihi", 0)
        old_sarki_id   = idx("Şarkı ID", 1)
        old_sarki_adi  = idx("Şarkı Adı", 2)
        old_sanatci    = idx("Sanatçı", 3)
        old_sanatci_id = idx("Sanatçı ID", -1)
        old_album      = idx("Albüm", -1)
        old_sure_ms    = idx("Süre (ms)", -1)
        old_iso        = idx("_played_at_iso", -1)

        new_rows = [list(config.HAM_HEADERS)]
        migrated = 0

        for row in rows:
            def get(i, default=""):
                return (row[i] or "").strip() if 0 <= i < len(row) else default

            new_row = [
                get(old_tarih),
                get(old_sarki_id),
                get(old_sarki_adi),
                get(old_sanatci),
                get(old_sanatci_id),
                get(old_album),
                get(old_sure_ms),
                get(old_iso),
            ]
            new_rows.append(new_row)
            migrated += 1

        try:
            ws.clear()
            ws.update(f"A1:H{len(new_rows)}", new_rows, value_input_option="RAW")
            logger.info(f"✅ Migration tamamlandı ({user_id}): {migrated} satır")
            return {"migrated": migrated, "skipped": 0, "already_ok": False}
        except Exception as e:
            logger.error(f"❌ Migration yazma hatası ({user_id}): {e}")
            return {"error": str(e)}

    def get_artist_ids_from_user_sheet(self, user_id: str) -> dict:
        ws = self._find_sheet(user_id)
        if not ws:
            return {}
        all_values = ws.get_all_values()
        if len(all_values) < 2:
            return {}
        headers = all_values[0]
        try:
            idx_sanatci = headers.index("Sanatçı")
            idx_id      = headers.index("Sanatçı ID")
        except ValueError:
            return {}
        result = {}
        for row in all_values[1:]:
            if len(row) <= max(idx_sanatci, idx_id):
                continue
            name = (row[idx_sanatci] or "").strip()
            aid  = (row[idx_id] or "").strip()
            if name and name not in result:
                result[name] = aid
        return result

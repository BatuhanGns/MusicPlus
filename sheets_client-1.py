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
    "Dinlenme Zamanı", "Şarkı ID", "Şarkı Adı", "Sanatçı",
    "Albüm", "Süre (ms)", "Süre (sn)"
]

class SheetsClient:
    def __init__(self):
        creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(SPREADSHEET_ID)
        self._ensure_sheets()

    def _find_sheet(self, name):
        """Sayfa adını büyük/küçük harf ve boşluk farkı gözetmeden bul"""
        for ws in self.sh.worksheets():
            if ws.title.strip().lower() == name.strip().lower():
                return ws
        return None

    def _ensure_sheets(self):
        ham_ws = self._find_sheet("Ham")
        if not ham_ws:
            ham_ws = self.sh.add_worksheet(title="Ham", rows=10000, cols=10)
            ham_ws.append_row(HAM_HEADERS, value_input_option="RAW")
            logger.info("✅ Ham sayfası oluşturuldu.")
        else:
            # Başlık yoksa veya yanlışsa güncelle
            first_row = ham_ws.row_values(1)
            if first_row != HAM_HEADERS:
                ham_ws.insert_row(HAM_HEADERS, 1, value_input_option="RAW")
                logger.info("✅ Ham sayfası başlıkları güncellendi.")

        ozet_ws = self._find_sheet("Özet")
        if not ozet_ws:
            self.sh.add_worksheet(title="Özet", rows=1000, cols=5)
            logger.info("✅ Özet sayfası oluşturuldu.")

    def _get_existing_played_ats(self):
        ws = self._find_sheet("Ham")
        records = ws.col_values(1)  # A sütunu = Dinlenme Zamanı
        return set(records[1:])     # başlığı atla

    def append_ham(self, tracks: list) -> int:
        ws = self._find_sheet("Ham")
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
        ham_ws = self._find_sheet("Ham")
        all_values = ham_ws.get_all_values()

        if len(all_values) < 2:
            logger.info("Ham sayfasında yeterli veri yok.")
            return

        # Sütun indekslerini başlıktan bul
        headers = all_values[0]
        try:
            idx_sarki   = headers.index("Şarkı Adı")
            idx_sanatci = headers.index("Sanatçı")
            idx_sure    = headers.index("Süre (sn)")
        except ValueError as e:
            logger.error(f"❌ Başlık bulunamadı: {e}")
            return

        # Şarkı bazında sayaç
        track_stats = defaultdict(lambda: {"count": 0, "duration_sec": 0, "artist": ""})
        # Sanatçı bazında sayaç
        artist_stats = defaultdict(lambda: {"count": 0, "duration_sec": 0})

        for row in all_values[1:]:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_sure):
                continue
            sarki   = row[idx_sarki].strip()
            sanatci = row[idx_sanatci].strip()
            try:
                sure = int(row[idx_sure])
            except (ValueError, IndexError):
                sure = 0

            if sarki:
                track_stats[sarki]["count"] += 1
                track_stats[sarki]["duration_sec"] += sure
                track_stats[sarki]["artist"] = sanatci

            if sanatci:
                artist_stats[sanatci]["count"] += 1
                artist_stats[sanatci]["duration_sec"] += sure

        # Özet sayfasını temizle ve yeniden yaz
        ozet_ws = self._find_sheet("Özet")
        ozet_ws.clear()

        rows = []
        rows.append(["📊 SANATÇI ÖZET"])
        rows.append(["Sanatçı", "Dinlenme Sayısı", "Toplam Süre (dk)"])

        for artist, stats in sorted(artist_stats.items(), key=lambda x: -x[1]["count"]):
            rows.append([
                artist,
                stats["count"],
                round(stats["duration_sec"] / 60, 1)
            ])

        rows.append([])
        rows.append(["📊 ŞARKI ÖZET"])
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

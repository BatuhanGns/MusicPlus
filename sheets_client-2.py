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

SPREADSHEET_ID = "1Jk7kUkSzaFgZJ89wZUQOootRrxnDXg3T0yMwLrCWaVE"

HAM_HEADERS = [
    "Dinlenme Tarihi", "Şarkı ID", "Şarkı Adı", "Sanatçı",
    "Albüm", "Süre (ms)", "Süre (sn)"
]

TR_GUNLER = {
    0: "Pazartesi", 1: "Salı", 2: "Çarşamba", 3: "Perşembe",
    4: "Cuma", 5: "Cumartesi", 6: "Pazar"
}

def format_sure(toplam_sn):
    """Saniyeyi 'X Saat Y Dakika' formatına çevir"""
    saat = toplam_sn // 3600
    dakika = (toplam_sn % 3600) // 60
    if saat > 0:
        return f"{saat} Saat {dakika} Dakika"
    return f"{dakika} Dakika"

def parse_spotify_date(played_at_str):
    """ISO 8601 Spotify tarihini datetime objesine çevir"""
    return datetime.strptime(played_at_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)

def format_tarih(played_at_str):
    """ISO 8601 → DD.MM.YYYY"""
    dt = parse_spotify_date(played_at_str)
    return dt.strftime("%d.%m.%Y")

def hafta_no(dt):
    """Tarihten (yıl, hafta_numarası) tuple döndür"""
    return dt.isocalendar()[:2]  # (yıl, hafta)


class SheetsClient:
    def __init__(self):
        creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(SPREADSHEET_ID)
        self._ensure_sheets()

    def _find_sheet(self, name):
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
            first_row = ham_ws.row_values(1)
            if first_row != HAM_HEADERS:
                ham_ws.insert_row(HAM_HEADERS, 1, value_input_option="RAW")
                logger.info("✅ Ham başlıkları güncellendi.")

        if not self._find_sheet("Özet"):
            self.sh.add_worksheet(title="Özet", rows=1000, cols=5)
            logger.info("✅ Özet sayfası oluşturuldu.")

        if not self._find_sheet("Analiz"):
            self.sh.add_worksheet(title="Analiz", rows=2000, cols=5)
            logger.info("✅ Analiz sayfası oluşturuldu.")

    def _get_existing_played_ats(self):
        ws = self._find_sheet("Ham")
        records = ws.col_values(1)
        return set(records[1:])  # başlığı atla

    def append_ham(self, tracks: list) -> int:
        ws = self._find_sheet("Ham")
        existing = self._get_existing_played_ats()

        new_rows = []
        for t in tracks:
            if t["played_at"] not in existing:
                new_rows.append([
                    format_tarih(t["played_at"]),   # A: DD.MM.YYYY
                    t["track_id"],                   # B
                    t["track_name"],                 # C
                    t["artist_name"],                # D
                    t["album_name"],                 # E
                    t["duration_ms"],                # F
                    t["duration_sec"],               # G
                ])
                existing.add(t["played_at"])  # duplicate önleme

        if new_rows:
            ws.append_rows(new_rows, value_input_option="RAW")

        return len(new_rows)

    def update_ozet(self):
        ham_ws = self._find_sheet("Ham")
        all_values = ham_ws.get_all_values()

        if len(all_values) < 2:
            logger.info("Ham sayfasında yeterli veri yok.")
            return

        headers = all_values[0]
        try:
            idx_sarki   = headers.index("Şarkı Adı")
            idx_sanatci = headers.index("Sanatçı")
            idx_sure    = headers.index("Süre (sn)")
        except ValueError as e:
            logger.error(f"❌ Başlık bulunamadı: {e}")
            return

        track_stats  = defaultdict(lambda: {"count": 0, "duration_sec": 0, "artist": ""})
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

        ozet_ws = self._find_sheet("Özet")
        ozet_ws.clear()

        rows = []
        rows.append(["📊 SANATÇI ÖZET"])
        rows.append(["Sanatçı", "Dinlenme Sayısı", "Toplam Süre (dk)"])
        for artist, stats in sorted(artist_stats.items(), key=lambda x: -x[1]["count"]):
            rows.append([artist, stats["count"], round(stats["duration_sec"] / 60, 1)])

        rows.append([])
        rows.append(["📊 ŞARKI ÖZET"])
        rows.append(["Şarkı", "Sanatçı", "Dinlenme Sayısı", "Toplam Süre (dk)"])
        for track, stats in sorted(track_stats.items(), key=lambda x: -x[1]["count"]):
            rows.append([track, stats["artist"], stats["count"], round(stats["duration_sec"] / 60, 1)])

        ozet_ws.update("A1", rows, value_input_option="RAW")
        logger.info(f"📊 Özet güncellendi: {len(artist_stats)} sanatçı, {len(track_stats)} şarkı")

    def update_analiz(self):
        """
        Son 7 günü hesapla, her gün için:
          A=Tarih (DD.MM.YYYY), B=Gün adı, C=Dinleme Süresi (X Saat Y Dakika)
        Haftalar arası 1 satır boşluk bırak.
        """
        ham_ws = self._find_sheet("Ham")
        all_values = ham_ws.get_all_values()

        if len(all_values) < 2:
            logger.info("Ham sayfasında yeterli veri yok.")
            return

        headers = all_values[0]
        try:
            idx_tarih = headers.index("Dinlenme Tarihi")
            idx_sure  = headers.index("Süre (sn)")
        except ValueError as e:
            logger.error(f"❌ Başlık bulunamadı: {e}")
            return

        # Gün bazında toplam süre: {"29.04.2025": 3600, ...}
        gun_sureler = defaultdict(int)
        for row in all_values[1:]:
            if len(row) <= max(idx_tarih, idx_sure):
                continue
            tarih = row[idx_tarih].strip()
            try:
                sure = int(row[idx_sure])
            except (ValueError, IndexError):
                sure = 0
            if tarih:
                gun_sureler[tarih] += sure

        # Son 7 günü bugünden geriye doğru üret
        bugun = datetime.now(timezone.utc).date()
        son_7_gun = [(bugun - timedelta(days=i)) for i in range(6, -1, -1)]

        # Hafta numarasına göre grupla
        haftalar = defaultdict(list)
        for gun in son_7_gun:
            hno = gun.isocalendar()[:2]
            haftalar[hno].append(gun)

        analiz_ws = self._find_sheet("Analiz")
        analiz_ws.clear()

        # Başlık
        rows = [["Tarih", "Gün", "Dinleme Süresi"]]

        for i, (hno, gunler) in enumerate(sorted(haftalar.items())):
            if i > 0:
                rows.append(["", "", ""])  # haftalar arası boşluk

            for gun in sorted(gunler):
                tarih_str = gun.strftime("%d.%m.%Y")
                gun_adi   = TR_GUNLER[gun.weekday()]
                toplam_sn = gun_sureler.get(tarih_str, 0)
                sure_str  = format_sure(toplam_sn) if toplam_sn > 0 else "—"
                rows.append([tarih_str, gun_adi, sure_str])

        analiz_ws.update("A1", rows, value_input_option="RAW")
        logger.info(f"📅 Analiz güncellendi: {len(son_7_gun)} gün")

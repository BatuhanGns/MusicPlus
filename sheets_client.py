import os
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import calendar
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
    "Albüm", "Süre (ms)", "Süre (sn)", "_played_at_iso"
]

TR_GUNLER = {
    0: "Pazartesi", 1: "Salı", 2: "Çarşamba", 3: "Perşembe",
    4: "Cuma", 5: "Cumartesi", 6: "Pazar"
}

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
        # Ham sayfası (1000 limitli)
        ham_ws = self._find_sheet("Ham")
        if not ham_ws:
            ham_ws = self.sh.add_worksheet(title="Ham", rows=1100, cols=10)
            ham_ws.append_row(HAM_HEADERS, value_input_option="RAW")
            logger.info("✅ Ham sayfası oluşturuldu.")
        else:
            first_row = ham_ws.row_values(1)
            if first_row != HAM_HEADERS:
                ham_ws.update("A1:H1", [HAM_HEADERS], value_input_option="RAW")
                logger.info("✅ Ham başlıkları güncellendi.")

        # TümVeri sayfası (hiç silinmez, istatistik için)
        tumveri_ws = self._find_sheet("TümVeri")
        if not tumveri_ws:
            tumveri_ws = self.sh.add_worksheet(title="TümVeri", rows=50000, cols=10)
            tumveri_ws.append_row(HAM_HEADERS, value_input_option="RAW")
            logger.info("✅ TümVeri sayfası oluşturuldu.")

        if not self._find_sheet("Özet"):
            self.sh.add_worksheet(title="Özet", rows=2000, cols=5)
            logger.info("✅ Özet sayfası oluşturuldu.")

        if not self._find_sheet("Analiz"):
            self.sh.add_worksheet(title="Analiz", rows=2000, cols=10)
            logger.info("✅ Analiz sayfası oluşturuldu.")

    def _get_existing_played_ats(self):
        """TümVeri'deki ISO timestamp'leri oku — duplicate kontrolü için"""
        ws = self._find_sheet("TümVeri")
        col_values = ws.col_values(8)  # H sütunu
        return set(col_values[1:])

    def append_ham(self, tracks: list) -> int:
        ham_ws    = self._find_sheet("Ham")
        tumveri_ws = self._find_sheet("TümVeri")
        existing  = self._get_existing_played_ats()

        new_rows = []
        for t in tracks:
            iso = t["played_at"]
            if iso not in existing:
                row = [
                    format_tarih(iso),
                    t["track_id"],
                    t["track_name"],
                    t["artist_name"],
                    t["album_name"],
                    t["duration_ms"],
                    t["duration_sec"],
                    iso,
                ]
                new_rows.append(row)
                existing.add(iso)

        if new_rows:
            # TümVeri'ye alta ekle (kronolojik)
            tumveri_ws.append_rows(new_rows, value_input_option="RAW")
            # Ham'a en üste ekle (en yeni üstte)
            ham_ws.insert_rows(list(reversed(new_rows)), row=2, value_input_option="RAW")
            self._trim_ham_to_1000(ham_ws)

        return len(new_rows)

    def _trim_ham_to_1000(self, ws):
        """Ham sayfasını 1000 kayıtta tut, en eskiler altta oldugu icin alttan sil"""
        all_rows = ws.get_all_values()
        data_rows = len(all_rows) - 1
        if data_rows > 1000:
            fazla = data_rows - 1000
            son_satir = len(all_rows)
            ws.delete_rows(son_satir - fazla + 1, son_satir)
            logger.info(f"🗑️ {fazla} eski kayit Ham'dan silindi.")

    def _get_tumveri_data(self):
        """TümVeri sayfasından tüm veriyi okur"""
        ws = self._find_sheet("TümVeri")
        all_values = ws.get_all_values()
        if len(all_values) < 2:
            return None, None
        return all_values[0], all_values[1:]

    def update_ozet(self):
        headers, rows = self._get_tumveri_data()
        if not headers:
            logger.info("TümVeri sayfasında yeterli veri yok.")
            return

        try:
            idx_sarki   = headers.index("Şarkı Adı")
            idx_sanatci = headers.index("Sanatçı")
            idx_album   = headers.index("Albüm")
            idx_sure    = headers.index("Süre (sn)")
        except ValueError as e:
            logger.error(f"❌ Başlık bulunamadı: {e}")
            return

        track_stats  = defaultdict(lambda: {"count": 0, "duration_sec": 0, "artist": ""})
        artist_stats = defaultdict(lambda: {"count": 0, "duration_sec": 0})
        album_stats  = defaultdict(lambda: {"count": 0, "duration_sec": 0, "artist": ""})

        for row in rows:
            if len(row) <= max(idx_sarki, idx_sanatci, idx_album, idx_sure):
                continue
            sarki   = row[idx_sarki].strip()
            sanatci = row[idx_sanatci].strip()
            album   = row[idx_album].strip()
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
            if album and album != "—":
                album_stats[album]["count"] += 1
                album_stats[album]["duration_sec"] += sure
                album_stats[album]["artist"] = sanatci

        ozet_ws = self._find_sheet("Özet")
        ozet_ws.clear()

        out = []
        out.append(["📊 SANATÇI ÖZET"])
        out.append(["Sanatçı", "Dinlenme Sayısı", "Toplam Süre (dk)"])
        for artist, s in sorted(artist_stats.items(), key=lambda x: -x[1]["count"]):
            out.append([artist, s["count"], round(s["duration_sec"] / 60, 1)])

        out.append([])
        out.append(["📊 ŞARKI ÖZET"])
        out.append(["Şarkı", "Sanatçı", "Dinlenme Sayısı", "Toplam Süre (dk)"])
        for track, s in sorted(track_stats.items(), key=lambda x: -x[1]["count"]):
            out.append([track, s["artist"], s["count"], round(s["duration_sec"] / 60, 1)])

        out.append([])
        out.append(["📊 ALBÜM ÖZET"])
        out.append(["Albüm", "Sanatçı", "Dinlenme Sayısı", "Toplam Süre (dk)"])
        for album, s in sorted(album_stats.items(), key=lambda x: -x[1]["count"]):
            out.append([album, s["artist"], s["count"], round(s["duration_sec"] / 60, 1)])

        ozet_ws.update("A1", out, value_input_option="RAW")
        logger.info(f"📊 Özet güncellendi: {len(artist_stats)} sanatçı, {len(track_stats)} şarkı, {len(album_stats)} albüm")

    def update_analiz(self):
        headers, rows = self._get_tumveri_data()
        if not headers:
            logger.info("TümVeri sayfasında yeterli veri yok.")
            return

        try:
            idx_tarih = headers.index("Dinlenme Tarihi")
            idx_sure  = headers.index("Süre (sn)")
        except ValueError as e:
            logger.error(f"❌ Başlık bulunamadı: {e}")
            return

        gun_sureler = defaultdict(int)
        ay_stats    = defaultdict(lambda: {"sure": 0, "gunler": set()})

        for row in rows:
            if len(row) <= max(idx_tarih, idx_sure):
                continue
            tarih = row[idx_tarih].strip()
            try:
                sure = int(row[idx_sure])
            except (ValueError, IndexError):
                sure = 0
            if not tarih:
                continue

            gun_sureler[tarih] += sure

            try:
                gun, ay, yil = tarih.split(".")
                ay_key = f"{yil}-{ay}"
                ay_stats[ay_key]["sure"] += sure
                ay_stats[ay_key]["gunler"].add(tarih)
            except ValueError:
                pass

        # ── A-C: Son 7 gün ──
        bugun    = datetime.now(timezone.utc).date()
        son_7_gun = [(bugun - timedelta(days=i)) for i in range(6, -1, -1)]

        haftalar = defaultdict(list)
        for gun in son_7_gun:
            hno = gun.isocalendar()[:2]
            haftalar[hno].append(gun)

        haftalik_rows = [["Tarih", "Gün", "Dinleme Süresi"]]
        for i, (hno, gunler) in enumerate(sorted(haftalar.items())):
            if i > 0:
                haftalik_rows.append(["", "", ""])
            for gun in sorted(gunler):
                tarih_str = gun.strftime("%d.%m.%Y")
                gun_adi   = TR_GUNLER[gun.weekday()]
                toplam_sn = gun_sureler.get(tarih_str, 0)
                sure_str  = format_sure(toplam_sn) if toplam_sn > 0 else "—"
                haftalik_rows.append([tarih_str, gun_adi, sure_str])

        # ── G-I: Aylık analiz (tüm zamanlar) ──
        aylik_rows = [["Ay", "Aylık Toplam", "Günlük Ortalama"]]
        for ay_key in sorted(ay_stats.keys()):
            yil, ay_no  = ay_key.split("-")
            ay_label    = f"{TR_AYLAR[int(ay_no)]} {yil}"
            toplam_sn   = ay_stats[ay_key]["sure"]
            gun_sayisi  = len(ay_stats[ay_key]["gunler"])
            ort_sn      = toplam_sn // gun_sayisi if gun_sayisi > 0 else 0
            aylik_rows.append([ay_label, format_sure(toplam_sn), format_sure(ort_sn)])

        analiz_ws = self._find_sheet("Analiz")
        analiz_ws.clear()
        analiz_ws.update("A1", haftalik_rows, value_input_option="RAW")
        analiz_ws.update("G1", aylik_rows,    value_input_option="RAW")

        logger.info(f"📅 Analiz güncellendi: {len(son_7_gun)} gün, {len(ay_stats)} ay")

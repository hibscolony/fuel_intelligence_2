"""
data_loader.py
==============
Membaca workbook Excel lama (.xls) "Laporan Bulanan Pemakian Solar" dan
mem-parsing strukturnya yang tidak beraturan (posisi section bergeser per
bulan) menjadi baris-baris transaksi long-format, TANPA mengasumsikan posisi
baris/kolom yang sama di setiap sheet.

Lihat README.md / data dictionary untuk penjelasan struktur workbook.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import sys as _sys
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))
import config


MONTH_MAP = {
    "JANUARI": 1, "FEBUARI": 2, "FEBRUARI": 2, "MARET": 3, "APRIL": 4,
    "MEI": 5, "MAY": 5, "JUNI": 6, "JULI": 7, "AGUSTUS": 8, "SEPTEMBER": 9,
    "OKTOBER": 10, "NOVEMBER": 11, "NOPEMBER": 11, "DESEMBER": 12,
}

# (kata kunci judul section, label kategori, apakah section berisi >1 sub-grup)
#
# CATATAN: layout tahun 2026 memecah beberapa section yang di 2025 digabung
# jadi satu, dan menambah 1 kategori baru (Compresor Meantenance). Urutan
# list ini PENTING -- keyword yang lebih SPESIFIK/PANJANG harus dicek lebih
# dulu supaya tidak salah tertangkap oleh keyword yang lebih pendek/umum
# (mis. "KENDARAAN BUS DAN ELF" harus dicek sebelum "KENDARAAN BUS" saja,
# karena keduanya sama-sama mengandung substring "KENDARAAN BUS").
SECTION_KEYWORDS = [
    ("PEMAKAIAN SOLAR RTGC", "RTGC", False),
    ("PEMAKAIAN SOLAR HEADTRUCK", "HEAD_TRUCK", False),
    ("PEMAKAIAN SOLAR SL", "SUPPORT", False),                       # 2025: SL/ RS/ FORKLIF (digabung)
    ("PEMAKAIAN SOLAR SITE LOADER", "SUPPORT", False),              # 2026: dipisah per alat
    ("PEMAKAIAN SOLAR RICHT STAGGER", "SUPPORT", False),            # 2026: "Reach Stacker" (ejaan sumber)
    ("PEMAKAIAN SOLAR REACH STACKER", "SUPPORT", False),            # jaga-jaga ejaan lain di file mendatang
    ("PEMAKAIAN SOLAR FORKLIF", "SUPPORT", False),                  # 2026: dipisah per alat
    ("PEMAKAIAN SOLAR COMPRESOR", "COMPRESSOR", False),             # 2026: kategori baru, tidak ada di 2025
    ("PEMAKAIAN SOLAR KOMPRESOR", "COMPRESSOR", False),             # jaga-jaga ejaan lain
    ("PEMAKAIAN SOLAR KENDARAAN OPERATIONAL", "KEND_OPS", False),
    ("PEMAKAIAN SOLAR KENDARAAN BUS DAN ELF", "BUS_ELF", True),     # 2025: 1 section, 2 sub-blok BUS+ELF
    ("PEMAKAIAN SOLAR KENDARAAN BUS", "BUS", False),                # 2026: BUS section sendiri
    ("PEMAKAIAN SOLAR KENDARAAN ELF", "ELF", False),                # 2026: ELF section sendiri
    ("PEMAKAIAN SOLAR MODUL", "MODUL", False),
    ("TOTALISATOR", "__TOTALISATOR__", False),
]


class WorkbookStructureError(ValueError):
    """Dilempar saat struktur sheet tidak sesuai pola yang diharapkan."""


@dataclass
class HeaderAnomaly:
    """Satu kejadian anomali pada baris header (mis. nomor hari berulang)."""
    source_sheet: str
    header_row: int
    day: int
    dropped_col: int


@dataclass
class ParseResult:
    """Hasil parsing satu workbook penuh."""
    long_df: pd.DataFrame
    totalisator_df: pd.DataFrame
    header_anomalies: list[HeaderAnomaly] = field(default_factory=list)


def load_raw_sheets(path: Path) -> dict[str, pd.DataFrame]:
    """Baca seluruh sheet workbook .xls TANPA header (header=None) supaya
    posisi judul/section bisa dideteksi secara dinamis per sheet.

    Raises
    ------
    RuntimeError jika file tidak bisa dibuka (mis. xlrd belum terinstall
    untuk membaca format .xls lama, atau file korup).
    """
    try:
        xls = pd.ExcelFile(path)
    except Exception as exc:
        raise RuntimeError(
            f"Gagal membuka file '{path}'. Jika file berformat .xls lama, "
            f"pastikan library 'xlrd' terinstall (`pip install xlrd`). "
            f"Error asli: {exc}"
        ) from exc

    raw_sheets = {}
    for sheet_name in xls.sheet_names:
        raw_sheets[sheet_name] = pd.read_excel(xls, sheet_name=sheet_name, header=None)
    if not raw_sheets:
        raise WorkbookStructureError(f"Workbook '{path}' tidak memiliki sheet apapun.")
    return raw_sheets


def month_from_sheet_name(sheet_name: str) -> int:
    """Petakan nama sheet berbahasa Indonesia ('JANUARI 2025', dst) ke nomor bulan (1-12)."""
    key = sheet_name.strip().upper().split()[0]
    if key not in MONTH_MAP:
        raise WorkbookStructureError(f"Nama bulan tidak dikenali pada sheet: {sheet_name!r}")
    return MONTH_MAP[key]


def year_from_sheet_name(sheet_name: str) -> int:
    """Ambil token tahun (4 digit) dari nama sheet, mis. 'JANUARI 2026' -> 2026."""
    match = re.search(r"(19|20)\d{2}", sheet_name)
    if not match:
        raise WorkbookStructureError(f"Tahun tidak ditemukan pada nama sheet: {sheet_name!r}")
    return int(match.group())


def is_monthly_data_sheet(sheet_name: str) -> bool:
    """Cek apakah sheet ini kemungkinan sheet data bulanan yang valid (nama
    bulan + tahun dikenali) -- dipakai utk melewati sheet lain di workbook
    (rekap mingguan, catatan khusus, sheet kosong, dst) tanpa membuat pipeline
    berhenti.
    """
    try:
        month_from_sheet_name(sheet_name)
        year_from_sheet_name(sheet_name)
        return True
    except WorkbookStructureError:
        return False


class MonthlySheetParser:
    """Parsing satu sheet bulanan. Instansiasi baru per sheet -- anomali
    header dikumpulkan per-instance (bukan state global), aman dipakai
    berulang kali di dalam sesi Streamlit yang sama.
    """

    def __init__(self, raw: pd.DataFrame, sheet_name: str):
        self.raw = raw
        self.sheet_name = sheet_name
        self.month = month_from_sheet_name(sheet_name)
        self.year = year_from_sheet_name(sheet_name)
        self.anomalies: list[HeaderAnomaly] = []

    # -- deteksi section ----------------------------------------------------
    def find_section_starts(self) -> list[tuple[int, str, bool]]:
        """Cari baris judul tiap section pada kolom pertama sheet mentah."""
        starts = []
        for i in range(self.raw.shape[0]):
            cell = self.raw.iat[i, 0]
            if not isinstance(cell, str):
                continue
            text = " ".join(cell.upper().split())
            for keyword, label, multi in SECTION_KEYWORDS:
                if keyword in text:
                    starts.append((i, label, multi))
                    break
        return starts

    # -- pemetaan kolom hari --------------------------------------------------
    def header_day_columns(self, header_row: int) -> dict[int, int]:
        """Dari baris header suatu section, kembalikan {index_kolom: nomor_hari}
        untuk kolom yang headernya bilangan bulat 1-31. Kolom non-numerik
        ('/Minggu', 'WEEKS', 'Bulan') sengaja dikecualikan.

        Jika satu nomor hari muncul lebih dari sekali pada baris header yang
        sama (typo di file sumber), hanya kemunculan PERTAMA yang dipakai;
        sisanya dicatat ke self.anomalies.
        """
        col_to_day: dict[int, int] = {}
        seen_days: set[int] = set()
        for j in range(1, self.raw.shape[1]):
            val = self.raw.iat[header_row, j]
            day: Optional[int] = None
            if isinstance(val, (int, float)) and not pd.isna(val):
                if float(val).is_integer() and 1 <= int(val) <= 31:
                    day = int(val)
            elif isinstance(val, str):
                s = val.strip()
                if s.isdigit() and 1 <= int(s) <= 31:
                    day = int(s)
            if day is not None:
                if day in seen_days:
                    self.anomalies.append(HeaderAnomaly(self.sheet_name, header_row, day, j))
                    continue
                seen_days.add(day)
                col_to_day[j] = day
        return col_to_day

    @staticmethod
    def classify_cell(value) -> tuple[float, Optional[str]]:
        """Klasifikasikan satu sel mentah menjadi (fuel_liter, status_text)."""
        if pd.isna(value):
            return np.nan, None
        if isinstance(value, (int, float)):
            return float(value), None
        text = str(value).strip()
        if text == "":
            return np.nan, None
        upper = " ".join(text.upper().split())
        if upper in config.STATUS_TOKENS:
            return np.nan, upper
        try:
            return float(text.replace(",", ".")), None
        except ValueError:
            return np.nan, upper  # token tak dikenal -- disimpan, bukan dibuang

    # -- parsing blok data ----------------------------------------------------
    def parse_data_block(self, header_row: int, category: str, section_end: int) -> list[dict]:
        """Parsing satu blok data alat (RTGC/HT/SUPPORT/KEND_OPS/MODUL)."""
        col_to_day = self.header_day_columns(header_row)
        records = []
        r = header_row + 1
        while r < section_end:
            first_col = self.raw.iat[r, 0]
            if isinstance(first_col, str) and first_col.strip().upper() == "TOTAL":
                r += 1
                break
            if isinstance(first_col, str) and first_col.strip().upper() == "UNIT":
                r += 1
                continue
            if pd.isna(first_col):
                r += 1
                continue
            equipment_id = str(first_col).strip()
            for col_idx, day in col_to_day.items():
                fuel, status = self.classify_cell(self.raw.iat[r, col_idx])
                if pd.isna(fuel) and status is None:
                    continue  # sel benar-benar kosong -- bukan missing bertanda
                records.append({
                    "day": day, "month": self.month, "year": self.year, "equipment_category": category,
                    "equipment_id": equipment_id, "fuel_liter": fuel, "status_text": status,
                    "source_sheet": self.sheet_name, "source_row": r,
                })
            r += 1
        return records

    def parse_bus_elf_block(self, header_row: int, section_end: int) -> list[dict]:
        """Section 'KENDARAAN BUS DAN ELF' menumpuk 2 grup alat/TOTAL/UNIT di
        bawah satu baris header yang sama: grup pertama = BUS, kedua = ELF.
        """
        col_to_day = self.header_day_columns(header_row)
        records = []
        r = header_row + 1
        group_labels = ["BUS", "ELF"]
        group_idx = 0
        while r < section_end and group_idx < len(group_labels):
            first_col = self.raw.iat[r, 0]
            if isinstance(first_col, str) and first_col.strip().upper() == "TOTAL":
                r += 1
                group_idx += 1
                continue
            if isinstance(first_col, str) and first_col.strip().upper() == "UNIT":
                r += 1
                continue
            if pd.isna(first_col):
                r += 1
                continue
            equipment_id = str(first_col).strip()
            category = group_labels[group_idx]
            for col_idx, day in col_to_day.items():
                fuel, status = self.classify_cell(self.raw.iat[r, col_idx])
                if pd.isna(fuel) and status is None:
                    continue
                records.append({
                    "day": day, "month": self.month, "year": self.year, "equipment_category": category,
                    "equipment_id": equipment_id, "fuel_liter": fuel, "status_text": status,
                    "source_sheet": self.sheet_name, "source_row": r,
                })
            r += 1
        return records

    def parse_totalisator(self, header_row: int, section_end: int) -> list[dict]:
        """Parsing blok TOTALISATOR -- HANYA dipakai untuk validasi (Tahap 3),
        tidak pernah menjadi baris transaksi.
        """
        col_to_day = self.header_day_columns(header_row)
        rows = []
        r = header_row + 1
        while r < section_end:
            label = self.raw.iat[r, 0]
            if not isinstance(label, str) or label.strip() == "":
                r += 1
                continue
            cat = label.strip().upper()
            if cat == "TOTAL":
                cat = "GRAND_TOTAL"
            for col_idx, day in col_to_day.items():
                val = self.raw.iat[r, col_idx]
                if isinstance(val, (int, float)) and not pd.isna(val):
                    rows.append({"month": self.month, "year": self.year, "day": day, "category_label": cat,
                                 "workbook_value": float(val)})
            r += 1
        return rows

    # -- entry point ------------------------------------------------------
    def parse(self) -> tuple[list[dict], list[dict]]:
        """Parsing sheet penuh -> (records, totalisator_rows)."""
        starts = self.find_section_starts()
        if not starts:
            raise WorkbookStructureError(
                f"Tidak ada section yang terdeteksi pada sheet {self.sheet_name!r} -- "
                f"struktur sheet mungkin berbeda dari yang diharapkan. Periksa manual."
            )
        starts.append((self.raw.shape[0], "__END__", False))

        records: list[dict] = []
        totalisator_rows: list[dict] = []
        for idx in range(len(starts) - 1):
            row0, label, multi = starts[idx]
            row_next = starts[idx + 1][0]
            header_row = row0 + 1
            if header_row >= row_next:
                continue
            if label == "__TOTALISATOR__":
                totalisator_rows.extend(self.parse_totalisator(header_row, row_next))
            elif multi:
                records.extend(self.parse_bus_elf_block(header_row, row_next))
            else:
                records.extend(self.parse_data_block(header_row, label, row_next))
        return records, totalisator_rows


def parse_single_workbook(path: Path) -> ParseResult:
    """Parsing SATU file workbook -> ParseResult. Sheet yang namanya tidak
    dikenali sebagai 'BULAN TAHUN' (mis. rekap mingguan, catatan khusus,
    sheet kosong) otomatis DILEWATI, bukan membuat pipeline berhenti --
    tapi tetap dicatat di `skipped_sheets` supaya tetap bisa diaudit.
    """
    raw_sheets = load_raw_sheets(path)

    all_records: list[dict] = []
    all_totalisator: list[dict] = []
    all_anomalies: list[HeaderAnomaly] = []
    skipped_sheets: list[str] = []

    for sheet_name, raw in raw_sheets.items():
        if not is_monthly_data_sheet(sheet_name):
            skipped_sheets.append(sheet_name)
            continue
        parser = MonthlySheetParser(raw, sheet_name)
        try:
            records, totalisator_rows = parser.parse()
        except WorkbookStructureError:
            skipped_sheets.append(sheet_name)
            continue
        for r in records:
            r["source_file"] = path.name
        for r in totalisator_rows:
            r["source_file"] = path.name
        all_records.extend(records)
        all_totalisator.extend(totalisator_rows)
        all_anomalies.extend(parser.anomalies)

    long_df = pd.DataFrame(all_records)
    if long_df.empty:
        raise WorkbookStructureError(f"Parsing '{path.name}' menghasilkan 0 baris transaksi -- periksa struktur file.")

    long_df["date"] = pd.to_datetime(
        dict(year=long_df["year"], month=long_df["month"], day=long_df["day"]), errors="coerce")
    long_df = long_df.drop(columns=["day"])
    long_df = long_df[["date", "year", "month", "equipment_category", "equipment_id", "fuel_liter",
                        "status_text", "source_sheet", "source_file", "source_row"]]

    totalisator_df = pd.DataFrame(all_totalisator)
    if not totalisator_df.empty:
        totalisator_df["date"] = pd.to_datetime(
            dict(year=totalisator_df["year"], month=totalisator_df["month"], day=totalisator_df["day"]),
            errors="coerce")

    if skipped_sheets:
        warnings.warn(
            f"{len(skipped_sheets)} sheet di '{path.name}' dilewati (bukan format data bulanan "
            f"'BULAN TAHUN' yang dikenali): {skipped_sheets}", stacklevel=2,
        )

    return ParseResult(long_df=long_df, totalisator_df=totalisator_df, header_anomalies=all_anomalies)


def parse_workbook(path: Optional[Path] = None, paths: Optional[list[Path]] = None) -> ParseResult:
    """Parsing satu ATAU BEBERAPA file workbook (mis. laporan 2025 dan 2026
    yang terpisah file) -> ParseResult gabungan. Data dari tiap file
    digabung, diurutkan tanggal, dan boleh saling menyambung tahun.

    Parameters
    ----------
    path : Path, optional
        Path ke SATU file .xls/.xlsx. Dipertahankan utk kompatibilitas mundur.
    paths : list[Path], optional
        Daftar path ke BEBERAPA file. Jika diisi, `path` diabaikan.
        Jika None dan `path` juga None, seluruh file .xls/.xlsx di
        data/raw/ dipakai (lewat config.resolve_raw_workbook_paths()).
    """
    if paths is None:
        paths = [path] if path is not None else config.resolve_raw_workbook_paths()

    results = [parse_single_workbook(p) for p in paths]

    long_df = pd.concat([r.long_df for r in results], ignore_index=True)
    totalisator_frames = [r.totalisator_df for r in results if not r.totalisator_df.empty]
    totalisator_df = pd.concat(totalisator_frames, ignore_index=True) if totalisator_frames else pd.DataFrame()
    all_anomalies = [a for r in results for a in r.header_anomalies]

    long_df = long_df.sort_values(["date", "equipment_category", "equipment_id"]).reset_index(drop=True)
    return ParseResult(long_df=long_df, totalisator_df=totalisator_df, header_anomalies=all_anomalies)


if __name__ == "__main__":
    result = parse_workbook()
    print(f"Baris transaksi: {len(result.long_df):,}")
    print(f"Rentang tanggal: {result.long_df['date'].min()} - {result.long_df['date'].max()}")
    print(result.long_df["equipment_category"].value_counts())
    print(f"Anomali header terdeteksi: {len(result.header_anomalies)}")

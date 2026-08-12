"""Unit test dasar untuk src/data_loader.py -- dijalankan dengan: pytest tests/"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.data_loader import parse_workbook, month_from_sheet_name, WorkbookStructureError


def test_month_from_sheet_name_recognizes_indonesian_months():
    assert month_from_sheet_name("JANUARI 2025") == 1
    assert month_from_sheet_name("DESEMBER 2025") == 12
    assert month_from_sheet_name("FEBUARI 2025") == 2  # ejaan sesuai file sumber


def test_month_from_sheet_name_rejects_unknown():
    with pytest.raises(WorkbookStructureError):
        month_from_sheet_name("BULAN MISTERI 2025")


@pytest.fixture(scope="module")
def parse_result():
    return parse_workbook()


def test_parse_workbook_returns_all_12_months(parse_result):
    assert sorted(parse_result.long_df["month"].unique()) == list(range(1, 13))


def test_parse_workbook_has_expected_categories(parse_result):
    # COMPRESSOR baru muncul di layout tahun 2026 -- tes tetap memastikan
    # kategori 2025 tidak hilang, dan mengizinkan kategori baru bertambah
    core_categories = {"RTGC", "HEAD_TRUCK", "SUPPORT", "KEND_OPS", "BUS", "ELF", "MODUL"}
    found = set(parse_result.long_df["equipment_category"].unique())
    assert core_categories <= found


def test_parse_workbook_no_totalisator_rows_leak_into_long_df(parse_result):
    # baris TOTALISATOR tidak boleh muncul sebagai equipment_category
    assert "GRAND_TOTAL" not in parse_result.long_df["equipment_category"].unique()
    assert "TOTAL" not in parse_result.long_df["equipment_id"].unique()
    assert "UNIT" not in parse_result.long_df["equipment_id"].unique()


def test_source_row_is_populated_and_traceable(parse_result):
    assert parse_result.long_df["source_row"].notna().all()
    assert (parse_result.long_df["source_row"] >= 0).all()


def test_known_header_anomaly_is_detected(parse_result):
    # anomali typo "21"x3 pada blok TOTALISATOR Feb/Mar/Apr 2025 harus tetap terdeteksi,
    # meskipun file lain (mis. laporan tahun berikutnya) ikut dimuat bersamaan
    anomaly_sheets = {a.source_sheet for a in parse_result.header_anomalies}
    assert {"FEBUARI 2025", "MARET 2025", "APRIL 2025"} <= anomaly_sheets


def test_parse_workbook_raises_clear_error_for_missing_file(tmp_path):
    fake_path = tmp_path / "does_not_exist.xls"
    with pytest.raises(RuntimeError):
        parse_workbook(fake_path)

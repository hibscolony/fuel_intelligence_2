"""Tests for source-aware cleaning semantics."""
import pandas as pd

from src.data_cleaning import build_cleaned_fuel_data


def _base_ujb_rows():
    return [
        {
            "date": pd.Timestamp("2026-08-18"), "event_time": "08:00:00",
            "year": 2026, "month": 8, "equipment_category": "HEAD_TRUCK",
            "equipment_id": "101", "fuel_liter": 50.0, "status_text": "online",
            "source_sheet": "N/A", "source_file": "ujb_dashboard_scrape", "source_row": 1,
            "source_event_key": "evt-1", "source_system": "UJB",
            "source_selection_reason": "UJB_PREFERRED_ON_COVERED_DATE",
        },
        {
            "date": pd.Timestamp("2026-08-18"), "event_time": "13:00:00",
            "year": 2026, "month": 8, "equipment_category": "HEAD_TRUCK",
            "equipment_id": "101", "fuel_liter": 40.0, "status_text": "online",
            "source_sheet": "N/A", "source_file": "ujb_dashboard_scrape", "source_row": 2,
            "source_event_key": "evt-2", "source_system": "UJB",
            "source_selection_reason": "UJB_PREFERRED_ON_COVERED_DATE",
        },
    ]


def test_two_real_ujb_refuels_same_unit_day_are_not_duplicates():
    cleaned = build_cleaned_fuel_data(pd.DataFrame(_base_ujb_rows()))
    assert len(cleaned) == 2
    assert not (cleaned["data_status"] == "DUPLICATE").any()
    assert not (cleaned["data_status"] == "UNKNOWN_STATUS").any()


def test_repeated_ujb_source_event_key_is_duplicate():
    rows = _base_ujb_rows()
    rows[1]["source_event_key"] = "evt-1"
    cleaned = build_cleaned_fuel_data(pd.DataFrame(rows))
    assert (cleaned["data_status"] == "DUPLICATE").all()


def test_excel_same_unit_day_duplicate_semantics_remain():
    rows = [
        {
            "date": pd.Timestamp("2025-01-01"), "year": 2025, "month": 1,
            "equipment_category": "RTGC", "equipment_id": "01",
            "fuel_liter": 100.0, "status_text": None,
            "source_sheet": "JANUARI 2025", "source_file": "a.xls", "source_row": 10,
            "source_system": "EXCEL", "source_selection_reason": "EXCEL_NON_UJB_CATEGORY",
        },
        {
            "date": pd.Timestamp("2025-01-01"), "year": 2025, "month": 1,
            "equipment_category": "RTGC", "equipment_id": "01",
            "fuel_liter": 100.0, "status_text": None,
            "source_sheet": "JANUARI 2025", "source_file": "copy.xls", "source_row": 10,
            "source_system": "EXCEL", "source_selection_reason": "EXCEL_NON_UJB_CATEGORY",
        },
    ]
    cleaned = build_cleaned_fuel_data(pd.DataFrame(rows))
    assert (cleaned["data_status"] == "DUPLICATE").all()

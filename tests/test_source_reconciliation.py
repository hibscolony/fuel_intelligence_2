"""Tests for Excel + UJB source precedence."""
import pandas as pd

from src.source_reconciliation import reconcile_excel_and_ujb


def _df(rows):
    return pd.DataFrame(rows)


def test_ujb_replaces_excel_for_dispenser_categories_on_covered_dates():
    excel = _df([
        {"date": "2026-08-17", "equipment_category": "HEAD_TRUCK", "equipment_id": "101", "fuel_liter": 70.0},
        {"date": "2026-08-18", "equipment_category": "HEAD_TRUCK", "equipment_id": "101", "fuel_liter": 75.0},
        {"date": "2026-08-18", "equipment_category": "BUS", "equipment_id": "01", "fuel_liter": 50.0},
        {"date": "2026-08-18", "equipment_category": "RTGC", "equipment_id": "01", "fuel_liter": 500.0},
    ])
    ujb = _df([
        {"date": "2026-08-18", "equipment_category": "HEAD_TRUCK", "equipment_id": "101", "fuel_liter": 72.0, "source_event_key": "a"},
        {"date": "2026-08-18", "equipment_category": "BUS", "equipment_id": "01", "fuel_liter": 49.0, "source_event_key": "b"},
    ])

    result = reconcile_excel_and_ujb(excel, ujb)
    selected = result.selected_df

    # Historical HT stays Excel; overlapping HT/BUS use UJB; RTGC always Excel.
    assert len(selected) == 4
    assert ((selected["date"] == pd.Timestamp("2026-08-17")) & (selected["source_system"] == "EXCEL")).any()
    assert ((selected["date"] == pd.Timestamp("2026-08-18")) & (selected["equipment_category"] == "HEAD_TRUCK") & (selected["source_system"] == "UJB")).any()
    assert ((selected["date"] == pd.Timestamp("2026-08-18")) & (selected["equipment_category"] == "BUS") & (selected["source_system"] == "UJB")).any()
    assert ((selected["equipment_category"] == "RTGC") & (selected["source_system"] == "EXCEL")).any()

    overlap_excel = selected[
        (selected["date"] == pd.Timestamp("2026-08-18"))
        & selected["equipment_category"].isin(["HEAD_TRUCK", "BUS"])
        & (selected["source_system"] == "EXCEL")
    ]
    assert overlap_excel.empty


def test_gap_date_is_not_silently_assumed_to_have_ujb_coverage():
    excel = _df([
        {"date": "2026-08-19", "equipment_category": "HEAD_TRUCK", "equipment_id": "101", "fuel_liter": 80.0},
    ])
    ujb = _df([
        {"date": "2026-08-18", "equipment_category": "HEAD_TRUCK", "equipment_id": "101", "fuel_liter": 70.0},
        {"date": "2026-08-20", "equipment_category": "HEAD_TRUCK", "equipment_id": "101", "fuel_liter": 75.0},
    ])

    result = reconcile_excel_and_ujb(excel, ujb)
    retained = result.selected_df[
        (result.selected_df["date"] == pd.Timestamp("2026-08-19"))
        & (result.selected_df["source_system"] == "EXCEL")
    ]
    assert len(retained) == 1
    assert retained.iloc[0]["source_selection_reason"] == "EXCEL_OUTSIDE_UJB_COVERAGE"


def test_multiple_ujb_events_same_unit_day_are_preserved():
    excel = _df([])
    ujb = _df([
        {"date": "2026-08-18", "event_time": "08:00:00", "equipment_category": "HEAD_TRUCK", "equipment_id": "101", "fuel_liter": 50.0, "source_event_key": "evt-1"},
        {"date": "2026-08-18", "event_time": "13:00:00", "equipment_category": "HEAD_TRUCK", "equipment_id": "101", "fuel_liter": 40.0, "source_event_key": "evt-2"},
    ])

    result = reconcile_excel_and_ujb(excel, ujb)
    assert len(result.selected_df) == 2
    assert result.selected_df["source_event_key"].nunique() == 2


def test_audit_reports_suppressed_excel_volume():
    excel = _df([
        {"date": "2026-08-18", "equipment_category": "BUS", "equipment_id": "01", "fuel_liter": 50.0},
    ])
    ujb = _df([
        {"date": "2026-08-18", "equipment_category": "BUS", "equipment_id": "01", "fuel_liter": 49.0},
    ])

    result = reconcile_excel_and_ujb(excel, ujb)
    suppressed = result.audit_df[
        (result.audit_df["source_system"] == "EXCEL")
        & (result.audit_df["selection_reason"] == "EXCEL_SUPPRESSED_UJB_PRECEDENCE")
    ].iloc[0]
    assert suppressed["suppressed_rows"] == 1
    assert suppressed["suppressed_liter"] == 50.0

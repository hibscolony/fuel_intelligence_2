"""Tests for Excel + UJB source precedence."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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


def test_forklift_ujb_is_not_added_when_excel_support_exists_same_day():
    excel = _df([
        {"date": "2026-08-18", "equipment_category": "SUPPORT", "equipment_id": "17", "fuel_liter": 30.0},
        {"date": "2026-08-18", "equipment_category": "SUPPORT", "equipment_id": "RS-01", "fuel_liter": 90.0},
    ])
    ujb = _df([
        {"date": "2026-08-18", "equipment_category": "FORKLIFT", "equipment_id": "17", "fuel_liter": 20.0, "source_event_key": "fork-1"},
    ])

    result = reconcile_excel_and_ujb(excel, ujb)
    assert len(result.selected_df) == 2
    assert set(result.selected_df["source_system"]) == {"EXCEL"}

    suppressed = result.audit_df[
        (result.audit_df["source_system"] == "UJB")
        & (result.audit_df["selection_reason"] == "UJB_FORKLIFT_SUPPRESSED_EXCEL_SUPPORT_BRIDGE")
    ].iloc[0]
    assert suppressed["suppressed_rows"] == 1
    assert suppressed["suppressed_liter"] == 20.0


def test_forklift_ujb_is_used_when_excel_support_missing():
    excel = _df([
        {"date": "2026-08-18", "equipment_category": "RTGC", "equipment_id": "01", "fuel_liter": 500.0},
    ])
    ujb = _df([
        {"date": "2026-08-18", "equipment_category": "FORKLIFT", "equipment_id": "17", "fuel_liter": 20.0},
    ])

    result = reconcile_excel_and_ujb(excel, ujb)
    forklift = result.selected_df[result.selected_df["equipment_category"] == "FORKLIFT"]
    assert len(forklift) == 1
    assert forklift.iloc[0]["source_system"] == "UJB"
    assert forklift.iloc[0]["source_selection_reason"] == "UJB_FORKLIFT_FALLBACK_NO_EXCEL_SUPPORT"


def test_modul_ujb_is_suppressed_when_excel_modul_exists_same_day():
    excel = _df([
        {"date": "2026-08-20", "equipment_category": "MODUL", "equipment_id": "GENSET", "fuel_liter": 80.0},
    ])
    ujb = _df([
        {"date": "2026-08-20", "equipment_category": "MODUL", "equipment_id": "GENSET", "fuel_liter": 40.0, "source_event_key": "genset-1"},
    ])

    result = reconcile_excel_and_ujb(excel, ujb)
    selected = result.selected_df[result.selected_df["equipment_category"] == "MODUL"]
    assert len(selected) == 1
    assert selected.iloc[0]["source_system"] == "EXCEL"
    assert selected.iloc[0]["source_selection_reason"] == "EXCEL_MODUL_AUTHORITATIVE_WHEN_PRESENT"

    suppressed = result.audit_df[
        (result.audit_df["source_system"] == "UJB")
        & (result.audit_df["selection_reason"] == "UJB_MODUL_SUPPRESSED_EXCEL_MODUL_PRESENT")
    ].iloc[0]
    assert suppressed["suppressed_rows"] == 1
    assert suppressed["suppressed_liter"] == 40.0


def test_modul_ujb_is_used_when_excel_modul_missing():
    excel = _df([
        {"date": "2026-08-20", "equipment_category": "RTGC", "equipment_id": "01", "fuel_liter": 500.0},
    ])
    ujb = _df([
        {"date": "2026-08-20", "equipment_category": "MODUL", "equipment_id": "GENSET", "fuel_liter": 40.0, "source_event_key": "genset-1"},
    ])

    result = reconcile_excel_and_ujb(excel, ujb)
    modul = result.selected_df[result.selected_df["equipment_category"] == "MODUL"]
    assert len(modul) == 1
    assert modul.iloc[0]["source_system"] == "UJB"
    assert modul.iloc[0]["source_selection_reason"] == "UJB_MODUL_FALLBACK_NO_EXCEL_MODUL"


def test_unapproved_ujb_category_is_quarantined_from_total_but_audited():
    excel = _df([
        {"date": "2026-08-18", "equipment_category": "RTGC", "equipment_id": "01", "fuel_liter": 500.0},
    ])
    ujb = _df([
        {"date": "2026-08-18", "equipment_category": "NEW_VENDOR_TYPE", "equipment_id": "X1", "fuel_liter": 33.0},
    ])

    result = reconcile_excel_and_ujb(excel, ujb)
    assert "NEW_VENDOR_TYPE" not in set(result.selected_df["equipment_category"])

    suppressed = result.audit_df[
        (result.audit_df["source_system"] == "UJB")
        & (result.audit_df["selection_reason"] == "UJB_UNAPPROVED_CATEGORY_SUPPRESSED")
    ].iloc[0]
    assert suppressed["suppressed_rows"] == 1
    assert suppressed["suppressed_liter"] == 33.0


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

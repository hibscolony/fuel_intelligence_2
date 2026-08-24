"""Tests untuk identitas event dan history persisten UJB."""

import pandas as pd

from src.ujb_history import make_source_event_key, merge_ujb_history


def _raw_event(date="2026-08-24", time="08:00:00", unit="HT 152", volume="80.00"):
    return {
        "Site": "JICT",
        "Product": "SOLAR",
        "Date": date,
        "Time": time,
        "Unit": unit,
        "Status": "online",
        "Volume (L)": volume,
        "Kilometer": "1000",
        "Stock (L)": "9999",
        "No": "1",
    }


def _snapshot_row(event_key: str, date="2026-08-24", event_time="08:00:00", fuel=80.0):
    return {
        "date": date,
        "event_time": event_time,
        "equipment_category": "HEAD_TRUCK",
        "equipment_id": "152",
        "fuel_liter": fuel,
        "status_text": "online",
        "source_file": "ujb_dashboard_scrape",
        "source_event_key": event_key,
    }


def test_event_key_ignores_row_number_and_stock_state():
    a = _raw_event()
    b = _raw_event()
    b["No"] = "999"
    b["Stock (L)"] = "1234"

    assert make_source_event_key(a) == make_source_event_key(b)


def test_same_unit_same_day_different_time_is_distinct_event():
    a = make_source_event_key(_raw_event(time="08:00:00"))
    b = make_source_event_key(_raw_event(time="12:00:00"))
    assert a != b


def test_overlapping_snapshots_do_not_duplicate_history():
    key_a = make_source_event_key(_raw_event(time="08:00:00"))
    key_b = make_source_event_key(_raw_event(time="12:00:00", volume="75.00"))

    existing = pd.DataFrame([_snapshot_row(key_a)])
    latest = pd.DataFrame([
        _snapshot_row(key_a),
        _snapshot_row(key_b, event_time="12:00:00", fuel=75.0),
    ])

    merged = merge_ujb_history(existing, latest)

    assert len(merged) == 2
    assert merged["source_event_key"].nunique() == 2
    assert set(merged["source_event_key"]) == {key_a, key_b}

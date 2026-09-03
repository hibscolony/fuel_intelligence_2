import pandas as pd

from src.ujb_coverage import build_coverage_manifest_from_diagnostics


def _diagnostics(**coverage_overrides):
    coverage = {
        "requested_start": "2026-08-18",
        "requested_end": "2026-08-24",
        "observed_start": "2026-08-18",
        "observed_end": "2026-08-24",
        "observed_days": 7,
        "requested_days": 7,
        "reaches_requested_start": True,
    }
    coverage.update(coverage_overrides)
    return {
        "strategy": "single_range:button",
        "coverage": coverage,
        "after": {
            "inputs": [{
                "meta": "placeholder=Select date range",
                "value": "2026-08-18 to 2026-08-24",
            }]
        },
    }


def test_legacy_diagnostics_with_full_observed_window_are_complete():
    manifest = build_coverage_manifest_from_diagnostics(_diagnostics())
    assert len(manifest) == 7
    assert set(manifest["coverage_status"]) == {"COMPLETE"}


def test_explicit_failed_pagination_marks_every_requested_day_failed():
    diagnostics = _diagnostics()
    diagnostics["pagination"] = {
        "complete": False,
        "termination_reason": "page_cap_reached",
    }
    manifest = build_coverage_manifest_from_diagnostics(diagnostics)
    assert set(manifest["coverage_status"]) == {"FAILED"}


def test_complete_pagination_without_verified_date_filter_is_unknown():
    diagnostics = _diagnostics()
    diagnostics["strategy"] = "not_found"
    diagnostics["pagination"] = {
        "complete": True,
        "termination_reason": "next_disabled_last_page",
    }

    manifest = build_coverage_manifest_from_diagnostics(diagnostics)

    assert set(manifest["coverage_status"]) == {"UNKNOWN"}
    assert set(manifest["evidence"]) == {"date_filter_not_verified"}


def test_legacy_incomplete_window_is_unknown_not_authoritative():
    manifest = build_coverage_manifest_from_diagnostics(
        _diagnostics(observed_end="2026-08-22", observed_days=5)
    )
    assert set(manifest["coverage_status"]) == {"UNKNOWN"}
    assert manifest["date"].min() == pd.Timestamp("2026-08-18")

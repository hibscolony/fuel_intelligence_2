"""Tests for dashboard-facing formatting helpers."""
from src.formatting import (
    format_date_label,
    format_recommendation_evidence,
    format_recommendation_role,
)


def test_recommendation_evidence_is_human_readable_without_losing_values():
    result = format_recommendation_evidence(
        "health_score=44.4, critical_anomaly_count=5, trend_percentage=21.6%"
    )

    assert result == "Health score: 44.4 · Anomali kritis: 5 · Tren konsumsi: 21.6%"


def test_recommendation_role_and_date_are_localised_for_display():
    assert format_recommendation_role("ICT/Data Team") == "Tim ICT/Data"
    assert format_date_label("2026-09-09") == "09 Sep 2026"


def test_unknown_recommendation_values_have_safe_fallbacks():
    assert format_recommendation_role("Vendor") == "Vendor"
    assert format_date_label("belum ditentukan") == "belum ditentukan"

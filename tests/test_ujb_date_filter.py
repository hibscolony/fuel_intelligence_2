"""Tests for UJB date-range format inference."""
from datetime import date

from src.ujb_date_filter import format_range_like_current, infer_slash_date_order


def test_infer_mdy_when_second_component_exceeds_twelve():
    assert infer_slash_date_order("08/22/2026 - 08/24/2026") == "MDY"


def test_infer_dmy_when_first_component_exceeds_twelve():
    assert infer_slash_date_order("22/08/2026 - 24/08/2026") == "DMY"


def test_ambiguous_slash_date_uses_reference_date():
    assert infer_slash_date_order(
        "08/09/2026 - 08/10/2026", reference_date=date(2026, 8, 10)
    ) == "MDY"


def test_format_range_preserves_mdy_style():
    assert format_range_like_current(
        "08/22/2026 - 08/24/2026",
        "2026-08-18",
        "2026-08-24",
        reference_date=date(2026, 8, 24),
    ) == "08/18/2026 - 08/24/2026"


def test_format_range_preserves_dmy_style():
    assert format_range_like_current(
        "22/08/2026 - 24/08/2026",
        "2026-08-18",
        "2026-08-24",
        reference_date=date(2026, 8, 24),
    ) == "18/08/2026 - 24/08/2026"

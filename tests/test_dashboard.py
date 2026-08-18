"""Smoke test untuk seluruh halaman Streamlit -- dijalankan dengan: pytest tests/test_dashboard.py

Memakai streamlit.testing.v1.AppTest (framework resmi Streamlit) utk
menjalankan tiap skrip halaman TANPA server sungguhan, dan memastikan tidak
ada exception yg terlempar saat render.
"""
import sys
import warnings
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from streamlit.testing.v1 import AppTest

PAGES = [
    "pages/executive_overview.py",
    "pages/consumption_explorer.py",
    "pages/forecast_monitoring.py",
    "pages/anomaly_monitoring.py",
    "pages/equipment_health.py",
    "pages/data_quality.py",
    "pages/saving_simulator.py",
    "pages/recommendations.py",
]


@pytest.mark.parametrize("page_path", PAGES)
def test_page_runs_without_exception(page_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        at = AppTest.from_file(str(PROJECT_ROOT / page_path), default_timeout=180)
        at.run()
    assert not at.exception, f"{page_path} raised: {[str(e.value) for e in at.exception]}"


def test_app_entry_point_runs_without_exception():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=180)
        at.run()
    assert not at.exception


def test_forecast_explorer_button_triggers_prediction_without_exception():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        at = AppTest.from_file(str(PROJECT_ROOT / "pages/forecast_monitoring.py"), default_timeout=180)
        at.run()
        assert not at.exception
        buttons = at.button
        assert len(buttons) >= 1
        buttons[0].click().run()
    assert not at.exception


def test_forecast_chart_and_metrics_change_with_selected_model():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        at = AppTest.from_file(str(PROJECT_ROOT / "pages/forecast_monitoring.py"), default_timeout=200)
        at.run()
        assert not at.exception
        metrics_before = [m.value for m in at.markdown if 'jict-metric-value' in m.value][:5]

        at.selectbox[0].select("hist_gradient_boosting").run()
        assert not at.exception
        metrics_after = [m.value for m in at.markdown if 'jict-metric-value' in m.value][:5]

    assert metrics_before != metrics_after, "Metrics/graph did not change when switching models"


def test_consumption_explorer_filters_do_not_raise():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        at = AppTest.from_file(str(PROJECT_ROOT / "pages/consumption_explorer.py"), default_timeout=180)
        at.run()
        assert not at.exception

        at.selectbox[0].select("RTGC").run()
        assert not at.exception

        if len(at.selectbox[1].options) > 1:
            at.selectbox[1].select(at.selectbox[1].options[1]).run()
            assert not at.exception

        at.radio[0].set_value("Harian").run()
    assert not at.exception


def test_consumption_explorer_unit_comparison_range_mode():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        at = AppTest.from_file(str(PROJECT_ROOT / "pages/consumption_explorer.py"), default_timeout=180)
        at.run()
        assert not at.exception

        compare_cat = [s for s in at.selectbox if s.key == "compare_category"][0]
        compare_cat.select("BUS").run()
    assert not at.exception


def test_consumption_explorer_unit_comparison_single_date_mode():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        at = AppTest.from_file(str(PROJECT_ROOT / "pages/consumption_explorer.py"), default_timeout=180)
        at.run()
        assert not at.exception

        date_mode = [r for r in at.radio if r.key == "compare_date_mode"][0]
        date_mode.set_value("Tanggal Tertentu (Cek Anomali Harian)").run()
        assert not at.exception

        compare_cat = [s for s in at.selectbox if s.key == "compare_category"][0]
        compare_cat.select("RTGC").run()
    assert not at.exception

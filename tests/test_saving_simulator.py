"""Unit test dasar untuk src/saving_simulator.py -- dijalankan dengan: pytest tests/"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.saving_simulator import (
    SavingSimulatorInputs, run_saving_scenarios, calculate_l_per_teu, build_saving_report,
)


def test_bau_scenario_has_zero_saving():
    inputs = SavingSimulatorInputs(baseline_total_liter=1_000_000)
    df = run_saving_scenarios(inputs)
    bau = df[df["scenario"] == "Business as Usual"].iloc[0]
    assert bau["saving_liter"] == 0.0
    assert bau["projected_consumption"] == 1_000_000.0


def test_target_saving_achieves_full_target_percentage():
    inputs = SavingSimulatorInputs(baseline_total_liter=1_000_000, saving_target_percentage=10.0)
    df = run_saving_scenarios(inputs)
    target = df[df["scenario"] == "Target Saving"].iloc[0]
    assert abs(target["target_achievement_percentage"] - 100.0) < 1e-6
    assert abs(target["gap_to_target_liter"]) < 1e-6


def test_moderate_saving_is_between_bau_and_target():
    inputs = SavingSimulatorInputs(baseline_total_liter=1_000_000, saving_target_percentage=10.0)
    df = run_saving_scenarios(inputs)
    saving_by_scenario = df.set_index("scenario")["saving_liter"]
    assert saving_by_scenario["Business as Usual"] < saving_by_scenario["Moderate Saving"] < \
        saving_by_scenario["Target Saving"]


def test_saving_cost_scales_with_fuel_price():
    inputs_cheap = SavingSimulatorInputs(baseline_total_liter=1_000_000, saving_target_percentage=10.0,
                                          fuel_price_per_liter=1000.0)
    inputs_expensive = SavingSimulatorInputs(baseline_total_liter=1_000_000, saving_target_percentage=10.0,
                                              fuel_price_per_liter=2000.0)
    saving_cheap = run_saving_scenarios(inputs_cheap).set_index("scenario")["saving_cost"]["Target Saving"]
    saving_expensive = run_saving_scenarios(inputs_expensive).set_index("scenario")["saving_cost"]["Target Saving"]
    assert saving_expensive == pytest.approx(saving_cheap * 2, rel=1e-6)


def test_calculate_l_per_teu_without_actual_teu_returns_none_and_warns():
    result = calculate_l_per_teu(1_000_000, total_teu=None)
    assert result["actual_l_per_teu"] is None
    assert "warning" in result


def test_calculate_l_per_teu_with_actual_teu_computes_value():
    result = calculate_l_per_teu(1_000_000, total_teu=400_000)
    assert result["actual_l_per_teu"] == pytest.approx(2.5)
    assert "warning" not in result


def test_required_monthly_reduction_is_target_over_twelve():
    inputs = SavingSimulatorInputs(baseline_total_liter=1_200_000, saving_target_percentage=10.0)
    df = run_saving_scenarios(inputs)
    expected = inputs.saving_target_liter / 12
    assert (df["required_monthly_reduction"] == round(expected, 1)).all()


def test_build_saving_report_has_expected_keys():
    inputs = SavingSimulatorInputs(baseline_total_liter=1_000_000)
    report = build_saving_report(inputs)
    assert {"inputs", "scenarios", "l_per_teu_info"} <= set(report.keys())
    assert len(report["scenarios"]) == 3

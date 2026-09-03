import pandas as pd

from src.forecast_deployment import assess_production_readiness, build_candidate_registry


def test_production_gate_blocks_stale_short_gapped_data():
    gate = assess_production_readiness(
        training_end=pd.Timestamp("2025-12-31"),
        latest_actual_date=pd.Timestamp("2026-08-24"),
        latest_segment_days=7,
        source_gap_days=27,
    )

    assert gate.status == "BLOCKED"
    assert gate.training_staleness_days == 236
    assert len(gate.reasons) == 3


def test_production_gate_allows_fresh_contiguous_data_for_model_review():
    gate = assess_production_readiness(
        training_end=pd.Timestamp("2026-08-20"),
        latest_actual_date=pd.Timestamp("2026-08-24"),
        latest_segment_days=90,
        source_gap_days=0,
    )

    assert gate.status == "READY_FOR_MODEL_REVIEW"
    assert gate.reasons == ()


def test_registry_never_auto_promotes_and_respects_data_gate():
    leaderboard = pd.DataFrame([{
        "horizon_days": 30,
        "model_name": "seasonal_naive_7",
        "wape": 7.3,
        "n_forecasts": 6,
        "selection_ready": True,
        "interval_readiness_status": "READY",
        "provisional_best_for_horizon": True,
    }])

    blocked = build_candidate_registry(leaderboard, "BLOCKED")
    reviewable = build_candidate_registry(leaderboard, "READY_FOR_MODEL_REVIEW")

    assert blocked.iloc[0]["promotion_status"] == "BLOCKED"
    assert reviewable.iloc[0]["promotion_status"] == "ELIGIBLE_FOR_REVIEW"
    assert reviewable.iloc[0]["promotion_reason"] == "MANUAL_APPROVAL_REQUIRED"


def test_registry_reports_all_blocking_reasons():
    leaderboard = pd.DataFrame([{
        "horizon_days": 60,
        "model_name": "model_x",
        "wape": 20.0,
        "n_forecasts": 3,
        "selection_ready": False,
        "interval_readiness_status": "LIMITED",
        "provisional_best_for_horizon": True,
    }])

    registry = build_candidate_registry(leaderboard, "BLOCKED", max_wape_pct=15.0)

    assert registry.iloc[0]["promotion_reason"] == (
        "DATA_GATE_BLOCKED;MODEL_VALIDATION_INSUFFICIENT;WAPE_ABOVE_DEPLOYMENT_LIMIT"
    )

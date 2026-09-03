"""Production-readiness gates for forecast candidates.

Model accuracy alone is insufficient for deployment.  The latest operational
segment must also be long enough and reasonably fresh, with no unresolved
source gap between the training history and current operations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class ForecastDeploymentGate:
    status: str
    training_end: pd.Timestamp
    latest_actual_date: pd.Timestamp
    training_staleness_days: int
    latest_segment_days: int
    source_gap_days: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def assess_production_readiness(
    training_end: pd.Timestamp,
    latest_actual_date: pd.Timestamp,
    latest_segment_days: int,
    source_gap_days: int,
    max_staleness_days: int = 30,
    min_latest_segment_days: int = 60,
) -> ForecastDeploymentGate:
    """Return a deterministic data gate before any model can be promoted."""
    training_end = pd.Timestamp(training_end).normalize()
    latest_actual_date = pd.Timestamp(latest_actual_date).normalize()
    latest_segment_days = int(latest_segment_days)
    source_gap_days = int(source_gap_days)
    staleness = max(0, int((latest_actual_date - training_end).days))

    reasons: list[str] = []
    if staleness > int(max_staleness_days):
        reasons.append(
            f"Training berakhir {staleness} hari sebelum aktual terbaru "
            f"(batas {int(max_staleness_days)} hari)."
        )
    if latest_segment_days < int(min_latest_segment_days):
        reasons.append(
            f"Segmen operasional terbaru baru {latest_segment_days} hari "
            f"(minimum {int(min_latest_segment_days)} hari)."
        )
    if source_gap_days > 0:
        reasons.append(f"Masih ada {source_gap_days} hari source coverage gap.")

    return ForecastDeploymentGate(
        status="READY_FOR_MODEL_REVIEW" if not reasons else "BLOCKED",
        training_end=training_end,
        latest_actual_date=latest_actual_date,
        training_staleness_days=staleness,
        latest_segment_days=latest_segment_days,
        source_gap_days=source_gap_days,
        reasons=tuple(reasons),
    )


def build_candidate_registry(
    leaderboard: pd.DataFrame,
    data_gate_status: str,
    max_wape_pct: float = 15.0,
) -> pd.DataFrame:
    """Build an auditable registry; this function never auto-promotes a model."""
    if leaderboard.empty:
        return pd.DataFrame(columns=[
            "horizon_days", "model_name", "wape", "n_forecasts",
            "selection_ready", "interval_readiness_status",
            "candidate_status", "promotion_status", "promotion_reason",
        ])

    required = {
        "horizon_days", "model_name", "wape", "n_forecasts",
        "selection_ready", "provisional_best_for_horizon",
    }
    missing = required.difference(leaderboard.columns)
    if missing:
        raise ValueError(f"Leaderboard tidak lengkap: {sorted(missing)}")

    candidates = leaderboard[leaderboard["provisional_best_for_horizon"]].copy()
    if "interval_readiness_status" not in candidates.columns:
        candidates["interval_readiness_status"] = "UNKNOWN"
    candidates["candidate_status"] = candidates["selection_ready"].map(
        {True: "VALIDATED_CANDIDATE", False: "PROVISIONAL"}
    )

    promotion_status = []
    promotion_reason = []
    for row in candidates.itertuples(index=False):
        blockers: list[str] = []
        if str(data_gate_status) != "READY_FOR_MODEL_REVIEW":
            blockers.append("DATA_GATE_BLOCKED")
        if not bool(row.selection_ready):
            blockers.append("MODEL_VALIDATION_INSUFFICIENT")
        if float(row.wape) > float(max_wape_pct):
            blockers.append("WAPE_ABOVE_DEPLOYMENT_LIMIT")
        if blockers:
            promotion_status.append("BLOCKED")
            promotion_reason.append(";".join(blockers))
        else:
            # Explicit human approval and production artifact generation remain
            # separate actions; evaluation never deploys by itself.
            promotion_status.append("ELIGIBLE_FOR_REVIEW")
            promotion_reason.append("MANUAL_APPROVAL_REQUIRED")
    candidates["promotion_status"] = promotion_status
    candidates["promotion_reason"] = promotion_reason
    return candidates.sort_values("horizon_days").reset_index(drop=True)

"""
forecast_evaluation.py
======================
Kerangka evaluasi forecasting multi-horizon dengan rolling origin.

Tujuannya: jangan menilai forecast D+30/D+60/D+90 hanya dari backtest D+1.
Setiap origin memakai data yang tersedia SAMPAI origin tersebut, lalu model
memprediksi beberapa horizon sekaligus. Hasil diringkas terpisah per horizon.

Residual rolling-origin juga dipakai sebagai dasar prediction interval yang
horizon-aware. Origin awal dipakai untuk kalibrasi dan origin yang lebih baru
menjadi holdout evaluasi, sehingga coverage interval tidak dinilai pada
residual yang sama dengan yang membentuk interval.
"""
from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from src.forecasting_models import recursive_forecast


DEFAULT_EVAL_HORIZONS = (1, 3, 7, 14, 30, 60, 90)


def validate_daily_history(history: pd.Series) -> pd.Series:
    """Validasi bahwa history benar-benar deret kalender harian yang aman."""
    if not isinstance(history, pd.Series):
        raise TypeError("history harus berupa pandas Series.")
    if history.empty:
        raise ValueError("history kosong.")

    s = history.copy()
    if not isinstance(s.index, pd.DatetimeIndex):
        try:
            s.index = pd.to_datetime(s.index)
        except Exception as exc:
            raise ValueError("Index history harus dapat dikonversi menjadi DatetimeIndex.") from exc

    if s.index.has_duplicates:
        raise ValueError("History memiliki tanggal duplikat; agregasikan ke satu nilai per hari dulu.")

    s = s.sort_index().astype(float)
    expected = pd.date_range(s.index.min(), s.index.max(), freq="D")
    if len(expected) != len(s.index) or not s.index.equals(expected):
        raise ValueError(
            "History tidak berfrekuensi kalender harian lengkap. Jangan drop tanggal kosong; "
            "selesaikan source coverage gap sebelum membuat lag/rolling."
        )
    if s.isna().any():
        n_nan = int(s.isna().sum())
        raise ValueError(
            f"History memiliki {n_nan} hari bernilai NaN/unresolved. Jangan dropna karena lag kalender akan bergeser."
        )
    if (s < 0).any():
        raise ValueError("History mengandung nilai fuel negatif; bersihkan target sebelum forecasting.")
    return s


def _normalize_horizons(horizons: Iterable[int]) -> tuple[int, ...]:
    hs = tuple(sorted({int(h) for h in horizons}))
    if not hs or hs[0] <= 0:
        raise ValueError("Semua horizon harus integer positif.")
    return hs


def build_multi_horizon_backtest(
    history: pd.Series,
    model_name: str,
    horizons: Sequence[int] = DEFAULT_EVAL_HORIZONS,
    evaluation_days: int = 180,
    origin_step_days: int = 7,
    min_train_days: int = 60,
) -> pd.DataFrame:
    """Rolling-origin backtest untuk beberapa forecast horizon."""
    s = validate_daily_history(history)
    hs = _normalize_horizons(horizons)
    max_h = max(hs)

    evaluation_days = int(evaluation_days)
    origin_step_days = int(origin_step_days)
    min_train_days = int(min_train_days)
    if evaluation_days <= 0 or origin_step_days <= 0 or min_train_days <= 1:
        raise ValueError("evaluation_days/origin_step_days/min_train_days harus bernilai positif.")

    n = len(s)
    last_origin_pos = n - 1 - max_h
    min_origin_pos = min_train_days - 1
    if last_origin_pos < min_origin_pos:
        raise ValueError(
            f"Data terlalu pendek untuk horizon maksimum D+{max_h}: butuh minimal "
            f"{min_train_days + max_h} hari, tersedia {n}."
        )

    desired_first_origin = last_origin_pos - evaluation_days + 1
    first_origin_pos = max(min_origin_pos, desired_first_origin)

    rows: list[dict] = []
    origin_positions = list(range(first_origin_pos, last_origin_pos + 1, origin_step_days))
    if origin_positions[-1] != last_origin_pos:
        origin_positions.append(last_origin_pos)

    for origin_pos in origin_positions:
        train = s.iloc[:origin_pos + 1]
        try:
            path = recursive_forecast(train, max_h, model_name)
        except Exception as exc:
            rows.append({
                "origin_date": s.index[origin_pos], "target_date": pd.NaT,
                "horizon_days": np.nan, "actual_fuel": np.nan, "forecast_fuel": np.nan,
                "residual": np.nan, "abs_error": np.nan, "ape": np.nan,
                "model_name": model_name, "status": f"MODEL_ERROR: {type(exc).__name__}: {exc}",
            })
            continue

        for h in hs:
            target_pos = origin_pos + h
            target_date = s.index[target_pos]
            actual = float(s.iloc[target_pos])
            forecast = float(path.iloc[h - 1])
            residual = actual - forecast
            ape = abs(residual) / abs(actual) * 100 if actual != 0 else np.nan
            rows.append({
                "origin_date": s.index[origin_pos],
                "target_date": target_date,
                "horizon_days": h,
                "actual_fuel": actual,
                "forecast_fuel": forecast,
                "residual": residual,
                "abs_error": abs(residual),
                "ape": ape,
                "model_name": model_name,
                "status": "OK",
            })

    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("Tidak ada origin yang dapat dievaluasi.")
    return df.sort_values(["origin_date", "horizon_days"], na_position="last").reset_index(drop=True)


def summarize_multi_horizon_backtest(backtest_df: pd.DataFrame) -> pd.DataFrame:
    """Ringkas MAE, RMSE, WAPE, bias, dan jumlah forecast per horizon."""
    required = {"horizon_days", "actual_fuel", "forecast_fuel", "residual", "status"}
    missing = required.difference(backtest_df.columns)
    if missing:
        raise ValueError(f"Kolom backtest tidak lengkap: {sorted(missing)}")

    ok = backtest_df[backtest_df["status"] == "OK"].copy()
    if ok.empty:
        return pd.DataFrame(columns=["horizon_days", "n_forecasts", "mae", "rmse", "wape", "bias"])

    records = []
    for h, sub in ok.groupby("horizon_days", sort=True):
        residual = sub["residual"].astype(float)
        actual = sub["actual_fuel"].astype(float)
        denom = actual.abs().sum()
        records.append({
            "horizon_days": int(h),
            "n_forecasts": int(len(sub)),
            "mae": float(residual.abs().mean()),
            "rmse": float(np.sqrt(np.mean(np.square(residual)))),
            "wape": float(residual.abs().sum() / denom * 100) if denom > 0 else np.nan,
            "bias": float(residual.mean()),
        })
    return pd.DataFrame(records).sort_values("horizon_days").reset_index(drop=True)


def residual_quantiles_by_horizon(backtest_df: pd.DataFrame,
                                  lower_q: float = 0.1,
                                  upper_q: float = 0.9) -> pd.DataFrame:
    """Residual quantile terpisah per horizon untuk interval yang horizon-aware."""
    if not 0 <= lower_q < upper_q <= 1:
        raise ValueError("Quantile harus memenuhi 0 <= lower_q < upper_q <= 1.")
    ok = backtest_df[backtest_df["status"] == "OK"].copy()
    if ok.empty:
        return pd.DataFrame(columns=["horizon_days", "lower_residual", "upper_residual", "n_residuals"])

    out = (ok.groupby("horizon_days")["residual"]
           .agg(lower_residual=lambda s: s.quantile(lower_q),
                upper_residual=lambda s: s.quantile(upper_q),
                n_residuals="count")
           .reset_index())
    out["horizon_days"] = out["horizon_days"].astype(int)
    return out.sort_values("horizon_days").reset_index(drop=True)


def build_independent_interval_evaluation(
    backtest_df: pd.DataFrame,
    calibration_fraction: float = 0.6,
    lower_q: float = 0.1,
    upper_q: float = 0.9,
    min_calibration_origins: int = 5,
    min_evaluation_origins: int = 3,
    max_coverage_gap_pct: float = 20.0,
) -> dict:
    """Calibrate intervals on earlier origins and score them on later holdout origins.

    The split is chronological and shared by every horizon, preventing future
    residuals from leaking into interval calibration. The returned evaluation
    rows can also be used for honest model selection.
    """
    required = {
        "origin_date", "horizon_days", "actual_fuel", "forecast_fuel",
        "residual", "status",
    }
    missing = required.difference(backtest_df.columns)
    if missing:
        raise ValueError(f"Kolom backtest tidak lengkap: {sorted(missing)}")
    if not 0 < float(calibration_fraction) < 1:
        raise ValueError("calibration_fraction harus berada di antara 0 dan 1.")
    if min_calibration_origins < 1 or min_evaluation_origins < 1:
        raise ValueError("Minimum origin kalibrasi/evaluasi harus positif.")

    ok = backtest_df[backtest_df["status"].eq("OK")].copy()
    ok["origin_date"] = pd.to_datetime(ok["origin_date"], errors="coerce")
    ok = ok.dropna(subset=["origin_date"]).sort_values(["origin_date", "horizon_days"])
    origins = pd.Index(ok["origin_date"].drop_duplicates().sort_values())
    if len(origins) < 2:
        raise ValueError("Butuh minimal dua rolling origin untuk kalibrasi dan holdout.")

    split_pos = int(np.floor(len(origins) * float(calibration_fraction)))
    split_pos = min(max(split_pos, 1), len(origins) - 1)
    calibration_origins = set(origins[:split_pos])
    calibration = ok[ok["origin_date"].isin(calibration_origins)].copy()
    evaluation = ok[~ok["origin_date"].isin(calibration_origins)].copy()

    quantiles = residual_quantiles_by_horizon(calibration, lower_q=lower_q, upper_q=upper_q)
    q_columns = ["horizon_days", "lower_residual", "upper_residual", "n_residuals"]
    scored = evaluation.merge(quantiles[q_columns], on="horizon_days", how="left")
    scored["lower_interval"] = (scored["forecast_fuel"] + scored["lower_residual"]).clip(lower=0.0)
    scored["upper_interval"] = (scored["forecast_fuel"] + scored["upper_residual"]).clip(lower=0.0)
    scored["within_interval"] = (
        scored["actual_fuel"].ge(scored["lower_interval"])
        & scored["actual_fuel"].le(scored["upper_interval"])
    )
    scored["interval_width"] = scored["upper_interval"] - scored["lower_interval"]

    point_summary = summarize_multi_horizon_backtest(scored)
    interval_rows = []
    expected_coverage = float(upper_q - lower_q) * 100
    for horizon, sub in scored.groupby("horizon_days", sort=True):
        q_row = quantiles[quantiles["horizon_days"].eq(int(horizon))]
        n_calibration = 0 if q_row.empty else int(q_row.iloc[0]["n_residuals"])
        n_evaluation = int(len(sub))
        ready = (
            n_calibration >= int(min_calibration_origins)
            and n_evaluation >= int(min_evaluation_origins)
            and sub["lower_interval"].notna().all()
            and sub["upper_interval"].notna().all()
        )
        coverage = float(sub["within_interval"].mean() * 100) if n_evaluation else np.nan
        coverage_gap = abs(coverage - expected_coverage) if pd.notna(coverage) else np.nan
        ready = bool(ready and pd.notna(coverage_gap) and coverage_gap <= float(max_coverage_gap_pct))
        interval_rows.append({
            "horizon_days": int(horizon),
            "n_calibration": n_calibration,
            "n_evaluation": n_evaluation,
            "interval_coverage_pct": coverage,
            "expected_coverage_pct": expected_coverage,
            "coverage_gap_pct": coverage_gap,
            "mean_interval_width": float(sub["interval_width"].mean()),
            "readiness_status": "READY" if ready else "LIMITED",
        })

    interval_summary = pd.DataFrame(interval_rows)
    return {
        "calibration_df": calibration.reset_index(drop=True),
        "evaluation_df": scored.reset_index(drop=True),
        "residual_quantiles": quantiles,
        "point_summary": point_summary,
        "interval_summary": interval_summary,
        "calibration_start": pd.Timestamp(origins[0]),
        "calibration_end": pd.Timestamp(origins[split_pos - 1]),
        "evaluation_start": pd.Timestamp(origins[split_pos]),
        "evaluation_end": pd.Timestamp(origins[-1]),
        "interval_calibration_independent": True,
    }


def choose_calibration_horizon(target_horizon: int, available_horizons: Iterable[int]) -> int:
    """Pilih horizon kalibrasi konservatif: horizon terkecil >= target."""
    target_horizon = int(target_horizon)
    hs = _normalize_horizons(available_horizons)
    for h in hs:
        if h >= target_horizon:
            return h
    return hs[-1]


def apply_horizon_prediction_interval(
    point_forecast: float,
    target_horizon: int,
    residual_quantiles: pd.DataFrame,
    nonnegative: bool = True,
) -> dict:
    """Bangun empirical prediction interval sesuai forecast horizon.

    Target yang berada di antara horizon evaluasi memakai horizon berikutnya
    secara konservatif (D+5 -> residual D+7). Target di atas horizon terbesar
    memakai horizon terbesar tetapi diberi ``interval_extrapolated=True``.
    """
    required = {"horizon_days", "lower_residual", "upper_residual", "n_residuals"}
    missing = required.difference(residual_quantiles.columns)
    if missing:
        raise ValueError(f"Residual quantile tidak lengkap: {sorted(missing)}")
    if residual_quantiles.empty:
        raise ValueError("Residual quantile kosong; prediction interval belum bisa dikalibrasi.")

    q = residual_quantiles.copy()
    q["horizon_days"] = pd.to_numeric(q["horizon_days"], errors="coerce")
    q = q.dropna(subset=["horizon_days"]).sort_values("horizon_days")
    if q.empty:
        raise ValueError("Tidak ada horizon residual yang valid.")

    target_horizon = int(target_horizon)
    available = tuple(q["horizon_days"].astype(int).tolist())
    calibration_horizon = choose_calibration_horizon(target_horizon, available)
    row = q[q["horizon_days"].astype(int).eq(calibration_horizon)].iloc[0]

    point = float(point_forecast)
    lower = point + float(row["lower_residual"])
    upper = point + float(row["upper_residual"])
    if nonnegative:
        point = max(0.0, point)
        lower = max(0.0, lower)
        upper = max(0.0, upper)
    if upper < lower:
        lower, upper = upper, lower

    return {
        "point": point,
        "lower": lower,
        "upper": upper,
        "interval_calibration_horizon": int(calibration_horizon),
        "interval_n_residuals": int(row["n_residuals"]),
        "interval_extrapolated": bool(target_horizon > max(available)),
        "interval_method": "rolling_origin_residual_quantile",
        "interval_calibration_independent": False,
    }


def rank_model_horizon_summaries(
    summary_df: pd.DataFrame,
    min_forecasts: int = 5,
) -> pd.DataFrame:
    """Rank model secara TERPISAH untuk setiap horizon.

    Ranking: evaluasi lengkap lebih dulu, lalu WAPE, MAE, |bias|, RMSE.
    ``best_for_horizon`` tidak berarti model terbaik secara universal.
    """
    required = {"model_name", "horizon_days", "n_forecasts", "mae", "rmse", "wape", "bias"}
    missing = required.difference(summary_df.columns)
    if missing:
        raise ValueError(f"Summary model-horizon tidak lengkap: {sorted(missing)}")
    if summary_df.empty:
        return summary_df.copy()

    out = summary_df.copy()
    for col in ["horizon_days", "n_forecasts", "mae", "rmse", "wape", "bias"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["model_name", "horizon_days", "n_forecasts", "wape", "mae"])
    if out.empty:
        return out

    max_n = out.groupby("horizon_days")["n_forecasts"].transform("max")
    out["evaluation_complete"] = out["n_forecasts"].eq(max_n)
    if "interval_readiness_status" in out.columns:
        out["interval_ready"] = out["interval_readiness_status"].eq("READY")
    else:
        out["interval_ready"] = True
    out["selection_ready"] = (
        out["n_forecasts"].ge(int(min_forecasts)) & out["interval_ready"]
    )
    out["abs_bias"] = out["bias"].abs()
    out = out.sort_values(
        ["horizon_days", "selection_ready", "evaluation_complete", "wape", "mae", "abs_bias", "rmse", "model_name"],
        ascending=[True, False, False, True, True, True, True, True],
        na_position="last",
    ).reset_index(drop=True)
    out["rank"] = out.groupby("horizon_days").cumcount() + 1
    out["provisional_best_for_horizon"] = out["rank"].eq(1)
    out["best_for_horizon"] = out["rank"].eq(1) & out["selection_ready"]
    return out

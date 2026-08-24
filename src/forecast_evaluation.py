"""
forecast_evaluation.py
======================
Kerangka evaluasi forecasting multi-horizon dengan rolling origin.

Tujuannya: jangan menilai forecast D+30/D+60/D+90 hanya dari backtest D+1.
Setiap origin memakai data yang tersedia SAMPAI origin tersebut, lalu model
memprediksi beberapa horizon sekaligus. Hasil diringkas terpisah per horizon.
"""
from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from src.forecasting_models import recursive_forecast


DEFAULT_EVAL_HORIZONS = (1, 3, 7, 14, 30, 60, 90)


def validate_daily_history(history: pd.Series) -> pd.Series:
    """Validasi bahwa history benar-benar deret kalender harian yang aman.

    Forecast berbasis lag kalender tidak boleh menerima deret irregular atau
    NaN hasil coverage gap. Jangan memperbaikinya dengan ``dropna`` karena itu
    mengubah arti lag_7 dari 7 hari menjadi 7 observasi sebelumnya.
    """
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
    """Rolling-origin backtest untuk beberapa forecast horizon.

    Contoh satu origin:
        train <= 2025-09-30
        evaluasi D+1, D+3, D+7, D+14, D+30, ...

    Origin berikutnya maju ``origin_step_days`` hari. Model hanya melihat data
    sampai origin tersebut; aktual masa depan dipakai murni untuk evaluasi.
    """
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

    # Batasi origin ke jendela evaluasi terbaru, tetapi tetap sisakan seluruh
    # max_h hari setelah origin untuk aktual pembanding.
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


def choose_calibration_horizon(target_horizon: int, available_horizons: Iterable[int]) -> int:
    """Pilih horizon kalibrasi konservatif: horizon terkecil >= target.

    Contoh target D+5 memakai residual D+7; target D+20 memakai D+30.
    Jika target melebihi semua horizon tersedia, gunakan horizon maksimum.
    """
    target_horizon = int(target_horizon)
    hs = _normalize_horizons(available_horizons)
    for h in hs:
        if h >= target_horizon:
            return h
    return hs[-1]

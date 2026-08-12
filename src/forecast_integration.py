"""
forecast_integration.py
=======================
Modul integrasi untuk model forecasting yang SUDAH ADA (dibangun terpisah
oleh pengguna, mis. notebook JICT_Fuel_Forecasting_2025.ipynb). Modul ini
TIDAK membangun ulang model -- hanya menerima hasilnya dan memantau performa.

Format input yang diharapkan (`forecast_results.csv` di data/processed/):

    date, actual_fuel, forecast_fuel, lower_interval, upper_interval, model_name

Jika file tersebut belum ada, `load_forecast_results()` membuat DATASET
PLACEHOLDER (ditandai jelas lewat model_name) dari data aktual + forecast
seasonal-naive sederhana, semata supaya modul monitoring di bawahnya bisa
diuji ujung-ke-ujung. Placeholder ini WAJIB diganti begitu file forecast
asli tersedia -- lihat parameter `path` di bawah.
"""
from __future__ import annotations

import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config

REQUIRED_COLUMNS = ["date", "actual_fuel", "forecast_fuel", "lower_interval",
                    "upper_interval", "model_name"]


class ForecastFormatError(ValueError):
    """Dilempar jika file forecast tidak sesuai skema yang diharapkan."""


def _validate_schema(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ForecastFormatError(
            f"File forecast tidak memiliki kolom wajib: {missing}. "
            f"Kolom yang diharapkan: {REQUIRED_COLUMNS}"
        )


def _build_placeholder_forecast(daily_actual: pd.Series) -> pd.DataFrame:
    """Bangun forecast PLACEHOLDER (seasonal-naive 7 hari) semata untuk
    menguji pipeline monitoring saat model asli belum diintegrasikan.
    JANGAN dipakai sebagai forecast produksi -- model_name diberi label
    jelas supaya tidak tertukar dengan model asli pengguna.
    """
    warnings.warn(
        "forecast_results.csv tidak ditemukan -- memakai forecast PLACEHOLDER "
        "(seasonal-naive 7 hari) hanya untuk menguji pipeline monitoring. "
        "Ganti dengan output model forecasting asli sesegera mungkin.",
        stacklevel=2,
    )
    s = daily_actual.copy()
    forecast = s.shift(7)
    residual = (s - forecast).dropna()
    lower_q, upper_q = residual.quantile([0.1, 0.9]) if len(residual) else (np.nan, np.nan)

    df = pd.DataFrame({
        "date": s.index,
        "actual_fuel": s.values,
        "forecast_fuel": forecast.values,
        "lower_interval": forecast.values + lower_q,
        "upper_interval": forecast.values + upper_q,
        "model_name": config.FORECAST_PLACEHOLDER_MODEL_NAME,
    })
    return df.dropna(subset=["forecast_fuel"]).reset_index(drop=True)


def load_forecast_results(path: Optional[Path] = None,
                           daily_actual_fallback: Optional[pd.Series] = None) -> pd.DataFrame:
    """Muat hasil forecasting yang sudah ada.

    Parameters
    ----------
    path : Path, optional
        Lokasi file forecast_results.csv. Default: data/processed/forecast_results.csv
    daily_actual_fallback : pd.Series, optional
        Deret aktual harian (index=date) dipakai untuk membangun placeholder
        JIKA file forecast asli belum ditemukan. Jika tidak diberikan dan
        file tidak ada, fungsi ini melempar FileNotFoundError.
    """
    path = path or (config.PROCESSED_DATA_DIR / config.FORECAST_RESULTS_FILENAME)
    if path.exists():
        df = pd.read_csv(path, parse_dates=["date"])
        _validate_schema(df)
        return df.sort_values("date").reset_index(drop=True)

    if daily_actual_fallback is None:
        raise FileNotFoundError(
            f"File forecast '{path}' tidak ditemukan, dan tidak ada `daily_actual_fallback` "
            f"untuk membuat placeholder. Sediakan salah satunya."
        )
    return _build_placeholder_forecast(daily_actual_fallback)


def compute_forecast_errors(df: pd.DataFrame) -> pd.DataFrame:
    """Tambahkan kolom residual & error ke tabel forecast."""
    out = df.copy()
    out["residual"] = out["actual_fuel"] - out["forecast_fuel"]
    out["abs_error"] = out["residual"].abs()
    with np.errstate(divide="ignore", invalid="ignore"):
        out["pct_error"] = np.where(out["actual_fuel"] != 0,
                                     out["residual"] / out["actual_fuel"] * 100, np.nan)
    out["within_interval"] = (
        (out["actual_fuel"] >= out["lower_interval"]) & (out["actual_fuel"] <= out["upper_interval"])
    )
    return out


def _wape(actual: np.ndarray, pred: np.ndarray) -> float:
    denom = np.sum(np.abs(actual))
    return float(np.nan) if denom == 0 else float(np.sum(np.abs(actual - pred)) / denom)


def classify_model_health(wape_pct: float) -> str:
    """HEALTHY / MONITOR / RETRAIN berdasarkan WAPE (ambang di config.py, bisa diubah)."""
    if pd.isna(wape_pct):
        return "INSUFFICIENT_DATA"
    if wape_pct <= config.FORECAST_WAPE_HEALTHY_MAX:
        return "HEALTHY"
    if wape_pct <= config.FORECAST_WAPE_MONITOR_MAX:
        return "MONITOR"
    return "RETRAIN"


@dataclass
class ForecastMonitoringSummary:
    model_name: str
    n_days_evaluated: int
    mae: float
    rmse: float
    wape: float
    bias: float                # rata-rata residual bertanda; >0 = model under-forecast
    interval_coverage_pct: float
    model_health_status: str


def compute_overall_metrics(df_with_errors: pd.DataFrame) -> ForecastMonitoringSummary:
    actual = df_with_errors["actual_fuel"].values
    pred = df_with_errors["forecast_fuel"].values
    mae = float(np.mean(np.abs(actual - pred)))
    rmse = float(np.sqrt(np.mean((actual - pred) ** 2)))
    wape = _wape(actual, pred) * 100
    bias = float(np.mean(actual - pred))
    coverage = float(df_with_errors["within_interval"].mean() * 100)
    model_name = (df_with_errors["model_name"].iloc[0] if len(df_with_errors) else "UNKNOWN")

    return ForecastMonitoringSummary(
        model_name=model_name, n_days_evaluated=len(df_with_errors),
        mae=round(mae, 1), rmse=round(rmse, 1), wape=round(wape, 2), bias=round(bias, 1),
        interval_coverage_pct=round(coverage, 1),
        model_health_status=classify_model_health(wape),
    )


def compute_rolling_performance(df_with_errors: pd.DataFrame,
                                 windows: Optional[list[int]] = None) -> pd.DataFrame:
    """Rolling MAE/WAPE untuk window 7/14/30 hari (default), dihitung per
    tanggal berjalan -- dipakai untuk mendeteksi model drift dari waktu ke waktu.
    """
    windows = windows or config.FORECAST_ROLLING_WINDOWS
    d = df_with_errors.sort_values("date").set_index("date")
    rows = []
    for window in windows:
        rolling_mae = d["abs_error"].rolling(window, min_periods=max(2, window // 2)).mean()
        rolling_actual_sum = d["actual_fuel"].abs().rolling(window, min_periods=max(2, window // 2)).sum()
        rolling_error_sum = d["abs_error"].rolling(window, min_periods=max(2, window // 2)).sum()
        rolling_wape = (rolling_error_sum / rolling_actual_sum * 100)
        rows.append(pd.DataFrame({
            "date": d.index, "window_days": window,
            "rolling_mae": rolling_mae.values, "rolling_wape": rolling_wape.values,
        }))
    result = pd.concat(rows, ignore_index=True).dropna(subset=["rolling_mae"])
    result["model_health_status"] = result["rolling_wape"].apply(classify_model_health)
    return result


def detect_model_drift_warning(rolling_perf: pd.DataFrame, window_days: int = 30) -> Optional[str]:
    """Bandingkan status HEALTHY/MONITOR/RETRAIN rolling window terbaru vs
    beberapa titik sebelumnya -- beri peringatan kalau tren memburuk.
    """
    sub = rolling_perf[rolling_perf["window_days"] == window_days].sort_values("date")
    if len(sub) < 2:
        return None
    latest, previous = sub["rolling_wape"].iloc[-1], sub["rolling_wape"].iloc[-2]
    if pd.isna(latest) or pd.isna(previous):
        return None
    if latest > previous * 1.2 and latest > config.FORECAST_WAPE_HEALTHY_MAX:
        return (f"WAPE rolling {window_days} hari memburuk dari {previous:.1f}% "
                f"menjadi {latest:.1f}% -- pertimbangkan retraining.")
    return None


def save_outputs(summary: ForecastMonitoringSummary, rolling_perf: pd.DataFrame,
                  df_with_errors: pd.DataFrame, output_dir: Optional[Path] = None) -> None:
    output_dir = output_dir or config.PROCESSED_DATA_DIR
    pd.DataFrame([summary.__dict__]).to_csv(output_dir / "forecast_monitoring_summary.csv", index=False)
    rolling_perf.to_csv(output_dir / "forecast_rolling_performance.csv", index=False)
    df_with_errors.to_csv(output_dir / "forecast_residuals.csv", index=False)


if __name__ == "__main__":
    from src.data_cleaning import run_cleaning_pipeline

    result = run_cleaning_pipeline()
    valid = result.cleaned_fuel_data[result.cleaned_fuel_data["data_status"] != "INVALID_DATE"]
    daily_actual = valid.groupby("date")["fuel_liter"].sum(min_count=1).asfreq("D")

    forecast_df = load_forecast_results(daily_actual_fallback=daily_actual)
    df_err = compute_forecast_errors(forecast_df)
    summary = compute_overall_metrics(df_err)
    rolling_perf = compute_rolling_performance(df_err)
    drift_warning = detect_model_drift_warning(rolling_perf)
    save_outputs(summary, rolling_perf, df_err)

    print("=== Forecast Monitoring Summary ===")
    for k, v in summary.__dict__.items():
        print(f"  {k}: {v}")
    print(f"\nPeringatan drift: {drift_warning or '(tidak ada)'}")
    print("\n=== Rolling performance (5 baris terakhir tiap window) ===")
    for w in config.FORECAST_ROLLING_WINDOWS:
        print(f"\n-- window {w} hari --")
        print(rolling_perf[rolling_perf["window_days"] == w].tail(5))

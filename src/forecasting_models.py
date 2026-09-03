"""
forecasting_models.py
======================
Forecast Explorer: memungkinkan pengguna memilih MODEL forecasting yang
berbeda-beda dan meminta prediksi untuk TANGGAL MANAPUN (dekat maupun jauh
di masa depan), langsung dari dashboard.

Dua rezim prediksi:

1. **Horizon dekat** (<= config.FORECAST_RELIABLE_HORIZON_DAYS sejak data
   terakhir): forecast REKURSIF hari-demi-hari memakai model yang dipilih
   (baseline / statistik / ML), dengan prediction interval dari residual
   quantile hasil backtest.
2. **Horizon jauh** (lebih dari itu, termasuk "tahun kapan pun"): TIDAK
   dipaksakan pakai model rekursif yang sama (errornya akan menumpuk & tidak
   realistis untuk ratusan/ribuan langkah). Sebagai gantinya dipakai
   **klimatologi hari-dalam-tahun** (rata-rata historis di sekitar tanggal
   yang sama, +/- N hari) -- dengan interval JAUH lebih lebar dan peringatan
   eksplisit bahwa data historis hanya mencakup 1 tahun, sehingga pola
   musiman antar-tahun belum benar-benar bisa dipelajari.

Model TIDAK dibangun ulang dari nol di sini secara konseptual -- modul ini
menyediakan beberapa pilihan model standar (baseline/statistik/ML) yang
sudah lazim dipakai di notebook forecasting proyek ini, supaya bisa
dibandingkan langsung dari dashboard.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor, HistGradientBoostingRegressor

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config

FEATURE_COLS = ["day_of_week", "day_of_month", "week_of_year", "month", "is_weekend",
                "lag_1", "lag_2", "lag_3", "lag_7", "lag_14", "lag_30",
                "rolling_mean_3", "rolling_mean_7", "rolling_mean_14", "rolling_mean_30",
                "rolling_std_7", "rolling_std_30"]


def make_features(s: pd.Series) -> pd.DataFrame:
    """Fitur kalender + lag + rolling dari deret harian `s`. Semua lag/rolling
    memakai shift(1) supaya tidak bocor data masa depan.
    """
    df = s.rename("y").to_frame()
    df["day_of_week"] = df.index.dayofweek
    df["day_of_month"] = df.index.day
    df["week_of_year"] = df.index.isocalendar().week.astype(int)
    df["month"] = df.index.month
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    for lag in [1, 2, 3, 7, 14, 30]:
        df[f"lag_{lag}"] = df["y"].shift(lag)
    for win in [3, 7, 14, 30]:
        df[f"rolling_mean_{win}"] = df["y"].shift(1).rolling(win).mean()
    for win in [7, 30]:
        df[f"rolling_std_{win}"] = df["y"].shift(1).rolling(win).std()
    return df


def _build_feature_row(working: pd.Series, target_date: pd.Timestamp) -> dict:
    row = {
        "day_of_week": target_date.dayofweek, "day_of_month": target_date.day,
        "week_of_year": int(target_date.isocalendar().week), "month": target_date.month,
        "is_weekend": int(target_date.dayofweek >= 5),
    }
    for lag in [1, 2, 3, 7, 14, 30]:
        row[f"lag_{lag}"] = working.iloc[-lag]
    for win in [3, 7, 14, 30]:
        row[f"rolling_mean_{win}"] = working.iloc[-win:].mean()
    for win in [7, 30]:
        row[f"rolling_std_{win}"] = working.iloc[-win:].std()
    return row


def _get_ml_model(model_name: str):
    if model_name == "random_forest":
        return RandomForestRegressor(n_estimators=300, max_depth=8, random_state=config.RANDOM_STATE, n_jobs=-1)
    if model_name == "hist_gradient_boosting":
        return HistGradientBoostingRegressor(max_depth=6, random_state=config.RANDOM_STATE)
    raise ValueError(f"Model ML tidak dikenal: {model_name}")


def _recursive_forecast_baseline(history: pd.Series, horizon: int, mode: str) -> pd.Series:
    working = history.copy()
    preds = []
    for _ in range(horizon):
        target_date = working.index[-1] + pd.Timedelta(days=1)
        if mode == "naive":
            yhat = working.iloc[-1]
        elif mode == "seasonal_naive_7":
            yhat = working.iloc[-7]
        elif mode == "moving_average_7":
            yhat = working.iloc[-7:].mean()
        elif mode == "moving_average_30":
            yhat = working.iloc[-30:].mean()
        else:
            raise ValueError(f"Mode baseline tidak dikenal: {mode}")
        preds.append((target_date, yhat))
        working.loc[target_date] = yhat
    return pd.Series({d: v for d, v in preds})


def _recursive_forecast_ml(history: pd.Series, horizon: int, model_name: str) -> pd.Series:
    feat = make_features(history).dropna()
    if len(feat) < 40:
        raise ValueError("Data historis terlalu sedikit untuk melatih model ML (butuh >= ~40 hari valid).")
    model = _get_ml_model(model_name)
    model.fit(feat[FEATURE_COLS], feat["y"])

    working = history.copy()
    preds = []
    for _ in range(horizon):
        target_date = working.index[-1] + pd.Timedelta(days=1)
        row = _build_feature_row(working, target_date)
        X_row = pd.DataFrame([row])[FEATURE_COLS]
        yhat = float(model.predict(X_row)[0])
        preds.append((target_date, yhat))
        working.loc[target_date] = yhat
    return pd.Series({d: v for d, v in preds})


def _recursive_forecast_holt_winters(history: pd.Series, horizon: int) -> pd.Series:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = ExponentialSmoothing(history, trend="add", seasonal="add", seasonal_periods=7,
                                      initialization_method="estimated").fit()
        forecast = model.forecast(horizon)
    return forecast


def recursive_forecast(history: pd.Series, horizon: int, model_name: str) -> pd.Series:
    """Forecast rekursif hari-demi-hari sejauh `horizon` hari sejak akhir `history`."""
    history = history.ffill().bfill().fillna(0.0)
    if model_name in {"naive", "seasonal_naive_7", "moving_average_7", "moving_average_30"}:
        return _recursive_forecast_baseline(history, horizon, model_name)
    if model_name == "holt_winters":
        return _recursive_forecast_holt_winters(history, horizon)
    if model_name in {"random_forest", "hist_gradient_boosting"}:
        return _recursive_forecast_ml(history, horizon, model_name)
    raise ValueError(f"Model tidak dikenal: {model_name}. Pilihan: {list(config.FORECAST_MODEL_CHOICES)}")


def forecast_horizon_totals(history: pd.Series, model_name: str,
                            horizons: tuple[int, ...] = (7, 30)) -> dict:
    """Return cumulative totals from a future forecast path.

    Keeping this calculation outside the UI prevents historical actual values
    from accidentally being labelled as seven- or thirty-day forecasts.
    """
    normalized_horizons = tuple(sorted({int(h) for h in horizons}))
    if not normalized_horizons or normalized_horizons[0] <= 0:
        raise ValueError("Semua horizon forecast harus berupa integer positif.")
    if history.empty:
        raise ValueError("History kosong; forecast horizon tidak dapat dihitung.")

    path = recursive_forecast(history, max(normalized_horizons), model_name).clip(lower=0.0)
    return {
        "model_name": model_name,
        "as_of_date": pd.Timestamp(history.index.max()),
        "path": path,
        "totals": {h: float(path.iloc[:h].sum()) for h in normalized_horizons},
    }


def backtest_residual_quantiles(history: pd.Series, model_name: str,
                                 backtest_days: Optional[int] = None) -> np.ndarray:
    """Bangun residual (actual - forecast) dari backtest 1-step-ahead
    sepanjang `backtest_days` terakhir, dipakai utk prediction interval.
    """
    history = history.ffill().bfill().fillna(0.0)
    backtest_days = backtest_days or config.FORECAST_BACKTEST_DAYS
    n = len(history)
    if n < backtest_days + 40:
        backtest_days = max(10, n - 40)
    if backtest_days <= 5:
        return np.array([0.0])

    residuals = []
    cutoff_start = n - backtest_days
    for i in range(cutoff_start, n):
        train = history.iloc[:i]
        actual = history.iloc[i]
        try:
            pred = recursive_forecast(train, 1, model_name).iloc[0]
            r = actual - pred
            if not np.isnan(r):
                residuals.append(r)
        except Exception:
            continue
    return np.array(residuals) if residuals else np.array([0.0])


def climatology_forecast(history: pd.Series, target_date: pd.Timestamp,
                          window_days: Optional[int] = None) -> dict:
    """Fallback untuk horizon JAUH (mis. tanggal di tahun-tahun berikutnya):
    rata-rata historis dari hari-hari di sekitar hari-dalam-tahun yang sama
    (+/- window_days), TIDAK memakai forecast rekursif model manapun.

    Interval dibangun dari sebaran nilai historis pada jendela tsb (bukan
    dari model), sehingga otomatis lebih lebar -- mencerminkan ketidaktahuan
    yang jauh lebih besar untuk horizon ini.
    """
    history = history.ffill().bfill().fillna(0.0)
    window_days = window_days or config.FORECAST_CLIMATOLOGY_WINDOW_DAYS
    target_doy = target_date.dayofyear
    history_doy = history.index.dayofyear

    diff = np.minimum(np.abs(history_doy - target_doy), 365 - np.abs(history_doy - target_doy))
    mask = diff <= window_days
    window_values = history[mask].dropna()

    if len(window_values) < 3:
        window_values = history.dropna()  # fallback lebih jauh: rata-rata seluruh data

    point = float(window_values.mean())
    lower = float(window_values.quantile(0.1))
    upper = float(window_values.quantile(0.9))
    return {"point": point, "lower": lower, "upper": upper, "n_reference_points": len(window_values)}


def build_backtest_dataframe(history: pd.Series, model_name: str,
                              backtest_days: Optional[int] = None,
                              lower_q: float = 0.1, upper_q: float = 0.9) -> pd.DataFrame:
    """Walk-forward 1-hari-ke-depan sepanjang `backtest_days` terakhir, untuk
    MODEL YANG DIPILIH PENGGUNA. Mengembalikan DataFrame dengan skema yang
    sama seperti forecast_integration (date, actual_fuel, forecast_fuel,
    lower_interval, upper_interval, model_name) supaya grafik & metrik
    "Aktual vs Forecast" bisa dipakai ulang untuk model APAPUN yang dipilih
    dari dropdown -- bukan cuma model yang sedang diintegrasikan/dipantau.
    """
    history = history.ffill().bfill().fillna(0.0)
    backtest_days = backtest_days or config.FORECAST_BACKTEST_DAYS
    n = len(history)
    if n < backtest_days + 40:
        backtest_days = max(10, n - 40)
    if backtest_days <= 5:
        raise ValueError("Data historis terlalu sedikit untuk backtest model ini.")

    cutoff_start = n - backtest_days
    dates, actuals, forecasts = [], [], []
    for i in range(cutoff_start, n):
        train = history.iloc[:i]
        actual = history.iloc[i]
        try:
            pred = recursive_forecast(train, 1, model_name).iloc[0]
        except Exception:
            continue
        dates.append(history.index[i])
        actuals.append(actual)
        forecasts.append(pred)

    df = pd.DataFrame({"date": dates, "actual_fuel": actuals, "forecast_fuel": forecasts})
    residuals = (df["actual_fuel"] - df["forecast_fuel"]).dropna().values
    if len(residuals) == 0:
        lower_resid, upper_resid = 0.0, 0.0
    else:
        lower_resid, upper_resid = float(np.quantile(residuals, lower_q)), float(np.quantile(residuals, upper_q))
    df["lower_interval"] = df["forecast_fuel"] + lower_resid
    df["upper_interval"] = df["forecast_fuel"] + upper_resid
    df["model_name"] = config.FORECAST_MODEL_CHOICES.get(model_name, model_name)
    return df


def build_cross_year_validation(daily_full: pd.Series, cutoff_date: pd.Timestamp,
                                 model_name: str) -> pd.DataFrame:
    """Validasi TRUE out-of-sample: latih/hitung HANYA dari data sebelum
    `cutoff_date` (mis. akhir 2025), lalu prediksi maju sepanjang sisa data
    yang sudah punya nilai AKTUAL (mis. Jan-Jul 2026) -- dan bandingkan.

    Horizon dekat (<= config.FORECAST_RELIABLE_HORIZON_DAYS) memakai forecast
    rekursif model terpilih; horizon jauh memakai klimatologi -- SAMA PERSIS
    dengan logika forecast_for_date(), tapi di sini dites terhadap data
    aktual sungguhan yang sudah terjadi, bukan cuma titik di masa depan.
    """
    history = daily_full.loc[:cutoff_date].ffill().bfill().fillna(0.0)
    actual_future = daily_full.loc[cutoff_date + pd.Timedelta(days=1):].fillna(0.0)
    if len(actual_future) == 0:
        raise ValueError("Tidak ada data aktual setelah cutoff_date untuk divalidasi.")

    horizon_total = len(actual_future)
    reliable_horizon = min(horizon_total, config.FORECAST_RELIABLE_HORIZON_DAYS)

    rows = []
    if reliable_horizon > 0:
        recursive_path = recursive_forecast(history, reliable_horizon, model_name)
        for date in recursive_path.index:
            if date in actual_future.index:
                rows.append({"date": date, "actual": float(actual_future.loc[date]),
                            "forecast": float(recursive_path.loc[date]), "method": "recursive_forecast"})

    remaining_dates = actual_future.index[reliable_horizon:]
    for date in remaining_dates:
        clim = climatology_forecast(history, date)
        rows.append({"date": date, "actual": float(actual_future.loc[date]),
                    "forecast": clim["point"], "method": "climatology_fallback"})

    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    df["residual"] = df["actual"] - df["forecast"]
    df["abs_pct_error"] = (df["residual"].abs() / df["actual"].replace(0, np.nan) * 100)
    return df


def forecast_for_date(history: pd.Series, model_name: str, target_date: pd.Timestamp,
                       lower_q: float = 0.1, upper_q: float = 0.9) -> dict:
    """Entry point utama Forecast Explorer: prediksi utk SATU tanggal
    target, otomatis memilih rezim (rekursif vs klimatologi) berdasarkan
    seberapa jauh target_date dari data historis terakhir.
    """
    history = history.ffill().bfill().fillna(0.0)
    last_date = history.index.max()
    horizon_days = (target_date - last_date).days

    if horizon_days <= 0:
        actual = history.get(target_date)
        return {
            "method": "historical_actual", "point": float(actual) if actual is not None and not pd.isna(actual) else None,
            "lower": None, "upper": None, "horizon_days": horizon_days,
            "warning": "Tanggal yang diminta ada di dalam rentang data historis -- ini nilai AKTUAL, bukan prediksi."
                       if actual is not None and not pd.isna(actual) else
                       "Tanggal ada di rentang historis tapi tidak ada data tercatat untuk hari tsb.",
        }

    if horizon_days <= config.FORECAST_RELIABLE_HORIZON_DAYS:
        path = recursive_forecast(history, horizon_days, model_name)
        point = float(path.iloc[-1])
        residuals = backtest_residual_quantiles(history, model_name)
        lower_resid = float(np.quantile(residuals, lower_q)) if len(residuals) > 0 else 0.0
        upper_resid = float(np.quantile(residuals, upper_q)) if len(residuals) > 0 else 0.0
        lower = point + lower_resid
        upper = point + upper_resid
        return {
            "method": "recursive_forecast", "model_name": model_name, "point": point,
            "lower": lower, "upper": upper, "horizon_days": horizon_days, "path": path,
            "warning": None if horizon_days <= 60 else
                       f"Horizon {horizon_days} hari cukup jauh -- akurasi rekursif menurun seiring "
                       f"bertambahnya horizon (lihat Bagian 8 notebook forecasting).",
        }

    # Horizon jauh: fallback klimatologi, model_name yang dipilih pengguna DIABAIKAN dgn sengaja
    clim = climatology_forecast(history, target_date)
    return {
        "method": "climatology_fallback", "model_name": None, "point": clim["point"],
        "lower": clim["lower"], "upper": clim["upper"], "horizon_days": horizon_days,
        "n_reference_points": clim["n_reference_points"],
        "warning": (
            f"Tanggal ini {horizon_days} hari dari data terakhir -- melebihi ambang "
            f"{config.FORECAST_RELIABLE_HORIZON_DAYS} hari yang dianggap masih punya dasar rekursif. "
            f"Prediksi memakai RATA-RATA HISTORIS pada tanggal yang sama tahun-tahun sebelumnya "
            f"(klimatologi), BUKAN model {config.FORECAST_MODEL_CHOICES.get(model_name, model_name)} yang dipilih. "
            f"Karena data historis hanya mencakup 1 tahun, pola musiman ANTAR TAHUN belum benar-benar "
            f"bisa dipelajari -- perlakukan angka ini sebagai perkiraan kasar, bukan prediksi presisi."
        ),
    }

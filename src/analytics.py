"""
analytics.py
============
Lapisan orkestrasi yang menjalankan SELURUH pipeline (parsing -> cleaning ->
anomaly -> change-point -> health score -> clustering -> forecast monitoring
-> recommendations) SATU KALI per sesi Streamlit, di-cache dengan
`st.cache_data` / `st.cache_resource` supaya tiap halaman dashboard tidak
menjalankan ulang komputasi berat yang sama.

Semua halaman di folder pages/ mengambil data lewat fungsi-fungsi di sini,
bukan memanggil src/* secara langsung -- satu sumber kebenaran, satu cache.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config
from src.data_cleaning import run_cleaning_pipeline, CleaningResult
from src.ujb_source import NoUjbDataError
from src.data_quality import compute_dq_kpis, detect_missing_dates, detect_zero_consumption_streaks
from src.forecast_data import build_daily_refueling_series
from src.forecast_integration import (
    load_forecast_results, compute_forecast_errors, compute_overall_metrics,
    compute_rolling_performance, detect_model_drift_warning,
)
from src.forecast_evaluation import (
    DEFAULT_EVAL_HORIZONS,
    build_multi_horizon_backtest as _build_multi_horizon_backtest,
    summarize_multi_horizon_backtest as _summarize_multi_horizon_backtest,
    residual_quantiles_by_horizon as _residual_quantiles_by_horizon,
)
from src.anomaly_detection import detect_anomalies, summarize_anomalies
from src.change_point import detect_all_change_points, summarize_change_points
from src.health_score import build_health_score_table
from src.clustering import cluster_all_equipment, summarize_clusters
from src.saving_simulator import SavingSimulatorInputs, run_saving_scenarios, calculate_l_per_teu
from src.recommendation_engine import build_recommendations, summarize_recommendations
from src.forecasting_models import forecast_for_date as _forecast_for_date
from src.forecasting_models import build_backtest_dataframe as _build_backtest_dataframe
from src.forecasting_models import build_cross_year_validation as _build_cross_year_validation


@st.cache_data(show_spinner="Memuat data hybrid Excel + UJB...")
def get_cleaning_result() -> dict:
    """Jalankan source reconciliation + cleaning dan bungkus hasil sebagai dict.

    Sumber data ditentukan ``config.DATA_SOURCE_MODE`` (default ``hybrid``).
    Audit source precedence ikut diekspos agar halaman Data Quality dapat
    menunjukkan row/liter mana yang dipakai atau disuppress dari tiap sumber.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            result = run_cleaning_pipeline()
        except NoUjbDataError as e:
            st.error(
                "**Belum ada data untuk ditampilkan.**\n\n"
                f"{e}"
            )
            st.stop()

    return {
        "cleaned_fuel_data": result.cleaned_fuel_data,
        "equipment_master": result.equipment_master,
        "data_quality_report": result.data_quality_report,
        "monthly_reconciliation": result.monthly_reconciliation,
        "category_monthly_reconciliation": result.category_monthly_reconciliation,
        "totalisator_df": result.totalisator_df,
        "source_reconciliation_audit": result.source_reconciliation_audit,
    }


@st.cache_data(show_spinner="Menghitung KPI kualitas data...")
def get_data_quality() -> dict:
    cleaning = get_cleaning_result()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        zero_streaks = detect_zero_consumption_streaks(cleaning["cleaned_fuel_data"])
        missing_dates = detect_missing_dates(cleaning["cleaned_fuel_data"])
        kpis = compute_dq_kpis(cleaning["cleaned_fuel_data"], cleaning["monthly_reconciliation"], zero_streaks)
    return {"kpis": kpis, "zero_streaks": zero_streaks, "missing_dates": missing_dates}


@st.cache_data(show_spinner="Memuat & memantau hasil forecasting...")
def get_forecast_monitoring() -> dict:
    cleaning = get_cleaning_result()
    daily_actual = build_daily_refueling_series(cleaning["cleaned_fuel_data"], strict_source_coverage=True)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        forecast_df = load_forecast_results(daily_actual_fallback=daily_actual)
        df_err = compute_forecast_errors(forecast_df)
        summary = compute_overall_metrics(df_err)
        rolling_perf = compute_rolling_performance(df_err)
        drift_warning = detect_model_drift_warning(rolling_perf)

    is_placeholder = summary.model_name == config.FORECAST_PLACEHOLDER_MODEL_NAME
    return {"forecast_df": df_err, "summary": summary, "rolling_perf": rolling_perf,
            "drift_warning": drift_warning, "is_placeholder": is_placeholder, "daily_actual": daily_actual}


@st.cache_data(show_spinner="Mendeteksi anomali konsumsi solar...")
def get_anomalies() -> pd.DataFrame:
    cleaning = get_cleaning_result()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return detect_anomalies(cleaning["cleaned_fuel_data"])


@st.cache_data(show_spinner="Mendeteksi perubahan pola konsumsi...")
def get_change_points() -> pd.DataFrame:
    cleaning = get_cleaning_result()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return detect_all_change_points(cleaning["cleaned_fuel_data"])


@st.cache_data(show_spinner="Menghitung fuel consumption health score...")
def get_health_scores() -> pd.DataFrame:
    cleaning = get_cleaning_result()
    anomaly_df = get_anomalies()
    change_points = get_change_points()
    dq = get_data_quality()
    forecast = get_forecast_monitoring()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return build_health_score_table(
            cleaning["cleaned_fuel_data"], anomaly_df, change_points, dq["zero_streaks"],
            cleaning["category_monthly_reconciliation"], forecast["summary"].__dict__,
        )


@st.cache_data(show_spinner="Menyegmentasi equipment...")
def get_clusters() -> pd.DataFrame:
    cleaning = get_cleaning_result()
    anomaly_df = get_anomalies()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return cluster_all_equipment(cleaning["cleaned_fuel_data"], anomaly_df)


def get_saving_scenarios(fuel_price_per_liter: Optional[float] = None,
                          saving_target_percentage: Optional[float] = None,
                          saving_target_liter: Optional[float] = None,
                          target_throughput_teu: Optional[float] = None,
                          actual_teu: Optional[float] = None) -> dict:
    cleaning = get_cleaning_result()
    valid = cleaning["cleaned_fuel_data"][cleaning["cleaned_fuel_data"]["data_status"] != "INVALID_DATE"]
    baseline_total = float(valid["fuel_liter"].sum())

    inputs = SavingSimulatorInputs(
        baseline_total_liter=baseline_total,
        fuel_price_per_liter=fuel_price_per_liter or config.DEFAULT_FUEL_PRICE_PER_LITER,
        saving_target_percentage=(saving_target_percentage if saving_target_percentage is not None
                                   else config.DEFAULT_SAVING_TARGET_PCT),
        saving_target_liter=saving_target_liter,
        target_throughput_teu=target_throughput_teu or config.DEFAULT_TARGET_THROUGHPUT_TEU,
        actual_teu=actual_teu,
    )
    scenarios_df = run_saving_scenarios(inputs)
    l_per_teu_info = calculate_l_per_teu(baseline_total, actual_teu)
    return {"inputs": inputs, "scenarios": scenarios_df, "l_per_teu_info": l_per_teu_info,
            "baseline_total": baseline_total}


@st.cache_data(show_spinner="Menyusun rekomendasi...")
def get_recommendations() -> pd.DataFrame:
    cleaning = get_cleaning_result()
    health_scores = get_health_scores()
    scenarios = get_saving_scenarios()["scenarios"]
    return build_recommendations(health_scores, cleaning["category_monthly_reconciliation"], scenarios)


@st.cache_data(show_spinner=False)
def get_daily_actual_series() -> pd.Series:
    cleaning = get_cleaning_result()
    return build_daily_refueling_series(cleaning["cleaned_fuel_data"], strict_source_coverage=True)


@st.cache_data(show_spinner=False)
def get_forecast_training_series() -> pd.Series:
    import pandas as pd
    full = get_daily_actual_series()
    return full.loc[:pd.Timestamp(config.FORECAST_TRAINING_CUTOFF)]


def get_forecast_for_date(model_name: str, target_date) -> dict:
    import pandas as pd
    training_series = get_forecast_training_series()
    return _forecast_for_date(training_series, model_name, pd.Timestamp(target_date))


@st.cache_data(show_spinner="Menjalankan backtest untuk model terpilih...")
def get_model_backtest(model_name: str, backtest_days: int = 60) -> dict:
    training_series = get_forecast_training_series()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = _build_backtest_dataframe(training_series, model_name, backtest_days)
        df_err = compute_forecast_errors(df)
        summary = compute_overall_metrics(df_err)
        rolling_perf = compute_rolling_performance(df_err)
        drift_warning = detect_model_drift_warning(rolling_perf)
    return {"forecast_df": df_err, "summary": summary, "rolling_perf": rolling_perf,
            "drift_warning": drift_warning}


@st.cache_data(show_spinner="Menjalankan evaluasi multi-horizon...")
def get_multi_horizon_backtest(model_name: str,
                               horizons: tuple[int, ...] = DEFAULT_EVAL_HORIZONS,
                               evaluation_days: int = 180,
                               origin_step_days: int = 7) -> dict:
    training_series = get_forecast_training_series()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = _build_multi_horizon_backtest(
            training_series, model_name,
            horizons=horizons,
            evaluation_days=evaluation_days,
            origin_step_days=origin_step_days,
        )
        summary = _summarize_multi_horizon_backtest(df)
        residual_quantiles = _residual_quantiles_by_horizon(df)
    return {"backtest_df": df, "summary": summary, "residual_quantiles": residual_quantiles}


@st.cache_data(show_spinner="Menjalankan validasi lintas tahun...")
def get_cross_year_validation(model_name: str, cutoff_date_str: str) -> pd.DataFrame:
    import pandas as pd
    daily_actual = get_daily_actual_series()
    cutoff = pd.Timestamp(cutoff_date_str)
    if daily_actual.index.max() <= cutoff:
        return pd.DataFrame()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return _build_cross_year_validation(daily_actual, cutoff, model_name)


def get_available_years() -> list:
    daily_actual = get_daily_actual_series()
    return sorted(daily_actual.index.year.unique().tolist())


def get_all_data() -> dict:
    return {
        "cleaning": get_cleaning_result(),
        "data_quality": get_data_quality(),
        "forecast": get_forecast_monitoring(),
        "anomalies": get_anomalies(),
        "change_points": get_change_points(),
        "health_scores": get_health_scores(),
        "clusters": get_clusters(),
        "recommendations": get_recommendations(),
    }

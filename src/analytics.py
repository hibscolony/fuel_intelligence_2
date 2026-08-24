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
from src.data_cleaning import run_cleaning_pipeline
from src.ujb_source import NoUjbDataError
from src.data_quality import compute_dq_kpis, detect_missing_dates, detect_zero_consumption_streaks
from src.forecast_data import (
    build_daily_refueling_series,
    build_forecast_calendar_audit,
    build_forecast_coverage_segments,
    select_complete_daily_segment,
)
from src.forecast_integration import (
    load_forecast_results, compute_forecast_errors, compute_overall_metrics,
    compute_rolling_performance, detect_model_drift_warning,
)
from src.forecast_evaluation import (
    DEFAULT_EVAL_HORIZONS,
    build_multi_horizon_backtest as _build_multi_horizon_backtest,
    summarize_multi_horizon_backtest as _summarize_multi_horizon_backtest,
    residual_quantiles_by_horizon as _residual_quantiles_by_horizon,
    apply_horizon_prediction_interval as _apply_horizon_prediction_interval,
    rank_model_horizon_summaries as _rank_model_horizon_summaries,
)
from src.anomaly_detection import detect_anomalies
from src.change_point import detect_all_change_points
from src.health_score import build_health_score_table
from src.clustering import cluster_all_equipment
from src.saving_simulator import SavingSimulatorInputs, run_saving_scenarios, calculate_l_per_teu
from src.recommendation_engine import build_recommendations
from src.forecasting_models import forecast_for_date as _forecast_for_date
from src.forecasting_models import build_backtest_dataframe as _build_backtest_dataframe
from src.forecasting_models import build_cross_year_validation as _build_cross_year_validation


@st.cache_data(show_spinner="Memuat data hybrid Excel + UJB...")
def get_cleaning_result() -> dict:
    """Jalankan source reconciliation + cleaning dan bungkus hasil sebagai dict."""
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


@st.cache_data(show_spinner=False)
def get_daily_actual_series() -> pd.Series:
    """Deret kalender penuh; coverage gap dipertahankan sebagai NaN."""
    cleaning = get_cleaning_result()
    return build_daily_refueling_series(
        cleaning["cleaned_fuel_data"], strict_source_coverage=False
    )


@st.cache_data(show_spinner=False)
def get_forecast_coverage() -> dict:
    """Audit + segmentasi coverage tanpa mengimputasi tanggal yang tidak diketahui."""
    cleaning = get_cleaning_result()
    cleaned = cleaning["cleaned_fuel_data"]
    full = build_daily_refueling_series(cleaned, strict_source_coverage=False)
    audit = build_forecast_calendar_audit(cleaned)
    segments = build_forecast_coverage_segments(cleaned, model_ready_min_days=30)
    gap_rows = audit[audit["calendar_status"] == "SOURCE_COVERAGE_GAP"].copy()

    latest_segment = select_complete_daily_segment(full, latest=True)
    latest_meta = None
    if not segments.empty:
        latest_meta = segments.loc[segments["is_latest"]].iloc[-1].to_dict()

    return {
        "full_series": full,
        "calendar_audit": audit,
        "segments": segments,
        "gap_days": int(len(gap_rows)),
        "gap_start": None if gap_rows.empty else pd.Timestamp(gap_rows["date"].min()),
        "gap_end": None if gap_rows.empty else pd.Timestamp(gap_rows["date"].max()),
        "latest_segment": latest_segment,
        "latest_segment_meta": latest_meta,
    }


@st.cache_data(show_spinner=False)
def get_forecast_training_series() -> pd.Series:
    """Pilih segmen lengkap terakhir sampai cutoff; jangan menyeberangi coverage gap."""
    full = get_daily_actual_series()
    cutoff = pd.Timestamp(config.FORECAST_TRAINING_CUTOFF)
    return select_complete_daily_segment(full, end_at=cutoff, min_days=60)


@st.cache_data(show_spinner=False)
def get_latest_operational_series() -> pd.Series:
    """Segmen coverage terbaru, biasanya UJB realtime setelah gap sumber."""
    full = get_daily_actual_series()
    return select_complete_daily_segment(full, latest=True, min_days=1)


@st.cache_data(show_spinner="Memuat & memantau hasil forecasting...")
def get_forecast_monitoring() -> dict:
    """Monitoring tanpa memaksa segmen historis dan UJB menjadi satu deret palsu."""
    training_actual = get_forecast_training_series()
    recent_actual = get_latest_operational_series()
    coverage = get_forecast_coverage()

    placeholder_actual = recent_actual if len(recent_actual) >= 14 else training_actual

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        forecast_df = load_forecast_results(daily_actual_fallback=placeholder_actual)
        df_err = compute_forecast_errors(forecast_df)
        summary = compute_overall_metrics(df_err)
        rolling_perf = compute_rolling_performance(df_err)
        drift_warning = detect_model_drift_warning(rolling_perf)

    is_placeholder = summary.model_name == config.FORECAST_PLACEHOLDER_MODEL_NAME
    return {
        "forecast_df": df_err,
        "summary": summary,
        "rolling_perf": rolling_perf,
        "drift_warning": drift_warning,
        "is_placeholder": is_placeholder,
        "daily_actual": placeholder_actual,
        "recent_operational_actual": recent_actual,
        "coverage": coverage,
    }


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


def get_forecast_for_date(model_name: str, target_date) -> dict:
    """Forecast explorer dengan empirical interval yang sesuai horizon."""
    training_series = get_forecast_training_series()
    target_ts = pd.Timestamp(target_date)
    result = _forecast_for_date(training_series, model_name, target_ts)

    if result.get("method") != "recursive_forecast":
        return result

    if "path" in result and isinstance(result["path"], pd.Series):
        result["path"] = result["path"].clip(lower=0.0)
        if len(result["path"]):
            result["point"] = float(result["path"].iloc[-1])

    horizon_days = int(result.get("horizon_days", 0))
    if horizon_days <= 0:
        return result

    try:
        multi = get_multi_horizon_backtest(
            model_name,
            horizons=DEFAULT_EVAL_HORIZONS,
            evaluation_days=180,
            origin_step_days=14,
        )
        interval = _apply_horizon_prediction_interval(
            result["point"], horizon_days, multi["residual_quantiles"], nonnegative=True
        )
        result.update(interval)

        note = (
            f"Prediction interval memakai residual rolling-origin D+"
            f"{interval['interval_calibration_horizon']} "
            f"({interval['interval_n_residuals']} residual)."
        )
        if interval["interval_extrapolated"]:
            note += (
                " Target melewati horizon kalibrasi maksimum; interval memakai D+90 "
                "sebagai fallback dan harus dianggap extrapolated."
            )
        note += " Kalibrasi residual belum memakai calibration set independen."
        existing = result.get("warning")
        result["warning"] = f"{existing} {note}".strip() if existing else note
    except Exception as exc:
        result["interval_method"] = "legacy_d1_fallback"
        result["interval_calibration_error"] = f"{type(exc).__name__}: {exc}"
        existing = result.get("warning")
        fallback_note = (
            "Interval multi-horizon belum dapat dikalibrasi; sementara memakai interval legacy D+1."
        )
        result["warning"] = f"{existing} {fallback_note}".strip() if existing else fallback_note

    return result


@st.cache_data(show_spinner="Membandingkan semua model per horizon...")
def get_model_horizon_leaderboard(
    horizons: tuple[int, ...] = DEFAULT_EVAL_HORIZONS,
    evaluation_days: int = 180,
    origin_step_days: int = 30,
) -> dict:
    """Bandingkan semua model pada origin/horizon yang sama, lalu rank per horizon."""
    training_series = get_forecast_training_series()
    summary_frames: list[pd.DataFrame] = []
    errors: list[dict] = []

    for model_name in config.FORECAST_MODEL_CHOICES.keys():
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                bt = _build_multi_horizon_backtest(
                    training_series,
                    model_name,
                    horizons=horizons,
                    evaluation_days=evaluation_days,
                    origin_step_days=origin_step_days,
                )
                summary = _summarize_multi_horizon_backtest(bt)
            if summary.empty:
                errors.append({"model_name": model_name, "error": "No successful forecasts"})
                continue
            summary = summary.copy()
            summary["model_name"] = model_name
            summary["model_label"] = config.FORECAST_MODEL_CHOICES.get(model_name, model_name)
            summary_frames.append(summary)
        except Exception as exc:
            errors.append({"model_name": model_name, "error": f"{type(exc).__name__}: {exc}"})

    if summary_frames:
        combined = pd.concat(summary_frames, ignore_index=True, sort=False)
        leaderboard = _rank_model_horizon_summaries(combined)
        if "model_label" not in leaderboard.columns:
            leaderboard["model_label"] = leaderboard["model_name"].map(config.FORECAST_MODEL_CHOICES)
        winners = leaderboard[leaderboard["best_for_horizon"]].copy()
    else:
        combined = pd.DataFrame()
        leaderboard = pd.DataFrame()
        winners = pd.DataFrame()

    return {
        "leaderboard": leaderboard,
        "winners": winners,
        "summary": combined,
        "errors": pd.DataFrame(errors),
        "evaluation_days": int(evaluation_days),
        "origin_step_days": int(origin_step_days),
    }


@st.cache_data(show_spinner="Menjalankan validasi lintas tahun...")
def get_cross_year_validation(model_name: str, cutoff_date_str: str) -> pd.DataFrame:
    """Validasi hanya di segmen kontigu yang mencakup cutoff; berhenti sebelum gap."""
    full = get_daily_actual_series()
    cutoff = pd.Timestamp(cutoff_date_str)
    try:
        contiguous = select_complete_daily_segment(full, containing_date=cutoff)
    except ValueError:
        return pd.DataFrame()
    if contiguous.index.max() <= cutoff:
        return pd.DataFrame()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return _build_cross_year_validation(contiguous, cutoff, model_name)


def get_available_years() -> list:
    daily_actual = get_daily_actual_series().dropna()
    return sorted(daily_actual.index.year.unique().tolist())


def get_all_data() -> dict:
    return {
        "cleaning": get_cleaning_result(),
        "data_quality": get_data_quality(),
        "forecast": get_forecast_monitoring(),
        "forecast_coverage": get_forecast_coverage(),
        "anomalies": get_anomalies(),
        "change_points": get_change_points(),
        "health_scores": get_health_scores(),
        "clusters": get_clusters(),
        "recommendations": get_recommendations(),
    }

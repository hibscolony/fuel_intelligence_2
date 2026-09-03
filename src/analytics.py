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
    compute_rolling_performance, detect_model_drift_warning, summarize_model_drift,
)
from src.forecast_evaluation import (
    DEFAULT_EVAL_HORIZONS,
    build_multi_horizon_backtest as _build_multi_horizon_backtest,
    summarize_multi_horizon_backtest as _summarize_multi_horizon_backtest,
    build_independent_interval_evaluation as _build_independent_interval_evaluation,
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
from src.forecasting_models import forecast_horizon_totals as _forecast_horizon_totals
from src.forecasting_models import build_backtest_dataframe as _build_backtest_dataframe
from src.forecasting_models import build_cross_year_validation as _build_cross_year_validation
from src.reporting import default_reporting_year, select_reporting_period
from src.forecast_deployment import assess_production_readiness, build_candidate_registry
from src.persistent_cache import get_metadata, load_artifact, save_artifact


@st.cache_data(show_spinner="Memuat data hybrid Excel + UJB...")
def get_cleaning_result() -> dict:
    """Jalankan source reconciliation + cleaning dan bungkus hasil sebagai dict."""
    fingerprint = config.get_raw_data_fingerprint()
    cached = load_artifact("cleaning_result", fingerprint)
    if cached is not None:
        return cached

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

    payload = {
        "cleaned_fuel_data": result.cleaned_fuel_data,
        "equipment_master": result.equipment_master,
        "data_quality_report": result.data_quality_report,
        "monthly_reconciliation": result.monthly_reconciliation,
        "category_monthly_reconciliation": result.category_monthly_reconciliation,
        "totalisator_df": result.totalisator_df,
        "source_reconciliation_audit": result.source_reconciliation_audit,
        "source_coverage_calendar": result.source_coverage_calendar,
    }
    save_artifact("cleaning_result", payload, fingerprint)
    return payload


@st.cache_data(show_spinner="Menghitung KPI kualitas data...")
def get_data_quality() -> dict:
    cleaning = get_cleaning_result()
    source_calendar = cleaning["source_coverage_calendar"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        zero_streaks = detect_zero_consumption_streaks(
            cleaning["cleaned_fuel_data"], source_coverage_calendar=source_calendar
        )
        missing_dates = detect_missing_dates(
            cleaning["cleaned_fuel_data"], source_coverage_calendar=source_calendar
        )
        kpis = compute_dq_kpis(
            cleaning["cleaned_fuel_data"], cleaning["monthly_reconciliation"], zero_streaks,
            source_coverage_calendar=source_calendar,
        )
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
    """Bangun deret training forecast KHUSUS dari sumber Excel saja.

    UJB sengaja TIDAK diikutsertakan di sini karena:
    - UJB adalah event stream (bisa banyak pengisian per hari per unit)
      yang pola & distribusinya berbeda dari laporan Excel bulanan.
    - Mencampur dua sumber dalam satu deret lag/rolling bisa membuat
      model belajar pola yang tidak konsisten.
    - Fitur analitik lain (anomaly, health score, dll) tetap memakai
      hybrid Excel+UJB melalui get_daily_actual_series().

    Filtered dari cleaned_fuel_data (bukan dari daily_actual_series hybrid)
    sehingga eksplisit dan tidak bergantung pada cutoff date kebetulan
    sebelum UJB masuk.
    """
    cleaning = get_cleaning_result()
    excel_only = cleaning["cleaned_fuel_data"][
        cleaning["cleaned_fuel_data"]["source_system"].astype(str).str.upper().ne("UJB")
    ].copy()
    full = build_daily_refueling_series(excel_only, strict_source_coverage=False)
    cutoff = pd.Timestamp(config.FORECAST_TRAINING_CUTOFF)
    return select_complete_daily_segment(full, end_at=cutoff, min_days=60)


@st.cache_data(show_spinner=False)
def get_latest_operational_series() -> pd.Series:
    """Segmen coverage terbaru, biasanya UJB realtime setelah gap sumber."""
    full = get_daily_actual_series()
    return select_complete_daily_segment(full, latest=True, min_days=1)


@st.cache_data(show_spinner=False)
def get_forecast_production_readiness() -> dict:
    """Evaluate data freshness/continuity before any model promotion."""
    training = get_forecast_training_series()
    latest = get_latest_operational_series()
    coverage = get_forecast_coverage()
    gate = assess_production_readiness(
        training_end=training.index.max(),
        latest_actual_date=latest.index.max(),
        latest_segment_days=len(latest),
        source_gap_days=coverage["gap_days"],
        max_staleness_days=config.FORECAST_PRODUCTION_MAX_STALENESS_DAYS,
        min_latest_segment_days=config.FORECAST_PRODUCTION_MIN_LATEST_SEGMENT_DAYS,
    )
    return gate.to_dict()


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
        drift_status = summarize_model_drift(rolling_perf)
        drift_warning = detect_model_drift_warning(rolling_perf)

    is_placeholder = summary.model_name == config.FORECAST_PLACEHOLDER_MODEL_NAME
    return {
        "forecast_df": df_err,
        "summary": summary,
        "rolling_perf": rolling_perf,
        "drift_status": drift_status,
        "drift_warning": drift_warning,
        "is_placeholder": is_placeholder,
        "daily_actual": placeholder_actual,
        "recent_operational_actual": recent_actual,
        "coverage": coverage,
    }


@st.cache_data(show_spinner="Menghitung forecast operasional 7/30 hari...")
def get_executive_forecast() -> dict:
    """Build honest future totals for Executive Overview.

    This is an explicitly labelled seasonal-naive operational baseline.  It is
    separate from production-model monitoring and never sums historical actual
    values under a forecast label.
    """
    recent_actual = get_latest_operational_series()
    if len(recent_actual) >= 7:
        history = recent_actual
        source = "latest_operational_segment"
    else:
        history = get_forecast_training_series()
        source = "training_segment_fallback"

    result = _forecast_horizon_totals(
        history,
        model_name="seasonal_naive_7",
        horizons=(7, 30),
    )
    result["source"] = source
    result["is_baseline_model"] = True
    result["history_days"] = int(len(history))
    result["readiness_status"] = "READY" if len(history) >= 60 else "LIMITED"
    if result["readiness_status"] == "LIMITED":
        result["readiness_warning"] = (
            f"Forecast memakai segmen operasional yang baru {len(history)} hari. "
            "Gunakan sebagai baseline sementara, bukan target komitmen."
        )
    return result


@st.cache_data(show_spinner="Mendeteksi anomali konsumsi solar...")
def get_anomalies() -> pd.DataFrame:
    fingerprint = config.get_raw_data_fingerprint()
    cached = load_artifact("anomalies", fingerprint)
    if cached is not None:
        return cached
    cleaning = get_cleaning_result()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = detect_anomalies(cleaning["cleaned_fuel_data"])
    save_artifact("anomalies", result, fingerprint)
    return result


@st.cache_data(show_spinner="Mendeteksi perubahan pola konsumsi...")
def get_change_points() -> pd.DataFrame:
    fingerprint = config.get_raw_data_fingerprint()
    cached = load_artifact("change_points", fingerprint)
    if cached is not None:
        return cached
    cleaning = get_cleaning_result()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = detect_all_change_points(cleaning["cleaned_fuel_data"])
    save_artifact("change_points", result, fingerprint)
    return result


@st.cache_data(show_spinner=False)
def get_data_freshness() -> dict:
    """Summarize the effective date of the data currently shown."""
    cleaned = get_cleaning_result()["cleaned_fuel_data"]
    valid_dates = pd.to_datetime(cleaned.get("date"), errors="coerce").dropna()
    data_as_of = valid_dates.max() if not valid_dates.empty else None
    today = pd.Timestamp.now().normalize()
    lag_days = None if data_as_of is None else max(0, int((today - data_as_of.normalize()).days))
    if lag_days is None:
        status, severity = "TANGGAL TIDAK TERSEDIA", "danger"
    elif lag_days <= 1:
        status, severity = "TERKINI", "success"
    elif lag_days <= 7:
        status, severity = "PERLU DIPERBARUI", "warning"
    else:
        status, severity = "DATA TERTINGGAL", "danger"

    source_series = cleaned.get("source_system", pd.Series(dtype=str))
    sources = sorted(value for value in source_series.dropna().astype(str).unique() if value)
    fingerprint = config.get_raw_data_fingerprint()
    metadata = get_metadata("cleaning_result", fingerprint) or {}
    return {
        "data_as_of": data_as_of,
        "lag_days": lag_days,
        "status": status,
        "severity": severity,
        "sources": sources,
        "snapshot_generated_at": metadata.get("generated_at"),
    }


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
                          actual_teu: Optional[float] = None,
                          target_liter_per_teu: Optional[float] = None,
                          reporting_year: Optional[int] = None) -> dict:
    cleaning = get_cleaning_result()
    cleaned = cleaning["cleaned_fuel_data"]
    selected_year = int(reporting_year) if reporting_year is not None else default_reporting_year(cleaned)
    reporting_period = select_reporting_period(cleaned, selected_year)
    baseline_total = reporting_period.total_liter

    selected_fuel_price = (
        fuel_price_per_liter if fuel_price_per_liter is not None
        else config.DEFAULT_FUEL_PRICE_PER_LITER
    )
    selected_throughput = (
        target_throughput_teu if target_throughput_teu is not None
        else config.DEFAULT_TARGET_THROUGHPUT_TEU
    )
    selected_l_per_teu = (
        target_liter_per_teu if target_liter_per_teu is not None
        else config.DEFAULT_TARGET_L_PER_TEU
    )

    inputs = SavingSimulatorInputs(
        baseline_total_liter=baseline_total,
        fuel_price_per_liter=selected_fuel_price,
        saving_target_percentage=(saving_target_percentage if saving_target_percentage is not None
                                   else config.DEFAULT_SAVING_TARGET_PCT),
        saving_target_liter=saving_target_liter,
        target_throughput_teu=selected_throughput,
        target_liter_per_teu=selected_l_per_teu,
        actual_teu=actual_teu,
        planning_days=reporting_period.n_calendar_days,
    )
    scenarios_df = run_saving_scenarios(inputs)
    l_per_teu_info = calculate_l_per_teu(
        baseline_total,
        actual_teu,
        target_liter_per_teu=selected_l_per_teu,
        target_throughput_teu=selected_throughput,
    )
    return {"inputs": inputs, "scenarios": scenarios_df, "l_per_teu_info": l_per_teu_info,
            "baseline_total": baseline_total, "reporting_period": reporting_period}


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
        drift_status = summarize_model_drift(rolling_perf)
        drift_warning = detect_model_drift_warning(rolling_perf)
    return {"forecast_df": df_err, "summary": summary, "rolling_perf": rolling_perf,
            "drift_warning": drift_warning, "drift_status": drift_status}


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
        backtest_summary = _summarize_multi_horizon_backtest(df)
        interval_validation = _build_independent_interval_evaluation(
            df,
            calibration_fraction=config.FORECAST_CALIBRATION_FRACTION,
            lower_q=config.FORECAST_INTERVAL_LOWER_Q,
            upper_q=config.FORECAST_INTERVAL_UPPER_Q,
            min_calibration_origins=config.FORECAST_MIN_CALIBRATION_ORIGINS,
            min_evaluation_origins=config.FORECAST_MIN_EVALUATION_ORIGINS,
            max_coverage_gap_pct=config.FORECAST_MAX_INTERVAL_COVERAGE_GAP_PCT,
        )
    payload = {
        "backtest_df": df,
        "summary": interval_validation["point_summary"],
        "backtest_summary": backtest_summary,
        "residual_quantiles": interval_validation["residual_quantiles"],
        "interval_validation": interval_validation,
    }
    return payload


@st.cache_data(show_spinner="Menghitung prediksi...")
def get_forecast_for_date(model_name: str, target_date: str) -> dict:
    """Forecast explorer dengan empirical interval yang sesuai horizon.

    Di-cache per (model_name, target_date). target_date harus berupa string
    (mis. '2026-09-30') agar cache key konsisten dan hashable.
    """
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
        interval["interval_calibration_independent"] = True
        interval["forecast_readiness_status"] = (
            "READY"
            if interval["interval_n_residuals"] >= config.FORECAST_MIN_CALIBRATION_ORIGINS
            and not interval["interval_extrapolated"]
            else "LIMITED"
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
        note += " Kalibrasi memakai origin lebih awal dan diuji pada holdout origin yang lebih baru."
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
    origin_step_days: int = 14,
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
                validation = _build_independent_interval_evaluation(
                    bt,
                    calibration_fraction=config.FORECAST_CALIBRATION_FRACTION,
                    lower_q=config.FORECAST_INTERVAL_LOWER_Q,
                    upper_q=config.FORECAST_INTERVAL_UPPER_Q,
                    min_calibration_origins=config.FORECAST_MIN_CALIBRATION_ORIGINS,
                    min_evaluation_origins=config.FORECAST_MIN_EVALUATION_ORIGINS,
                    max_coverage_gap_pct=config.FORECAST_MAX_INTERVAL_COVERAGE_GAP_PCT,
                )
                interval_summary = validation["interval_summary"].rename(columns={
                    "readiness_status": "interval_readiness_status",
                })
                summary = validation["point_summary"].merge(
                    interval_summary,
                    on="horizon_days",
                    how="left",
                    validate="one_to_one",
                )
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
        leaderboard = _rank_model_horizon_summaries(
            combined, min_forecasts=config.FORECAST_MIN_MODEL_SELECTION_ORIGINS
        )
        if "model_label" not in leaderboard.columns:
            leaderboard["model_label"] = leaderboard["model_name"].map(config.FORECAST_MODEL_CHOICES)
        winners = leaderboard[leaderboard["best_for_horizon"]].copy()
    else:
        combined = pd.DataFrame()
        leaderboard = pd.DataFrame()
        winners = pd.DataFrame()

    production_gate = get_forecast_production_readiness()
    registry = build_candidate_registry(
        leaderboard,
        data_gate_status=production_gate["status"],
        max_wape_pct=config.FORECAST_WAPE_MONITOR_MAX,
    ) if not leaderboard.empty else pd.DataFrame()

    return {
        "leaderboard": leaderboard,
        "winners": winners,
        "candidate_registry": registry,
        "production_gate": production_gate,
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

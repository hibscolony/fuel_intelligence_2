"""pages/forecast_monitoring.py -- Pemantauan & eksplorasi model forecasting."""
import sys
from datetime import timedelta
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config
from src.analytics import (
    get_forecast_for_date,
    get_forecast_training_series, get_model_backtest, get_multi_horizon_backtest,
    get_model_horizon_leaderboard,
    get_forecast_production_readiness,
    get_cross_year_validation, get_available_years,
)
from src.formatting import format_liter, format_percentage, format_number
from src import ui

ui.inject_global_css()

ui.page_header(
    title="Model Lab — Evaluasi Forecast",
    description="Evaluasi akurasi, ketidakpastian, dan kesiapan model sebelum digunakan untuk perencanaan operasional.",
)

training_series = get_forecast_training_series()
last_date = training_series.index.max().date()
production_gate = get_forecast_production_readiness()

ui.section_header("Kesiapan Forecast Operasional")
pg1, pg2, pg3, pg4 = st.columns(4)
with pg1:
    ui.metric_card(
        "Deployment Gate",
        production_gate["status"],
        status="success" if production_gate["status"] == "READY_FOR_MODEL_REVIEW" else "danger",
    )
with pg2:
    ui.metric_card("Training Staleness", f"{production_gate['training_staleness_days']} hari")
with pg3:
    ui.metric_card("Segmen Operasional", f"{production_gate['latest_segment_days']} hari")
with pg4:
    ui.metric_card("Source Gap", f"{production_gate['source_gap_days']} hari")

if production_gate["reasons"]:
    st.error(
        "Model belum boleh dipromosikan ke produksi:\n\n- "
        + "\n- ".join(production_gate["reasons"]),
        icon="🛑",
    )
else:
    st.success(
        "Data gate lulus. Kandidat model tetap memerlukan validasi model dan approval manual sebelum deployment."
    )

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# MODEL SELECTOR
# =============================================================================
ui.section_header("Pilih Model Analisis")

model_name = st.selectbox(
    "Forecast Model",
    options=list(config.FORECAST_MODEL_CHOICES.keys()),
    format_func=lambda k: config.FORECAST_MODEL_CHOICES[k],
)
st.caption(
    f"Model dilatih **hanya** dari data sampai **{last_date}**. "
    "Data training menggunakan **sumber Excel saja** — data UJB tidak diikutsertakan dalam training "
    "agar deret historis tetap konsisten. Halaman analitik lain (anomali, health score, dll) "
    "tetap menggunakan data hybrid Excel + UJB."
)

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# BACKTEST RESULTS — D+1 diagnostics only
# =============================================================================
backtest   = get_model_backtest(model_name, config.FORECAST_BACKTEST_DAYS)
bt_summary = backtest["summary"]
bt_df      = backtest["forecast_df"]
bt_rolling = backtest["rolling_perf"]

ui.section_header(f"Kinerja Prediksi Hari Berikutnya — {config.FORECAST_MODEL_CHOICES[model_name]}")
st.caption(
    f"Backtest walk-forward 1-hari-ke-depan pada {config.FORECAST_BACKTEST_DAYS} hari terakhir. "
    "Metrik di bagian ini hanya menilai D+1; jangan gunakan WAPE D+1 untuk menyimpulkan "
    "akurasi D+30, D+60, atau D+90. Evaluasi multi-horizon tersedia di bawah."
)

if backtest["drift_warning"]:
    st.warning(backtest["drift_warning"], icon="⚠️")

drift = backtest["drift_status"]
if drift["status"] != "INSUFFICIENT_DATA":
    dc1, dc2, dc3 = st.columns(3)
    with dc1: ui.metric_card("WAPE Baseline", format_percentage(drift["baseline_wape"]))
    with dc2: ui.metric_card("WAPE Terkini", format_percentage(drift["current_wape"]))
    with dc3: ui.metric_card(
        "Status Drift", drift["status"],
        subtext=f"Rasio {drift['deterioration_ratio']:.2f}x",
        status="danger" if drift["drift_detected"] else "success",
    )
    st.caption(
        "Baseline drift memakai periode rolling yang tidak bertumpang tindih dengan window terkini, "
        "sehingga perubahan satu hari tidak salah dibaca sebagai drift."
    )

kc1, kc2, kc3, kc4, kc5 = st.columns(5)
with kc1: ui.metric_card("MAE D+1",          format_liter(bt_summary.mae))
with kc2: ui.metric_card("RMSE D+1",         format_liter(bt_summary.rmse))
with kc3: ui.metric_card("WAPE D+1",         format_percentage(bt_summary.wape))
with kc4: ui.metric_card("Bias D+1",         format_liter(bt_summary.bias))
with kc5: ui.metric_card("Interval Cov.", format_percentage(bt_summary.interval_coverage_pct))

st.markdown("<br>", unsafe_allow_html=True)

status_val = bt_summary.model_health_status or "—"
sev_map = {"HEALTHY": "success", "MONITOR": "warning", "CRITICAL": "danger", "RETRAIN": "danger", "INSUFFICIENT_DATA": "neutral"}
sev = sev_map.get(status_val, "info")
badge_html = ui.status_badge(status_val, sev)
st.markdown(
    f'<div style="font-size:0.85rem;color:#71869B;font-weight:600;margin-bottom:1.25rem;">'
    f'Model Status D+1 &nbsp; {badge_html}</div>',
    unsafe_allow_html=True,
)

# =============================================================================
# ACTUAL vs FORECAST CHART
# =============================================================================
ui.section_header("Aktual vs Prediksi Hari Berikutnya")
st.caption("Perbandingan nilai historis aktual dengan prediksi satu hari ke depan.")

fig_bt = go.Figure()
fig_bt.add_trace(go.Scatter(
    x=bt_df["date"], y=bt_df["actual_fuel"],
    name="Aktual", line=dict(color="#1E4D7A", width=2),
))
fig_bt.add_trace(go.Scatter(
    x=bt_df["date"], y=bt_df["upper_interval"],
    line=dict(width=0), showlegend=False,
))
fig_bt.add_trace(go.Scatter(
    x=bt_df["date"], y=bt_df["lower_interval"],
    line=dict(width=0),
    fill="tonexty",
    fillcolor="rgba(91, 155, 230, 0.15)",
    name="Prediction Interval",
))
fig_bt.add_trace(go.Scatter(
    x=bt_df["date"], y=bt_df["forecast_fuel"],
    name="Forecast", line=dict(color="#5B9BE6", dash="dash", width=2),
))
fig_bt = ui.format_chart(fig_bt)
fig_bt.update_layout(height=400)
st.plotly_chart(fig_bt, width='stretch')

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# RESIDUAL & ROLLING WAPE
# =============================================================================
ui.section_header("Pola Kesalahan Prediksi D+1")
col1, col2 = st.columns(2)

with col1:
    color_map = {"Dalam interval": "#3977C8", "Luar interval": "#D94C4C"}
    fig2 = px.scatter(
        bt_df,
        x="date", y="residual",
        color=bt_df["within_interval"].map({True: "Dalam interval", False: "Luar interval"}),
        color_discrete_map=color_map,
    )
    fig2.add_hline(y=0, line_color="#94A3B8", line_dash="dash")
    fig2 = ui.format_chart(fig2)
    fig2.update_layout(height=320, legend_title="")
    st.plotly_chart(fig2, width='stretch')

with col2:
    fig3 = px.line(
        bt_rolling, x="date", y="rolling_wape", color="window_days",
        color_discrete_sequence=["#1E4D7A", "#3977C8", "#8FC3F0"],
    )
    for level, label, color in [
        (config.FORECAST_WAPE_HEALTHY_MAX, "Batas HEALTHY", "#E8A317"),
        (config.FORECAST_WAPE_MONITOR_MAX, "Batas MONITOR", "#D94C4C"),
    ]:
        fig3.add_hline(
            y=level, line_dash="dash", line_color=color,
            annotation_text=label,
            annotation_font_color=color,
        )
    fig3 = ui.format_chart(fig3)
    fig3.update_layout(height=320)
    st.plotly_chart(fig3, width='stretch')

st.download_button(
    f"Download Backtest D+1 {config.FORECAST_MODEL_CHOICES[model_name]} (CSV)",
    bt_df.to_csv(index=False),
    f"forecast_backtest_d1_{model_name}.csv",
    "text/csv",
)

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# MULTI-HORIZON ROLLING-ORIGIN EVALUATION
# =============================================================================
ui.section_header("Akurasi Berdasarkan Jangka Waktu")
st.caption(
    "Rolling-origin evaluation menguji model secara terpisah pada D+1, D+3, D+7, D+14, "
    "D+30, D+60, dan D+90. Setiap origin hanya memakai data yang sudah tersedia sampai "
    "tanggal tersebut, sehingga WAPE tiap horizon bisa dibandingkan secara lebih jujur."
)

with st.spinner("Menghitung multi-horizon backtest..."):
    multi = get_multi_horizon_backtest(model_name, evaluation_days=180, origin_step_days=14)

mh_summary = multi["summary"]
mh_raw = multi["backtest_df"]
mh_quantiles = multi["residual_quantiles"]
interval_validation = multi["interval_validation"]
interval_summary = interval_validation["interval_summary"]

if mh_summary.empty:
    st.warning("Belum cukup data untuk evaluasi multi-horizon model ini.")
else:
    def _metric_for_horizon(h, col):
        row = mh_summary[mh_summary["horizon_days"] == h]
        return None if row.empty else float(row.iloc[0][col])

    mh1, mh7, mh30, mh90 = st.columns(4)
    for col, h in [(mh1, 1), (mh7, 7), (mh30, 30), (mh90, 90)]:
        value = _metric_for_horizon(h, "wape")
        with col:
            ui.metric_card(f"WAPE D+{h}", "N/A" if value is None else format_percentage(value))

    st.markdown("<br>", unsafe_allow_html=True)

    mh_display = mh_summary.rename(columns={
        "horizon_days": "Horizon (hari)",
        "n_forecasts": "N Forecast",
        "mae": "MAE (L)",
        "rmse": "RMSE (L)",
        "wape": "WAPE (%)",
        "bias": "Bias (L)",
    }).copy()
    for col in ["MAE (L)", "RMSE (L)", "Bias (L)"]:
        mh_display[col] = mh_display[col].round(1)
    mh_display["WAPE (%)"] = mh_display["WAPE (%)"].round(2)

    mh_left, mh_right = st.columns([1.1, 1])
    with mh_left:
        st.dataframe(mh_display, width="stretch", hide_index=True)
    with mh_right:
        fig_h = px.line(
            mh_summary,
            x="horizon_days",
            y="wape",
            markers=True,
            labels={"horizon_days": "Forecast Horizon (hari)", "wape": "WAPE (%)"},
            color_discrete_sequence=["#3977C8"],
        )
        fig_h = ui.format_chart(fig_h)
        fig_h.update_layout(height=340)
        st.plotly_chart(fig_h, width='stretch')

    st.caption(
        "WAPE di atas dihitung pada holdout origin yang lebih baru. Origin sebelumnya dipakai "
        "untuk kalibrasi interval, sehingga evaluasi tidak memakai residual yang sama."
    )

    ui.section_header("Keandalan Rentang Prediksi")
    st.caption(
        f"Kalibrasi: {interval_validation['calibration_start']:%d %b %Y}–"
        f"{interval_validation['calibration_end']:%d %b %Y}. "
        f"Holdout: {interval_validation['evaluation_start']:%d %b %Y}–"
        f"{interval_validation['evaluation_end']:%d %b %Y}. Target coverage interval: 80%."
    )
    interval_display = interval_summary.rename(columns={
        "horizon_days": "Horizon",
        "n_calibration": "N Kalibrasi",
        "n_evaluation": "N Holdout",
        "interval_coverage_pct": "Coverage Aktual (%)",
        "expected_coverage_pct": "Target Coverage (%)",
        "coverage_gap_pct": "Gap Coverage (pp)",
        "mean_interval_width": "Lebar Interval Rata-rata (L)",
        "readiness_status": "Readiness",
    }).copy()
    for column in ["Coverage Aktual (%)", "Target Coverage (%)", "Gap Coverage (pp)", "Lebar Interval Rata-rata (L)"]:
        interval_display[column] = interval_display[column].round(1)
    st.dataframe(interval_display, width="stretch", hide_index=True)
    limited_horizons = interval_summary.loc[
        interval_summary["readiness_status"].ne("READY"), "horizon_days"
    ].astype(int).tolist()
    if limited_horizons:
        st.warning(
            "Prediction interval belum layak untuk horizon: "
            + ", ".join(f"D+{h}" for h in limited_horizons)
            + ". Coverage holdout terlalu jauh dari target atau sampelnya belum cukup."
        )

    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button(
            "Download Multi-Horizon Backtest (CSV)",
            mh_raw.to_csv(index=False),
            f"forecast_multi_horizon_{model_name}.csv",
            "text/csv",
        )
    with dl2:
        st.download_button(
            "Download Residual Quantile per Horizon (CSV)",
            mh_quantiles.to_csv(index=False),
            f"forecast_residual_quantiles_{model_name}.csv",
            "text/csv",
        )

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# ALL-MODEL LEADERBOARD BY HORIZON
# =============================================================================
ui.section_header("Kandidat Model per Jangka Waktu")
st.caption(
    "Tidak ada asumsi satu model terbaik untuk semua horizon. Ranking dihitung terpisah "
    "pada holdout origin berdasarkan WAPE, lalu MAE dan |bias| sebagai tie-breaker. "
    "Evaluasi lengkap diprioritaskan."
)

if st.button("Bandingkan Semua Model", key="run_horizon_leaderboard", type="secondary"):
    st.session_state["show_horizon_leaderboard"] = True

if st.session_state.get("show_horizon_leaderboard", False):
    with st.spinner("Membandingkan seluruh model pada horizon yang sama..."):
        comparison = get_model_horizon_leaderboard(
            evaluation_days=180,
            origin_step_days=14,
        )

    winners = comparison["winners"]
    leaderboard = comparison["leaderboard"]
    errors = comparison["errors"]
    registry = comparison["candidate_registry"]

    if winners.empty:
        st.warning(
            "Belum ada model dengan minimal "
            f"{config.FORECAST_MIN_MODEL_SELECTION_ORIGINS} holdout forecast. "
            "Kandidat yang ada masih provisional dan belum layak disebut pemenang."
        )
    else:
        winner_lookup = winners.set_index("horizon_days")
        card_horizons = [1, 7, 30, 60, 90]
        winner_cols = st.columns(len(card_horizons))
        for col, h in zip(winner_cols, card_horizons):
            with col:
                if h in winner_lookup.index:
                    row = winner_lookup.loc[h]
                    ui.metric_card(
                        f"Best D+{h}",
                        str(row["model_label"]),
                        subtext=f"WAPE {float(row['wape']):.2f}%",
                    )
                else:
                    ui.metric_card(f"Best D+{h}", "N/A")

        st.markdown("<br>", unsafe_allow_html=True)

        board_display = leaderboard.copy()
        board_display["model_label"] = board_display["model_name"].map(config.FORECAST_MODEL_CHOICES).fillna(board_display.get("model_label"))
        board_display = board_display.rename(columns={
            "horizon_days": "Horizon",
            "rank": "Rank",
            "model_label": "Model",
            "n_forecasts": "N Forecast",
            "wape": "WAPE (%)",
            "mae": "MAE (L)",
            "rmse": "RMSE (L)",
            "bias": "Bias (L)",
            "evaluation_complete": "Eval Lengkap",
            "selection_ready": "Selection Ready",
        })
        keep_cols = ["Horizon", "Rank", "Model", "N Forecast", "WAPE (%)", "MAE (L)", "RMSE (L)", "Bias (L)", "Eval Lengkap", "Selection Ready"]
        board_display = board_display[keep_cols]
        board_display["WAPE (%)"] = board_display["WAPE (%)"].round(2)
        for c in ["MAE (L)", "RMSE (L)", "Bias (L)"]:
            board_display[c] = board_display[c].round(1)
        st.dataframe(board_display, width="stretch", hide_index=True)

        fig_models = px.line(
            leaderboard,
            x="horizon_days",
            y="wape",
            color="model_label",
            markers=True,
            labels={
                "horizon_days": "Forecast Horizon (hari)",
                "wape": "WAPE (%)",
                "model_label": "Model",
            },
        )
        fig_models = ui.format_chart(fig_models)
        fig_models.update_layout(height=420)
        st.plotly_chart(fig_models, width='stretch')

        st.download_button(
            "Download Leaderboard Semua Model (CSV)",
            leaderboard.to_csv(index=False),
            "forecast_model_horizon_leaderboard.csv",
            "text/csv",
        )

    if not errors.empty:
        with st.expander("Model yang gagal dievaluasi", expanded=False):
            st.dataframe(errors, width="stretch", hide_index=True)

    if not registry.empty:
        ui.section_header("Daftar Kandidat Model")
        st.caption(
            "Registry ini adalah hasil evaluasi, bukan deployment. Status ELIGIBLE_FOR_REVIEW "
            "tetap membutuhkan approval manual dan pembuatan artefak produksi."
        )
        registry_display = registry[[
            "horizon_days", "model_name", "wape", "n_forecasts",
            "interval_readiness_status", "candidate_status",
            "promotion_status", "promotion_reason",
        ]].rename(columns={
            "horizon_days": "Horizon",
            "model_name": "Model",
            "wape": "WAPE (%)",
            "n_forecasts": "N Holdout",
            "interval_readiness_status": "Interval Readiness",
            "candidate_status": "Candidate Status",
            "promotion_status": "Promotion Status",
            "promotion_reason": "Promotion Reason",
        })
        registry_display["WAPE (%)"] = registry_display["WAPE (%)"].round(2)
        st.dataframe(registry_display, width="stretch", hide_index=True)
        st.download_button(
            "Download Candidate Registry (CSV)",
            registry.to_csv(index=False),
            "forecast_candidate_registry.csv",
            "text/csv",
        )

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# PREDICT TO DATE
# =============================================================================
ui.section_header("Prediksi ke Tanggal Tertentu")
st.caption("Eksplorasi prediksi kebutuhan solar total pada tanggal manapun di masa depan.")

with st.expander("Buka Simulator Prediksi", expanded=False):
    ec1, ec2 = st.columns([3, 1])
    target_date = ec1.date_input("Tanggal Prediksi", value=last_date + timedelta(days=30))
    run_explorer = ec2.button("Jalankan Prediksi", type="primary", width="stretch")

    if run_explorer or "explorer_result" in st.session_state:
        if run_explorer:
            with st.spinner("Menghitung prediksi…"):
                st.session_state["explorer_result"] = get_forecast_for_date(model_name, str(target_date))
                st.session_state["explorer_model"]  = model_name

        result    = st.session_state.get("explorer_result")
        used_model = st.session_state.get("explorer_model", model_name)

        if result:
            if used_model != model_name:
                st.info("Model berubah. Klik 'Jalankan Prediksi' lagi untuk update.")

            if result["method"] == "historical_actual":
                pt = result.get("point")
                val_str = format_liter(pt) if pt is not None else "—"
                st.info(f"Tanggal {target_date} adalah data historis. AKTUAL: **{val_str}**")
            else:
                rc1, rc2, rc3 = st.columns(3)
                with rc1: ui.metric_card("Prediksi",     format_liter(result["point"]))
                with rc2: ui.metric_card("Batas Bawah", format_liter(result["lower"]))
                with rc3: ui.metric_card("Batas Atas",  format_liter(result["upper"]))

                if result.get("interval_method") == "rolling_origin_residual_quantile":
                    interval_label = (
                        f"Interval dikalibrasi dengan residual D+{result['interval_calibration_horizon']} "
                        f"(n={result['interval_n_residuals']})."
                    )
                    if result.get("interval_extrapolated"):
                        interval_label += " Target berada di luar horizon kalibrasi maksimum, jadi interval bersifat extrapolated."
                    elif result.get("interval_calibration_independent"):
                        interval_label += " Kalibrasi dan evaluasi memakai origin yang terpisah."
                    st.caption(interval_label)

                if result.get("warning"):
                    st.warning(result["warning"])

                if result["method"] == "recursive_forecast" and "path" in result:
                    path = result["path"]
                    fig_exp = go.Figure()
                    fig_exp.add_trace(go.Scatter(
                        x=training_series.index[-60:], y=training_series.values[-60:],
                        name="Aktual", line=dict(color="#1E4D7A"),
                    ))
                    fig_exp.add_trace(go.Scatter(
                        x=path.index, y=path.values,
                        name="Forecast", line=dict(color="#5B9BE6", dash="dash"),
                    ))
                    fig_exp.add_trace(go.Scatter(
                        x=[path.index[-1]], y=[result["point"]],
                        mode="markers",
                        marker=dict(size=12, color="#E8A317"),
                        name=f"Prediksi {target_date}",
                    ))
                    fig_exp = ui.format_chart(fig_exp)
                    fig_exp.update_layout(height=360)
                    st.plotly_chart(fig_exp, width='stretch')

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# CROSS-YEAR VALIDATION
# =============================================================================
available_years = get_available_years()
if len(available_years) >= 2:
    ui.section_header("Uji pada Tahun Berikutnya")
    st.caption("Validasi out-of-sample menguji model pada data tahun berikutnya yang sengaja disembunyikan.")

    with st.expander("Konfigurasi Validasi", expanded=False):
        vc1, vc2 = st.columns(2)
        cutoff_year = vc1.selectbox(
            "Batas Data Latih", available_years[:-1], index=len(available_years) - 2,
        )
        validation_model = vc2.selectbox(
            "Model", options=list(config.FORECAST_MODEL_CHOICES.keys()),
            format_func=lambda k: config.FORECAST_MODEL_CHOICES[k],
            key="cross_year_model",
        )

        cutoff_date_str = f"{cutoff_year}-12-31"
        cross_val = get_cross_year_validation(validation_model, cutoff_date_str)

        if cross_val.empty:
            st.info(f"Belum ada data aktual di tahun setelah {cutoff_year}.")
        else:
            mae  = cross_val["residual"].abs().mean()
            wape = cross_val["residual"].abs().sum() / cross_val["actual"].abs().sum() * 100

            vc3, vc4, vc5 = st.columns(3)
            with vc3: ui.metric_card("WAPE (Out-of-Sample)", format_percentage(wape))
            with vc4: ui.metric_card("MAE (Out-of-Sample)",  format_liter(mae))
            with vc5: ui.metric_card("Hari Divalidasi",      str(len(cross_val)))

            fig_cv = go.Figure()
            fig_cv.add_trace(go.Scatter(
                x=cross_val["date"], y=cross_val["actual"],
                name="Aktual", line=dict(color="#1E4D7A"),
            ))
            for method, color, dash in [
                ("recursive_forecast",  "#5B9BE6", "dash"),
                ("climatology_fallback","#E8A317", "dot"),
            ]:
                sub = cross_val[cross_val["method"] == method]
                if not sub.empty:
                    fig_cv.add_trace(go.Scatter(
                        x=sub["date"], y=sub["forecast"],
                        name=f"Forecast ({method})",
                        line=dict(color=color, dash=dash),
                    ))
            cutoff_ts = pd.Timestamp(cutoff_date_str)
            fig_cv.add_shape(
                type="line", x0=cutoff_ts, x1=cutoff_ts, y0=0, y1=1,
                xref="x", yref="paper",
                line=dict(dash="dot", color="#94A3B8"),
            )
            fig_cv = ui.format_chart(fig_cv)
            fig_cv.update_layout(height=380)
            st.plotly_chart(fig_cv, width='stretch')

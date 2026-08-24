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
    get_forecast_monitoring, get_forecast_for_date,
    get_forecast_training_series, get_model_backtest, get_multi_horizon_backtest,
    get_cross_year_validation, get_available_years,
)
from src.formatting import format_liter, format_percentage, format_number
from src import ui

ui.inject_global_css()

ui.page_header(
    title="Forecast Monitoring",
    description="Pantau performa model forecasting, bandingkan prediksi dengan aktual, dan evaluasi hasil out-of-sample.",
)

training_series = get_forecast_training_series()
last_date = training_series.index.max().date()

# =============================================================================
# MODEL SELECTOR — wrapped in section card
# =============================================================================
st.html('<div class="jict-section-card">')
st.html('<div class="jict-section-title">Model Configuration</div>')

model_name = st.selectbox(
    "Forecast Model",
    options=list(config.FORECAST_MODEL_CHOICES.keys()),
    format_func=lambda k: config.FORECAST_MODEL_CHOICES[k],
)
st.caption(f"Model dilatih **hanya** dari data sampai **{last_date}**.")
st.html('</div>')

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# BACKTEST RESULTS — D+1 diagnostics only
# =============================================================================
backtest   = get_model_backtest(model_name, config.FORECAST_BACKTEST_DAYS)
bt_summary = backtest["summary"]
bt_df      = backtest["forecast_df"]
bt_rolling = backtest["rolling_perf"]

ui.section_header(f"Forecast Performance D+1 — {config.FORECAST_MODEL_CHOICES[model_name]}")
st.caption(
    f"Backtest walk-forward 1-hari-ke-depan pada {config.FORECAST_BACKTEST_DAYS} hari terakhir. "
    "Metrik di bagian ini hanya menilai D+1; jangan gunakan WAPE D+1 untuk menyimpulkan "
    "akurasi D+30, D+60, atau D+90. Evaluasi multi-horizon tersedia di bawah."
)

if backtest["drift_warning"]:
    st.warning(backtest["drift_warning"], icon="⚠️")

# KPI cards
kc1, kc2, kc3, kc4, kc5 = st.columns(5)
with kc1: ui.metric_card("MAE D+1",          format_liter(bt_summary.mae))
with kc2: ui.metric_card("RMSE D+1",         format_liter(bt_summary.rmse))
with kc3: ui.metric_card("WAPE D+1",         format_percentage(bt_summary.wape))
with kc4: ui.metric_card("Bias D+1",         format_liter(bt_summary.bias))
with kc5: ui.metric_card("Interval Cov.", format_percentage(bt_summary.interval_coverage_pct))

st.markdown("<br>", unsafe_allow_html=True)

# Model status badge
status_val = bt_summary.model_health_status or "—"
sev_map = {"HEALTHY": "success", "MONITOR": "warning", "CRITICAL": "danger", "INSUFFICIENT_DATA": "neutral"}
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
ui.section_header("Actual vs Forecast D+1")
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
st.plotly_chart(fig_bt, use_container_width=True)

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# RESIDUAL & ROLLING WAPE
# =============================================================================
ui.section_header("Analisis Residual & Rolling WAPE D+1")
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
    st.plotly_chart(fig2, use_container_width=True)

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
    st.plotly_chart(fig3, use_container_width=True)

st.download_button(
    f"Download Backtest D+1 {config.FORECAST_MODEL_CHOICES[model_name]} (CSV)",
    bt_df.to_csv(index=False),
    f"forecast_backtest_d1_{model_name}.csv",
    "text/csv",
)

st.markdown("<br><br>", unsafe_allow_html=True)

# =============================================================================
# MULTI-HORIZON ROLLING-ORIGIN EVALUATION
# =============================================================================
ui.section_header("Performa per Forecast Horizon")
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
        st.dataframe(mh_display, use_container_width=True, hide_index=True)
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
        st.plotly_chart(fig_h, use_container_width=True)

    st.caption(
        "Kurva ini memperlihatkan degradasi akurasi ketika horizon bertambah. "
        "Residual quantile dihitung terpisah per horizon sebagai fondasi prediction interval yang horizon-aware."
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

st.markdown("<br><br>", unsafe_allow_html=True)

# =============================================================================
# PREDICT TO DATE
# =============================================================================
ui.section_header("Prediksi ke Tanggal Tertentu")
st.caption("Eksplorasi prediksi kebutuhan solar total pada tanggal manapun di masa depan.")

with st.expander("Buka Simulator Prediksi", expanded=False):
    ec1, ec2 = st.columns([3, 1])
    target_date = ec1.date_input("Tanggal Prediksi", value=last_date + timedelta(days=30))
    run_explorer = ec2.button("Jalankan Prediksi", type="primary", use_container_width=True)

    if run_explorer or "explorer_result" in st.session_state:
        if run_explorer:
            with st.spinner("Menghitung prediksi…"):
                st.session_state["explorer_result"] = get_forecast_for_date(model_name, target_date)
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
                    st.plotly_chart(fig_exp, use_container_width=True)

st.markdown("<br><br>", unsafe_allow_html=True)

# =============================================================================
# CROSS-YEAR VALIDATION
# =============================================================================
available_years = get_available_years()
if len(available_years) >= 2:
    ui.section_header("Validasi Lintas Tahun")
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
            st.plotly_chart(fig_cv, use_container_width=True)

st.markdown("<br><br>", unsafe_allow_html=True)

# =============================================================================
# PRODUCTION MODEL MONITORING
# =============================================================================
ui.section_header("Monitoring Model Produksi Terintegrasi")
st.caption("Pantau model default yang di-deploy ke produksi.")

prod_forecast = get_forecast_monitoring()
prod_summary  = prod_forecast["summary"]

if prod_forecast["is_placeholder"]:
    st.error(
        "Model saat ini adalah PLACEHOLDER. Ganti dengan output model forecasting asli di data/processed.",
        icon="🚧",
    )

prod_status_val = prod_summary.model_health_status or "—"
prod_sev = sev_map.get(prod_status_val, "info")
prod_badge = ui.status_badge(prod_status_val, prod_sev)
model_label = prod_summary.model_name or "—"
st.markdown(
    f'<div style="font-size:0.85rem;color:#71869B;font-weight:600;margin-bottom:1rem;">'
    f'Model Status &nbsp; {prod_badge} &nbsp;'
    f'<span style="color:#94A3B8;">·</span> &nbsp;'
    f'<code style="font-size:0.8rem;">{model_label}</code></div>',
    unsafe_allow_html=True,
)

if prod_forecast["drift_warning"]:
    st.warning(prod_forecast["drift_warning"], icon="⚠️")

with st.expander("Detail Model Produksi", expanded=False):
    pc1, pc2, pc3, pc4, pc5 = st.columns(5)
    with pc1: ui.metric_card("MAE",          format_liter(prod_summary.mae))
    with pc2: ui.metric_card("RMSE",         format_liter(prod_summary.rmse))
    with pc3: ui.metric_card("WAPE",         format_percentage(prod_summary.wape))
    with pc4: ui.metric_card("Bias",         format_liter(prod_summary.bias))
    with pc5: ui.metric_card("Interval Cov.", format_percentage(prod_summary.interval_coverage_pct))

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
    get_forecast_training_series, get_model_backtest, get_cross_year_validation, get_available_years,
)
from src.formatting import format_liter, format_percentage, format_number

st.title("Forecast Monitoring")
st.caption(
    "Bandingkan model forecasting, lihat performanya di data historis, dan minta prediksi "
    "untuk tanggal manapun -- semua mengikuti model yang dipilih di bawah."
)

training_series = get_forecast_training_series()
last_date = training_series.index.max().date()
st.caption(
    f"Model di halaman ini dilatih HANYA dari data sampai **{last_date}** "
    f"(`config.FORECAST_TRAINING_CUTOFF`) -- data sesudahnya sengaja tidak diikutkan sbg training, "
    f"supaya prediksi ke tanggal setelahnya betul-betul out-of-sample. Untuk membandingkan dgn data "
    f"aktual sesudahnya, lihat bagian \"Validasi Lintas Tahun\" di bawah."
)

# =============================================================================
# PEMILIH MODEL -- dipakai bersama oleh grafik backtest & prediksi tanggal
# =============================================================================
st.subheader("Pilih Model")
model_name = st.selectbox(
    "Model forecasting", options=list(config.FORECAST_MODEL_CHOICES.keys()),
    format_func=lambda k: config.FORECAST_MODEL_CHOICES[k],
    label_visibility="collapsed",
)

st.divider()

# =============================================================================
# GRAFIK & METRIK AKTUAL VS FORECAST -- BERUBAH SESUAI MODEL DI ATAS
# =============================================================================
st.subheader(f"📊 Aktual vs Forecast -- {config.FORECAST_MODEL_CHOICES[model_name]}")
st.caption(
    f"Backtest walk-forward 1-hari-ke-depan pada {config.FORECAST_BACKTEST_DAYS} hari terakhir, "
    f"memakai model yang dipilih di atas."
)

backtest = get_model_backtest(model_name, config.FORECAST_BACKTEST_DAYS)
bt_summary = backtest["summary"]
bt_df = backtest["forecast_df"]
bt_rolling = backtest["rolling_perf"]

status_color = {"HEALTHY": "green", "MONITOR": "orange", "RETRAIN": "red",
                "INSUFFICIENT_DATA": "gray"}.get(bt_summary.model_health_status, "gray")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("MAE", format_liter(bt_summary.mae))
c2.metric("RMSE", format_liter(bt_summary.rmse))
c3.metric("WAPE", format_percentage(bt_summary.wape))
c4.metric("Bias", format_liter(bt_summary.bias), help="Positif = model under-forecast (aktual > prediksi)")
c5.metric("Interval Coverage", format_percentage(bt_summary.interval_coverage_pct))

st.markdown(f"**Status Model:** :{status_color}[{bt_summary.model_health_status}]  "
            f"(Ambang: HEALTHY \u2264 {config.FORECAST_WAPE_HEALTHY_MAX}% \u2264 MONITOR \u2264 "
            f"{config.FORECAST_WAPE_MONITOR_MAX}% \u2264 RETRAIN)")

if backtest["drift_warning"]:
    st.warning(backtest["drift_warning"], icon="\u26a0\ufe0f")

fig_bt = go.Figure()
fig_bt.add_trace(go.Scatter(x=bt_df["date"], y=bt_df["actual_fuel"], name="Aktual", line=dict(color="#0b2545")))
fig_bt.add_trace(go.Scatter(x=bt_df["date"], y=bt_df["forecast_fuel"], name="Forecast",
                             line=dict(color="#13c2c2", dash="dash")))
fig_bt.add_trace(go.Scatter(x=bt_df["date"], y=bt_df["upper_interval"], line=dict(width=0), showlegend=False))
fig_bt.add_trace(go.Scatter(x=bt_df["date"], y=bt_df["lower_interval"], line=dict(width=0), fill="tonexty",
                             fillcolor="rgba(19,194,194,0.15)", name="Prediction Interval"))
fig_bt.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig_bt, width="stretch")

col1, col2 = st.columns(2)
with col1:
    st.subheader("Residual")
    fig2 = px.scatter(bt_df, x="date", y="residual", color=bt_df["within_interval"].map(
        {True: "Dalam interval", False: "Luar interval"}))
    fig2.add_hline(y=0, line_color="black")
    fig2.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), legend_title="")
    st.plotly_chart(fig2, width="stretch")

with col2:
    st.subheader("Rolling WAPE")
    fig3 = px.line(bt_rolling, x="date", y="rolling_wape", color="window_days")
    for level, name in [(config.FORECAST_WAPE_HEALTHY_MAX, "Batas HEALTHY"),
                        (config.FORECAST_WAPE_MONITOR_MAX, "Batas MONITOR")]:
        fig3.add_hline(y=level, line_dash="dot", line_color="gray", annotation_text=name)
    fig3.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig3, width="stretch")

st.download_button(f"Download Backtest {config.FORECAST_MODEL_CHOICES[model_name]} (CSV)",
                    bt_df.to_csv(index=False), f"forecast_backtest_{model_name}.csv", "text/csv")

st.divider()

# =============================================================================
# PREDIKSI KE TANGGAL TERTENTU -- pakai model yang sama dari dropdown di atas
# =============================================================================
st.subheader("🔮 Prediksi ke Tanggal Tertentu")
st.caption(
    "Pilih tanggal manapun (dekat maupun jauh di masa depan) untuk melihat prediksi kebutuhan "
    "solar total pada tanggal tersebut, memakai model yang sama dari dropdown di atas."
)

ec1, ec2 = st.columns([3, 1])
target_date = ec1.date_input(
    "Tanggal yang ingin diprediksi", value=last_date + timedelta(days=30),
    help="Bisa tanggal jauh di masa depan (bahkan tahun berikutnya) -- metode prediksi otomatis "
         "menyesuaikan seberapa jauh horizonnya.",
)
run_explorer = ec2.button("Prediksi", type="primary", width="stretch")

if run_explorer or "explorer_result" in st.session_state:
    if run_explorer:
        with st.spinner("Menghitung prediksi..."):
            st.session_state["explorer_result"] = get_forecast_for_date(model_name, target_date)
            st.session_state["explorer_model"] = model_name
    result = st.session_state["explorer_result"]
    used_model = st.session_state.get("explorer_model", model_name)

    if used_model != model_name:
        st.info("Model di dropdown sudah berganti -- klik **Prediksi** lagi untuk memperbarui hasil di bawah.")

    if result["method"] == "historical_actual":
        if result["point"] is not None:
            st.info(f"📅 {target_date} ada di dalam data historis. Nilai AKTUAL: "
                    f"**{format_liter(result['point'])}** (bukan prediksi).")
        else:
            st.warning(f"📅 {target_date} ada di rentang historis tapi tidak ada data tercatat.")
    else:
        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("Prediksi", format_liter(result["point"]))
        rc2.metric("Batas Bawah", format_liter(result["lower"]))
        rc3.metric("Batas Atas", format_liter(result["upper"]))

        method_label = {"recursive_forecast": f"Forecast rekursif ({config.FORECAST_MODEL_CHOICES.get(result.get('model_name'), '')})",
                        "climatology_fallback": "Klimatologi (rata-rata historis hari-dalam-tahun yang sama)"}
        st.caption(f"Metode: **{method_label.get(result['method'], result['method'])}** | "
                   f"Horizon: {result['horizon_days']} hari sejak data terakhir ({last_date})")

        if result.get("warning"):
            icon = "🚨" if result["method"] == "climatology_fallback" else "⚠️"
            st.warning(result["warning"], icon=icon)

        if result["method"] == "recursive_forecast" and "path" in result:
            path = result["path"]
            fig_exp = go.Figure()
            fig_exp.add_trace(go.Scatter(x=training_series.index[-60:], y=training_series.values[-60:],
                                          name="Aktual (60 hari terakhir data training)", line=dict(color="#0b2545")))
            fig_exp.add_trace(go.Scatter(x=path.index, y=path.values, name="Forecast",
                                          line=dict(color="#13c2c2", dash="dash")))
            fig_exp.add_trace(go.Scatter(x=[path.index[-1]], y=[result["point"]], mode="markers",
                                          marker=dict(size=12, color="#d62728"), name=f"Prediksi {target_date}"))
            fig_exp.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_exp, width="stretch")

st.divider()

# =============================================================================
# VALIDASI LINTAS TAHUN -- pakai data tahun lebih baru sbg "ujian" out-of-sample
# =============================================================================
available_years = get_available_years()
if len(available_years) >= 2:
    st.subheader("📅 Validasi Lintas Tahun")
    st.caption(
        "Latih model HANYA dari data sebelum tanggal cutoff, lalu bandingkan prediksinya dengan "
        "AKTUAL sungguhan sesudahnya -- ini validasi out-of-sample yang jujur, bukan cuma backtest "
        "di data yang sama."
    )

    vc1, vc2 = st.columns(2)
    cutoff_year = vc1.selectbox("Latih dari data sebelum akhir tahun", available_years[:-1],
                                 index=len(available_years) - 2)
    validation_model = vc2.selectbox(
        "Model untuk validasi", options=list(config.FORECAST_MODEL_CHOICES.keys()),
        format_func=lambda k: config.FORECAST_MODEL_CHOICES[k], key="cross_year_model",
    )

    cutoff_date_str = f"{cutoff_year}-12-31"
    cross_val = get_cross_year_validation(validation_model, cutoff_date_str)

    if cross_val.empty:
        st.info(f"Belum ada data aktual di tahun setelah {cutoff_year} untuk divalidasi.")
    else:
        n_recursive = (cross_val["method"] == "recursive_forecast").sum()
        n_clim = (cross_val["method"] == "climatology_fallback").sum()
        mae = cross_val["residual"].abs().mean()
        wape = cross_val["residual"].abs().sum() / cross_val["actual"].abs().sum() * 100

        vc3, vc4, vc5 = st.columns(3)
        vc3.metric("WAPE (Out-of-Sample)", format_percentage(wape))
        vc4.metric("MAE (Out-of-Sample)", format_liter(mae))
        vc5.metric("Hari Divalidasi", f"{len(cross_val)} ({n_recursive} rekursif, {n_clim} klimatologi)")

        fig_cv = go.Figure()
        fig_cv.add_trace(go.Scatter(x=cross_val["date"], y=cross_val["actual"], name="Aktual",
                                     line=dict(color="#0b2545")))
        for method, color, dash in [("recursive_forecast", "#13c2c2", "dash"),
                                    ("climatology_fallback", "#ff7f0e", "dot")]:
            sub = cross_val[cross_val["method"] == method]
            if not sub.empty:
                fig_cv.add_trace(go.Scatter(x=sub["date"], y=sub["forecast"],
                                             name=f"Forecast ({method})", line=dict(color=color, dash=dash)))
        cutoff_ts = pd.Timestamp(cutoff_date_str)
        fig_cv.add_shape(type="line", x0=cutoff_ts, x1=cutoff_ts, y0=0, y1=1, xref="x", yref="paper",
                          line=dict(dash="dot", color="gray"))
        fig_cv.add_annotation(x=cutoff_ts, y=1, yref="paper", text="Cutoff (akhir data latih)",
                               showarrow=False, yanchor="bottom", font=dict(color="gray", size=11))
        fig_cv.update_layout(height=400, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig_cv, width="stretch")

        st.caption(
            f"Garis putus-putus cyan = horizon dekat (\u2264{config.FORECAST_RELIABLE_HORIZON_DAYS} hari, "
            f"forecast rekursif model terpilih). Garis titik-titik oranye = horizon jauh (klimatologi, "
            f"rata-rata historis hari-dalam-tahun yang sama). Semakin jauh dari garis cutoff, error "
            f"WAJAR semakin besar."
        )

        st.download_button("Download Validasi Lintas Tahun (CSV)", cross_val.to_csv(index=False),
                            f"cross_year_validation_{validation_model}.csv", "text/csv")

    st.divider()

# =============================================================================
# MONITORING MODEL TERINTEGRASI (PRODUKSI) -- dari forecast_results.csv / placeholder
# Ini TIDAK ikut dropdown di atas -- ini memantau model produksi Anda apa adanya.
# =============================================================================
st.subheader("Monitoring Model Terintegrasi (Produksi)")
st.caption(
    "Bagian ini memantau model forecasting yang SEDANG DIINTEGRASIKAN ke sistem "
    "(`data/processed/forecast_results.csv`) -- terpisah dari perbandingan model di atas."
)

forecast = get_forecast_monitoring()
summary = forecast["summary"]
df = forecast["forecast_df"]
rolling = forecast["rolling_perf"]

if forecast["is_placeholder"]:
    st.error(
        f"Model saat ini adalah **PLACEHOLDER** (`{summary.model_name}`) karena file "
        f"`data/processed/{config.FORECAST_RESULTS_FILENAME}` belum tersedia. Ganti dengan output "
        f"model forecasting asli Anda (skema kolom: date, actual_fuel, forecast_fuel, "
        f"lower_interval, upper_interval, model_name) supaya bagian ini memantau performa model "
        f"produksi sesungguhnya.",
        icon="🚧",
    )

status_color2 = {"HEALTHY": "green", "MONITOR": "orange", "RETRAIN": "red",
                 "INSUFFICIENT_DATA": "gray"}.get(summary.model_health_status, "gray")

pc1, pc2, pc3, pc4, pc5 = st.columns(5)
pc1.metric("MAE", format_liter(summary.mae))
pc2.metric("RMSE", format_liter(summary.rmse))
pc3.metric("WAPE", format_percentage(summary.wape))
pc4.metric("Bias", format_liter(summary.bias))
pc5.metric("Interval Coverage", format_percentage(summary.interval_coverage_pct))

st.markdown(f"**Status:** :{status_color2}[{summary.model_health_status}] | "
            f"Model: `{summary.model_name}` | Hari dievaluasi: {summary.n_days_evaluated}")

if forecast["drift_warning"]:
    st.warning(forecast["drift_warning"], icon="\u26a0\ufe0f")

with st.expander("Lihat grafik & detail monitoring model produksi"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["date"], y=df["actual_fuel"], name="Aktual", line=dict(color="#0b2545")))
    fig.add_trace(go.Scatter(x=df["date"], y=df["forecast_fuel"], name="Forecast",
                              line=dict(color="#13c2c2", dash="dash")))
    fig.add_trace(go.Scatter(x=df["date"], y=df["upper_interval"], line=dict(width=0), showlegend=False))
    fig.add_trace(go.Scatter(x=df["date"], y=df["lower_interval"], line=dict(width=0), fill="tonexty",
                              fillcolor="rgba(19,194,194,0.15)", name="Prediction Interval"))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

    latest_rolling = rolling.sort_values("date").groupby("window_days").tail(1)
    st.dataframe(latest_rolling[["window_days", "date", "rolling_mae", "rolling_wape", "model_health_status"]],
                 width="stretch", hide_index=True)

    st.download_button("Download Forecast Residuals Model Produksi (CSV)", df.to_csv(index=False),
                        "forecast_residuals.csv", "text/csv")

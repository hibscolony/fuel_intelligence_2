"""pages/data_quality.py -- Audit kualitas data & rekonsiliasi."""
import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analytics import get_cleaning_result, get_data_quality
from src.formatting import format_number, format_percentage
from src import ui

ui.inject_global_css()

ui.page_header(
    title="Data Quality",
    description="Audit otomatis kelengkapan & konsistensi data pengisian solar.",
    context="Termasuk rekonsiliasi terhadap total workbook historis.",
)

cleaning = get_cleaning_result()
dq       = get_data_quality()
kpis     = dq["kpis"]

# Overall status badge
status_map = {"PASS": "success", "REVIEW": "warning", "FAIL": "danger"}
status_sev = status_map.get(kpis.overall_status, "info")
badge_html = ui.status_badge(kpis.overall_status, status_sev)
st.markdown(
    f'<div style="font-size:0.85rem;color:#71869B;font-weight:600;margin-bottom:1.25rem;">'
    f'Overall Health &nbsp; {badge_html}</div>',
    unsafe_allow_html=True,
)

# ── Quality Metrics ───────────────────────────────────────────────────────
ui.section_header("Quality Metrics")
c1, c2, c3, c4 = st.columns(4)
with c1: ui.metric_card("Data Completeness",   format_percentage(kpis.data_completeness_percentage))
with c2: ui.metric_card("Valid Transaction %", format_percentage(kpis.valid_transaction_percentage))
with c3: ui.metric_card(
    "Duplicate Records", format_number(kpis.duplicate_count), "",
    "warning" if kpis.duplicate_count > 0 else "success",
)
with c4: ui.metric_card(
    "Invalid Value", format_number(kpis.invalid_value_count), "",
    "danger" if kpis.invalid_value_count > 0 else "success",
)

st.markdown("<br>", unsafe_allow_html=True)

c5, c6, c7, c8 = st.columns(4)
with c5: ui.metric_card(
    "Unusually High", format_number(kpis.unusually_high_count), "",
    "warning" if kpis.unusually_high_count > 0 else "success",
)
with c6: ui.metric_card(
    "Invalid Date", format_number(kpis.invalid_date_count), "",
    "danger" if kpis.invalid_date_count > 0 else "success",
)
with c7: ui.metric_card(
    "Major Rec. Issues", format_number(kpis.n_months_major_reconciliation_issue), "",
    "danger" if kpis.n_months_major_reconciliation_issue > 0 else "success",
)
with c8: ui.metric_card(
    "Reconciliation Status", kpis.reconciliation_status, "",
    "success" if kpis.reconciliation_status == "HEALTHY" else "warning",
)

st.markdown("<br><br>", unsafe_allow_html=True)

# ── Reconciliation Chart ──────────────────────────────────────────────────
ui.section_header("Rekonsiliasi Total Volume (Bulanan)")
monthly = cleaning["monthly_reconciliation"]

fig = px.bar(
    monthly, x="month_name",
    y=["workbook_reported_total", "recalculated_total"],
    barmode="group",
    labels={"value": "Liter", "month_name": ""},
    color_discrete_map={
        "workbook_reported_total": "#BFDDF8",
        "recalculated_total":      "#3977C8",
    },
)
fig = ui.format_chart(fig)
fig.update_layout(height=360, legend_title="")
st.plotly_chart(fig, use_container_width=True)

with st.expander("Tabel Detail Rekonsiliasi Bulanan"):
    st.dataframe(monthly, use_container_width=True, hide_index=True)

st.markdown("<br>", unsafe_allow_html=True)

ui.section_header("Rekonsiliasi per Kategori")
st.dataframe(
    cleaning["category_monthly_reconciliation"].sort_values(
        ["validation_status"],
        key=lambda s: s.map({
            "REQUIRES REVIEW": 0, "MAJOR DIFFERENCE": 1,
            "MINOR DIFFERENCE": 2, "MATCH": 3, "NO_WORKBOOK_VALUE": 4,
        }),
    ),
    use_container_width=True, hide_index=True,
)

st.markdown("<br><br>", unsafe_allow_html=True)

# ── Issue Tables ──────────────────────────────────────────────────────────
col1, col2 = st.columns(2)
with col1:
    ui.section_header("Ringkasan Isu Data")
    st.dataframe(cleaning["data_quality_report"], use_container_width=True, hide_index=True)

with col2:
    ui.section_header("Zero-Consumption Streak")
    st.caption("Equipment dengan jeda pengisian panjang di tengah masa aktifnya.")
    st.dataframe(
        dq["zero_streaks"][[
            "equipment_category", "equipment_id",
            "longest_gap_days", "longest_gap_start", "longest_gap_end",
        ]].head(20),
        use_container_width=True, hide_index=True,
    )

st.download_button(
    "Download Data Quality Report (CSV)",
    cleaning["data_quality_report"].to_csv(index=False),
    "data_quality_report.csv", "text/csv",
)

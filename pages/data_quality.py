"""pages/data_quality.py -- Audit kualitas data, workbook, dan source precedence."""
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analytics import get_cleaning_result, get_data_quality
from src.formatting import format_liter, format_number, format_percentage
from src import ui

ui.inject_global_css()

ui.page_header(
    title="Kualitas Data",
    description="Audit otomatis kelengkapan, konsistensi, dan asal sumber data pengisian solar.",
    context="Termasuk rekonsiliasi workbook historis dan precedence Excel ↔ UJB untuk mencegah double counting.",
)

cleaning = get_cleaning_result()
dq = get_data_quality()
kpis = dq["kpis"]
source_audit = cleaning.get("source_reconciliation_audit", pd.DataFrame())
source_calendar = cleaning.get("source_coverage_calendar", pd.DataFrame())

# Overall status badge
status_map = {"PASS": "success", "REVIEW": "warning", "FAILED": "danger"}
status_sev = status_map.get(kpis.overall_status, "info")
badge_html = ui.status_badge(kpis.overall_status, status_sev)
st.markdown(
    f'<div style="font-size:0.85rem;color:#71869B;font-weight:600;margin-bottom:1.25rem;">'
    f'Overall Health &nbsp; {badge_html}</div>',
    unsafe_allow_html=True,
)

# ── Quality Metrics ───────────────────────────────────────────────────────
ui.section_header("Metrik Kualitas")
c1, c2, c3, c4 = st.columns(4)
with c1:
    ui.metric_card("Data Completeness", format_percentage(kpis.data_completeness_percentage))
with c2:
    ui.metric_card("Valid Transaction %", format_percentage(kpis.valid_transaction_percentage))
with c3:
    ui.metric_card(
        "Duplicate Records", format_number(kpis.duplicate_count), "",
        "warning" if kpis.duplicate_count > 0 else "success",
    )
with c4:
    ui.metric_card(
        "Invalid Value", format_number(kpis.invalid_value_count), "",
        "danger" if kpis.invalid_value_count > 0 else "success",
    )

st.markdown("<br>", unsafe_allow_html=True)

c5, c6, c7, c8 = st.columns(4)
with c5:
    ui.metric_card(
        "Unusually High", format_number(kpis.unusually_high_count), "",
        "warning" if kpis.unusually_high_count > 0 else "success",
    )
with c6:
    ui.metric_card(
        "Invalid Date", format_number(kpis.invalid_date_count), "",
        "danger" if kpis.invalid_date_count > 0 else "success",
    )
with c7:
    ui.metric_card(
        "Major Rec. Issues", format_number(kpis.n_months_major_reconciliation_issue), "",
        "danger" if kpis.n_months_major_reconciliation_issue > 0 else "success",
    )
with c8:
    ui.metric_card(
        "Reconciliation Status", kpis.reconciliation_status, "",
        "success" if kpis.reconciliation_status == "HEALTHY" else "warning",
    )

st.markdown("<br><br>", unsafe_allow_html=True)

# ── Source Coverage Calendar ──────────────────────────────────────────────
ui.section_header("Kalender Cakupan Sumber")
st.caption(
    "UJB hanya authoritative pada tanggal berstatus COMPLETE. PARTIAL, FAILED, dan UNKNOWN "
    "tetap diaudit tetapi tidak boleh menggantikan Excel atau masuk ke total hybrid."
)

if source_calendar.empty:
    st.warning("Kalender coverage sumber belum tersedia.", icon="⚠️")
else:
    calendar = source_calendar.copy()
    calendar["date"] = pd.to_datetime(calendar["date"], errors="coerce")
    status_counts = calendar["source_coverage_status"].value_counts()
    known_pct = calendar["known_source_coverage"].fillna(False).mean() * 100
    cv1, cv2, cv3, cv4 = st.columns(4)
    with cv1:
        ui.metric_card("Known Coverage", format_percentage(known_pct), "Excel atau UJB COMPLETE", "info")
    with cv2:
        ui.metric_card("UJB Complete", format_number(status_counts.get("UJB_COMPLETE", 0)), "Hari authoritative", "success")
    with cv3:
        incomplete_days = int(status_counts.get("UJB_PARTIAL", 0) + status_counts.get("UJB_FAILED", 0))
        ui.metric_card("UJB Incomplete", format_number(incomplete_days), "Tidak masuk total", "danger" if incomplete_days else "success")
    with cv4:
        source_gap_days = int(status_counts.get("SOURCE_GAP", 0))
        ui.metric_card("Source Gap", format_number(source_gap_days), "Dipisahkan dari gap equipment", "warning" if source_gap_days else "success")

    unsafe_ujb = calendar[calendar["ujb_coverage_status"].isin(["PARTIAL", "FAILED", "UNKNOWN"])]
    unsafe_ujb = unsafe_ujb[unsafe_ujb["ujb_manifest_present"]]
    if not unsafe_ujb.empty:
        st.error(
            f"Ada {len(unsafe_ujb)} tanggal UJB non-authoritative. Transaksi pada tanggal tersebut "
            "dikarantina dari total hybrid.",
            icon="🛑",
        )

    with st.expander("Detail kalender coverage sumber", expanded=False):
        st.dataframe(calendar.tail(90), width="stretch", hide_index=True)
        st.download_button(
            "Download Source Coverage Calendar (CSV)",
            calendar.to_csv(index=False),
            "source_coverage_calendar.csv",
            "text/csv",
        )

st.markdown("<br><br>", unsafe_allow_html=True)

# ── Source Reconciliation Audit ───────────────────────────────────────────
ui.section_header("Audit Rekonsiliasi Sumber — Excel ↔ UJB")
st.caption(
    "Menunjukkan sumber mana yang benar-benar masuk ke total hybrid. "
    "Baris/liter yang disuppress tetap tercatat di audit, tetapi tidak ikut dijumlahkan."
)

if source_audit.empty:
    st.info("Audit source precedence belum tersedia pada hasil pipeline saat ini.")
else:
    audit = source_audit.copy()
    audit["date"] = pd.to_datetime(audit["date"], errors="coerce")

    def _source_sum(source: str, column: str) -> float:
        rows = audit[audit["source_system"].eq(source)]
        return float(pd.to_numeric(rows[column], errors="coerce").fillna(0).sum())

    excel_selected = _source_sum("EXCEL", "selected_liter")
    ujb_selected = _source_sum("UJB", "selected_liter")
    excel_suppressed = _source_sum("EXCEL", "suppressed_liter")
    ujb_suppressed = _source_sum("UJB", "suppressed_liter")

    ujb_days = audit.loc[
        audit["source_system"].eq("UJB")
        & audit["coverage_status"].eq("COMPLETE")
        & audit["input_rows"].gt(0), "date"
    ].dropna().dt.normalize().drop_duplicates().sort_values()
    coverage_label = "-"
    if not ujb_days.empty:
        coverage_label = f"{ujb_days.min():%d %b} – {ujb_days.max():%d %b %Y}"

    s1, s2, s3, s4 = st.columns(4)
    with s1:
        ui.metric_card("Excel Dipakai", format_liter(excel_selected), "Masuk total hybrid", "info")
    with s2:
        ui.metric_card("UJB Dipakai", format_liter(ujb_selected), f"Coverage {coverage_label}", "success")
    with s3:
        ui.metric_card(
            "Excel Digantikan UJB", format_liter(excel_suppressed),
            "Dicegah dari double counting", "warning" if excel_suppressed > 0 else "success",
        )
    with s4:
        ui.metric_card(
            "UJB Disuppress", format_liter(ujb_suppressed),
            "Coverage tidak lengkap / bridge / taxonomy", "warning" if ujb_suppressed > 0 else "success",
        )

    reason_summary = (
        audit.groupby(["source_system", "coverage_status", "selection_reason"], dropna=False)
        .agg(
            input_rows=("input_rows", "sum"),
            selected_rows=("selected_rows", "sum"),
            suppressed_rows=("suppressed_rows", "sum"),
            input_liter=("input_liter", "sum"),
            selected_liter=("selected_liter", "sum"),
            suppressed_liter=("suppressed_liter", "sum"),
        )
        .reset_index()
        .sort_values(["source_system", "suppressed_liter"], ascending=[True, False])
    )

    chart_df = reason_summary.groupby("source_system", as_index=False)[
        ["selected_liter", "suppressed_liter"]
    ].sum()
    chart_long = chart_df.melt(
        id_vars="source_system",
        value_vars=["selected_liter", "suppressed_liter"],
        var_name="status",
        value_name="fuel_liter",
    )
    chart_long["status"] = chart_long["status"].map({
        "selected_liter": "Dipakai",
        "suppressed_liter": "Disuppress",
    })

    fig_source = px.bar(
        chart_long,
        x="source_system",
        y="fuel_liter",
        color="status",
        barmode="stack",
        labels={"source_system": "Sumber", "fuel_liter": "Liter", "status": "Status"},
    )
    fig_source = ui.format_chart(fig_source)
    fig_source.update_layout(height=330, legend_title="")
    st.plotly_chart(fig_source, width='stretch')

    unapproved = reason_summary[
        reason_summary["selection_reason"].eq("UJB_UNAPPROVED_CATEGORY_SUPPRESSED")
        & reason_summary["suppressed_rows"].gt(0)
    ]
    if not unapproved.empty:
        st.warning(
            "Ada kategori UJB yang belum di-approve dan sengaja tidak dimasukkan ke total hybrid. "
            "Periksa tabel audit sebelum mengubah taxonomy/source precedence.",
            icon="⚠️",
        )

    incomplete = reason_summary[
        reason_summary["selection_reason"].isin([
            "UJB_PARTIAL_COVERAGE_SUPPRESSED",
            "UJB_FAILED_COVERAGE_SUPPRESSED",
            "UJB_UNKNOWN_COVERAGE_SUPPRESSED",
        ])
        & reason_summary["suppressed_rows"].gt(0)
    ]
    if not incomplete.empty:
        st.error(
            "Ada transaksi UJB dari run yang coverage-nya tidak terbukti lengkap. Transaksi tersebut "
            "sengaja tidak dimasukkan ke total hybrid.",
            icon="🛑",
        )

    with st.expander("Detail keputusan source precedence", expanded=False):
        st.dataframe(reason_summary, width="stretch", hide_index=True)
        st.download_button(
            "Download Source Reconciliation Audit (CSV)",
            source_audit.to_csv(index=False),
            "source_reconciliation_audit.csv",
            "text/csv",
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
        "recalculated_total": "#3977C8",
    },
)
fig = ui.format_chart(fig)
fig.update_layout(height=360, legend_title="")
st.plotly_chart(fig, width='stretch')

with st.expander("Tabel Detail Rekonsiliasi Bulanan"):
    st.dataframe(monthly, width="stretch", hide_index=True)

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
    width="stretch", hide_index=True,
)

st.markdown("<br><br>", unsafe_allow_html=True)

# ── Issue Tables ──────────────────────────────────────────────────────────
col1, col2 = st.columns(2)
with col1:
    ui.section_header("Ringkasan Isu Data")
    st.dataframe(cleaning["data_quality_report"], width="stretch", hide_index=True)

with col2:
    ui.section_header("Jeda Pengisian Panjang")
    st.caption("Equipment dengan jeda pengisian panjang; hari source gap sudah dikeluarkan dari perhitungan.")
    st.dataframe(
        dq["zero_streaks"][[
            "equipment_category", "equipment_id",
            "longest_gap_days", "longest_gap_start", "longest_gap_end",
        ]].head(20),
        width="stretch", hide_index=True,
    )

if not dq["missing_dates"].empty:
    with st.expander("Source Coverage Gap", expanded=False):
        st.caption("Hari ini tidak dihitung sebagai zero-consumption equipment karena sumbernya tidak diketahui.")
        st.dataframe(dq["missing_dates"], width="stretch", hide_index=True)

st.download_button(
    "Download Data Quality Report (CSV)",
    cleaning["data_quality_report"].to_csv(index=False),
    "data_quality_report.csv", "text/csv",
)

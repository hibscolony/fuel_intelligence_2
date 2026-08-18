"""
app.py
======
Entry point JICT Fuel Intelligence Dashboard. Jalankan dengan:

    streamlit run app.py

Navigasi 7 halaman didefinisikan eksplisit lewat st.navigation/st.Page
(bukan auto-discovery folder pages/) supaya urutan & label tab terkontrol
penuh terlepas dari nama file atau versi Streamlit yang dipakai.
"""
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config
from src import ui

st.set_page_config(
    page_title="JICT Fuel Intelligence",
    page_icon="⛽",
    layout="wide",
    initial_sidebar_state="expanded",
)

ui.inject_global_css()

# =============================================================================
# Auto-refresh session state
# =============================================================================
if "raw_data_fingerprint" not in st.session_state:
    st.session_state.raw_data_fingerprint = config.get_raw_data_fingerprint()
if "last_refreshed_at" not in st.session_state:
    st.session_state.last_refreshed_at = datetime.now()
if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = True

# Global application header
ui.global_header(st.session_state.last_refreshed_at.strftime('%H:%M:%S'))

# =============================================================================
# Sidebar
# =============================================================================
with st.sidebar:
    # Brand
    st.html("""
    <div class="jict-sidebar-brand">
        <span class="jict-sidebar-brand-name">JICT</span>
        <span class="jict-sidebar-brand-sub">Fuel Intelligence</span>
    </div>
    """)

    # st.navigation renders the nav links automatically — just add group labels
    st.html('<span class="jict-nav-group">Overview</span>')
    # (navigation items will be injected by st.navigation below)

    st.html("""
    <div style="height: 100px;"></div>
    """)

    st.divider()

    # Bottom controls
    st.toggle(
        "🌙  Dark Mode",
        key="dark_mode",
        help="Aktifkan mode gelap (Dark Navy) untuk tampilan malam.",
    )
    st.toggle(
        "Auto-refresh data",
        key="auto_refresh_enabled",
        help="Cek otomatis tiap 20 detik apakah file laporan solar di data/raw/ berubah.",
    )
    if st.button("⟳  Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.session_state.raw_data_fingerprint = config.get_raw_data_fingerprint()
        st.session_state.last_refreshed_at = datetime.now()
        st.rerun()

    st.caption("JICT Fuel Intelligence · Prototype · Operations Support")


@st.fragment(run_every="20s")
def _watch_raw_data_changes():
    """Fragment ringan yang berjalan sendiri tiap 20 detik."""
    if not st.session_state.auto_refresh_enabled:
        return
    current_fp = config.get_raw_data_fingerprint()
    if current_fp != st.session_state.raw_data_fingerprint:
        st.session_state.raw_data_fingerprint = current_fp
        st.session_state.last_refreshed_at = datetime.now()
        st.cache_data.clear()
        st.toast("Data sumber baru terdeteksi — dashboard diperbarui otomatis.", icon="🔄")
        st.rerun()


_watch_raw_data_changes()

pages = {
    "OVERVIEW": [
        st.Page("pages/executive_overview.py", title="Executive Overview", icon="📊", default=True),
    ],
    "ANALYTICS": [
        st.Page("pages/consumption_explorer.py", title="Konsumsi Detail", icon="🔍"),
        st.Page("pages/forecast_monitoring.py",  title="Forecast Monitoring", icon="📈"),
        st.Page("pages/anomaly_monitoring.py",   title="Fuel Anomaly", icon="⚠️"),
        st.Page("pages/equipment_health.py",     title="Equipment Health", icon="🩺"),
    ],
    "GOVERNANCE": [
        st.Page("pages/data_quality.py", title="Data Quality", icon="🔎"),
    ],
    "DECISION SUPPORT": [
        st.Page("pages/saving_simulator.py", title="Saving Simulator", icon="💰"),
        st.Page("pages/recommendations.py", title="Recommendations", icon="✅"),
    ],
}

nav = st.navigation(pages)
nav.run()

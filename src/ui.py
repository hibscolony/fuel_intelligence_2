"""
src/ui.py
=========
JICT Fuel Intelligence — Central Design System
Supports Light Blue Enterprise Analytics & Sleek Dark Navy Mode
"""
import streamlit as st

# =============================================================================
# DESIGN TOKENS
# =============================================================================
_COLORS_LIGHT = {
    "bg_main":       "#F5F9FF",
    "bg_alt":        "#EEF6FF",
    "sidebar_bg":    "#EAF4FF",
    "sidebar_active":"#CFE6FF",
    "sidebar_hover": "#DDEEFF",
    "sidebar_text":  "#35566F",
    "sidebar_group": "#7890A5",
    "primary":       "#3977C8",
    "accent":        "#5B9BE6",
    "soft_blue":     "#BFDDF8",
    "very_light":    "#EFF7FF",
    "card":          "#FFFFFF",
    "border":        "#DCE8F5",
    "text_primary":  "#18324B",
    "text_secondary":"#71869B",
    "text_muted":    "#94A3B8",
    "success":       "#22A06B",
    "success_bg":    "#E6F6EF",
    "success_border":"#A8DFCA",
    "warning":       "#E8A317",
    "warning_bg":    "#FEF5E7",
    "warning_border":"#F5CC80",
    "danger":        "#D94C4C",
    "danger_bg":     "#FDEAEA",
    "danger_border": "#F2ABAB",
    "info_bg":       "#EBF4FF",
    "info_border":   "#AACEF0",
    "grid":          "#EAF0F6",
}

_COLORS_DARK = {
    "bg_main":       "#0B1726",
    "bg_alt":        "#112236",
    "sidebar_bg":    "#0E1D2F",
    "sidebar_active":"#1D3958",
    "sidebar_hover": "#172E47",
    "sidebar_text":  "#B2CEE6",
    "sidebar_group": "#6885A3",
    "primary":       "#4E8FE0",
    "accent":        "#6BA7F2",
    "soft_blue":     "#1C3B5E",
    "very_light":    "#14263B",
    "card":          "#13253B",
    "border":        "#1F3B5B",
    "text_primary":  "#F0F6FC",
    "text_secondary":"#9DB2C7",
    "text_muted":    "#68829E",
    "success":       "#2ECE89",
    "success_bg":    "rgba(46, 206, 137, 0.15)",
    "success_border":"rgba(46, 206, 137, 0.3)",
    "warning":       "#F3B738",
    "warning_bg":    "rgba(243, 183, 56, 0.15)",
    "warning_border":"rgba(243, 183, 56, 0.3)",
    "danger":        "#F06A6A",
    "danger_bg":     "rgba(240, 106, 106, 0.15)",
    "danger_border":"rgba(240, 106, 106, 0.3)",
    "info_bg":       "rgba(78, 143, 224, 0.15)",
    "info_border":   "rgba(78, 143, 224, 0.3)",
    "grid":          "#1A2F45",
}


def get_colors() -> dict:
    """Returns active color tokens based on theme toggle."""
    is_dark = st.session_state.get("dark_mode", False)
    return _COLORS_DARK if is_dark else _COLORS_LIGHT


# =============================================================================
# GLOBAL CSS INJECTION
# =============================================================================
def inject_global_css():
    """Inject the complete JICT design system CSS dynamically based on theme."""
    _c = get_colors()

    css = f"""
    <style>
    /* ── Font ── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    /* ── Root Variables ── */
    :root {{
        --jict-bg:           {_c["bg_main"]};
        --jict-bg-alt:       {_c["bg_alt"]};
        --jict-card:         {_c["card"]};
        --jict-border:       {_c["border"]};
        --jict-primary:      {_c["primary"]};
        --jict-accent:       {_c["accent"]};
        --jict-text:         {_c["text_primary"]};
        --jict-text-sec:     {_c["text_secondary"]};
        --jict-text-muted:   {_c["text_muted"]};
        --jict-success:      {_c["success"]};
        --jict-warning:      {_c["warning"]};
        --jict-danger:       {_c["danger"]};
        --radius-lg:         16px;
        --radius-md:         12px;
        --radius-sm:         8px;
        --shadow-card:       0 2px 12px rgba(0, 0, 0, 0.12);
        --shadow-hover:      0 6px 20px rgba(0, 0, 0, 0.18);
    }}

    /* ── Global App Background ── */
    .stApp {{
        background-color: var(--jict-bg) !important;
        font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
        color: var(--jict-text);
    }}

    /* ── Block container ── */
    .block-container {{
        padding-top: 2rem !important;
        padding-bottom: 3rem !important;
        padding-left: 2.5rem !important;
        padding-right: 2.5rem !important;
        max-width: 1500px !important;
    }}

    /* ── Hide Streamlit fixed toolbar completely ── */
    [data-testid="stHeader"] {{
        display: none !important;
    }}
    [data-testid="stAppViewContainer"] {{
        padding-top: 0 !important;
    }}
    [data-testid="stSidebar"] {{
        top: 0 !important;
    }}

    /* ── Sidebar Theme Styling ── */
    [data-testid="stSidebar"] {{
        background-color: {_c["sidebar_bg"]} !important;
        border-right: 1px solid {_c["border"]};
    }}
    [data-testid="stSidebar"] * {{
        color: {_c["sidebar_text"]} !important;
    }}
    [data-testid="stSidebar"] hr {{
        border-color: {_c["border"]} !important;
    }}
    /* Nav items */
    [data-testid="stSidebarNav"] a {{
        border-radius: var(--radius-sm) !important;
        padding: 6px 10px !important;
        transition: background 0.15s ease !important;
        font-size: 0.875rem !important;
        font-weight: 500 !important;
    }}
    [data-testid="stSidebarNav"] a:hover {{
        background: {_c["sidebar_hover"]} !important;
    }}
    [data-testid="stSidebarNav"] a[aria-current="page"] {{
        background: {_c["sidebar_active"]} !important;
        color: {_c["primary"]} !important;
        border-left: 3px solid {_c["primary"]} !important;
        font-weight: 600 !important;
    }}
    [data-testid="stSidebarNav"] a[aria-current="page"] * {{
        color: {_c["primary"]} !important;
    }}

    /* ── Application Global Header ── */
    .jict-app-header {{
        background: {_c["card"]};
        border: 1px solid {_c["border"]};
        border-radius: var(--radius-lg);
        padding: 1rem 1.5rem;
        margin-bottom: 1.75rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        box-shadow: var(--shadow-card);
    }}
    .jict-app-header-brand {{
        display: flex;
        flex-direction: column;
        gap: 2px;
    }}
    .jict-app-header-title {{
        font-size: 1.1rem;
        font-weight: 700;
        color: {_c["text_primary"]};
        letter-spacing: -0.01em;
        margin: 0;
    }}
    .jict-app-header-sub {{
        font-size: 0.78rem;
        color: {_c["text_muted"]};
        font-weight: 400;
        margin: 0;
    }}
    .jict-app-header-status {{
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 3px;
    }}
    .jict-status-dot {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-size: 0.75rem;
        font-weight: 600;
        color: {_c["success"]};
        letter-spacing: 0.04em;
    }}
    .jict-status-dot::before {{
        content: '';
        width: 7px;
        height: 7px;
        background: {_c["success"]};
        border-radius: 50%;
        box-shadow: 0 0 0 2px {_c["success_bg"]};
    }}
    .jict-header-time {{
        font-size: 0.72rem;
        color: {_c["text_muted"]};
    }}

    /* ── Page Header ── */
    .jict-page-header {{
        margin-bottom: 1.75rem;
    }}
    .jict-page-title {{
        font-size: 1.9rem;
        font-weight: 700;
        color: {_c["text_primary"]};
        letter-spacing: -0.02em;
        margin: 0 0 6px 0;
        line-height: 1.2;
    }}
    .jict-page-desc {{
        font-size: 0.93rem;
        color: {_c["text_secondary"]};
        margin: 0 0 4px 0;
        line-height: 1.55;
    }}
    .jict-page-meta {{
        font-size: 0.78rem;
        color: {_c["text_muted"]};
        font-style: italic;
        margin: 0;
    }}

    /* ── Section Card Container ── */
    .jict-section-card {{
        background: {_c["card"]};
        border: 1px solid {_c["border"]};
        border-radius: var(--radius-lg);
        padding: 1.25rem 1.5rem;
        margin-bottom: 1.5rem;
        box-shadow: var(--shadow-card);
    }}
    .jict-section-title {{
        font-size: 0.72rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: {_c["text_muted"]};
        margin: 0 0 1rem 0;
        padding-bottom: 0.6rem;
        border-bottom: 1px solid {_c["border"]};
    }}

    /* ── Section Header (inline) ── */
    .jict-section-hdr {{
        font-size: 0.8rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: {_c["text_muted"]};
        margin: 1.5rem 0 0.85rem 0;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid {_c["border"]};
    }}

    /* ── Metric Cards ── */
    .jm-card {{
        background: {_c["card"]};
        border: 1px solid {_c["border"]};
        border-radius: var(--radius-lg);
        padding: 1.1rem 1.25rem;
        box-shadow: var(--shadow-card);
        height: 100%;
        position: relative;
        overflow: hidden;
        transition: box-shadow 0.15s ease, transform 0.15s ease;
    }}
    .jm-card:hover {{
        box-shadow: var(--shadow-hover);
        transform: translateY(-1px);
    }}
    .jm-card-accent-top {{
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
        border-radius: var(--radius-lg) var(--radius-lg) 0 0;
    }}
    .jm-label {{
        font-size: 0.7rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: {_c["text_muted"]};
        margin-bottom: 0.5rem;
    }}
    .jm-value {{
        font-size: 1.65rem;
        font-weight: 700;
        color: {_c["text_primary"]};
        letter-spacing: -0.02em;
        line-height: 1.15;
        margin-bottom: 0.3rem;
    }}
    .jm-sub {{
        font-size: 0.78rem;
        color: {_c["text_muted"]};
        line-height: 1.4;
    }}
    .jm-sub-success {{ color: {_c["success"]}; font-weight: 600; }}
    .jm-sub-danger  {{ color: {_c["danger"]};  font-weight: 600; }}
    .jm-sub-warning {{ color: {_c["warning"]}; font-weight: 600; }}
    .jm-sub-info    {{ color: {_c["primary"]}; font-weight: 600; }}

    /* ── Status Badges ── */
    .jict-badge {{
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 4px 10px;
        border-radius: 999px;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        white-space: nowrap;
    }}
    .jict-badge::before {{
        content: '●';
        font-size: 0.6rem;
    }}
    .jb-success {{
        background: {_c["success_bg"]};
        color: {_c["success"]};
        border: 1px solid {_c["success_border"]};
    }}
    .jb-warning {{
        background: {_c["warning_bg"]};
        color: {_c["warning"]};
        border: 1px solid {_c["warning_border"]};
    }}
    .jb-danger {{
        background: {_c["danger_bg"]};
        color: {_c["danger"]};
        border: 1px solid {_c["danger_border"]};
    }}
    .jb-info {{
        background: {_c["info_bg"]};
        color: {_c["primary"]};
        border: 1px solid {_c["info_border"]};
    }}
    .jb-neutral {{
        background: {_c["bg_alt"]};
        color: {_c["text_secondary"]};
        border: 1px solid {_c["border"]};
    }}

    /* ── Insight / Info Cards ── */
    .jict-insight {{
        background: {_c["info_bg"]};
        border: 1px solid {_c["info_border"]};
        border-left: 4px solid {_c["primary"]};
        border-radius: var(--radius-md);
        padding: 0.9rem 1.1rem;
        margin-bottom: 0.75rem;
    }}
    .jict-insight-title {{
        font-size: 0.85rem;
        font-weight: 700;
        color: {_c["text_primary"]};
        margin-bottom: 0.35rem;
    }}
    .jict-insight-body {{
        font-size: 0.83rem;
        color: {_c["text_secondary"]};
        line-height: 1.55;
    }}

    /* ── Chart Card ── */
    .jict-chart-card {{
        background: {_c["card"]};
        border: 1px solid {_c["border"]};
        border-radius: var(--radius-lg);
        padding: 1.25rem 1.5rem 0.75rem 1.5rem;
        box-shadow: var(--shadow-card);
        margin-bottom: 1.5rem;
    }}
    .jict-chart-card-title {{
        font-size: 0.85rem;
        font-weight: 700;
        color: {_c["text_primary"]};
        margin-bottom: 0.2rem;
    }}
    .jict-chart-card-desc {{
        font-size: 0.77rem;
        color: {_c["text_muted"]};
        margin-bottom: 1rem;
    }}

    /* ── Streamlit Component Overrides ── */

    /* Widget Labels (All Input Field Titles) */
    [data-testid="stWidgetLabel"],
    [data-testid="stWidgetLabel"] label,
    [data-testid="stWidgetLabel"] p,
    .stWidgetLabel p,
    .stWidgetLabel label,
    .stSelectbox label p,
    .stDateInput label p,
    .stRadio label p,
    .stNumberInput label p,
    .stTextInput label p,
    .stMultiSelect label p {{
        color: {_c["text_primary"]} !important;
        font-weight: 600 !important;
        font-size: 0.85rem !important;
    }}

    /* Selectbox */
    .stSelectbox > div > div,
    .stSelectbox div[data-baseweb="select"] {{
        background: {_c["card"]} !important;
        border: 1px solid {_c["border"]} !important;
        border-radius: var(--radius-md) !important;
        color: {_c["text_primary"]} !important;
        min-height: 44px !important;
    }}
    .stSelectbox div[data-baseweb="select"] * {{
        color: {_c["text_primary"]} !important;
    }}
    .stSelectbox > div > div:focus-within {{
        border-color: {_c["primary"]} !important;
        box-shadow: 0 0 0 3px rgba(78, 143, 224, 0.2) !important;
    }}

    /* Selectbox Dropdown Menu */
    [data-baseweb="menu"],
    div[data-baseweb="popover"] {{
        background-color: {_c["card"]} !important;
        border: 1px solid {_c["border"]} !important;
        border-radius: var(--radius-md) !important;
    }}
    [data-baseweb="menu"] li,
    [data-baseweb="menu"] div {{
        color: {_c["text_primary"]} !important;
        background-color: {_c["card"]} !important;
    }}
    [data-baseweb="menu"] li:hover {{
        background-color: {_c["sidebar_hover"]} !important;
    }}

    /* Date Input */
    .stDateInput input,
    [data-testid="stDateInput"] input,
    [data-testid="stDateInput"] div[data-baseweb="input"],
    [data-baseweb="input"] input {{
        background-color: {_c["card"]} !important;
        border: 1px solid {_c["border"]} !important;
        border-radius: var(--radius-md) !important;
        color: {_c["text_primary"]} !important;
        min-height: 42px !important;
    }}
    [data-testid="stDateInput"] svg,
    [data-testid="stDateInput"] button {{
        fill: {_c["text_secondary"]} !important;
        color: {_c["text_secondary"]} !important;
    }}
    /* DatePicker Calendar Popover */
    div[data-baseweb="calendar"] {{
        background-color: {_c["card"]} !important;
        color: {_c["text_primary"]} !important;
        border: 1px solid {_c["border"]} !important;
        border-radius: var(--radius-md) !important;
    }}
    div[data-baseweb="calendar"] * {{
        color: {_c["text_primary"]} !important;
    }}

    /* Radio Buttons */
    [data-testid="stRadio"] label,
    [data-testid="stRadio"] label p,
    [data-testid="stRadio"] label span,
    [data-testid="stRadio"] div[role="radiogroup"] label {{
        color: {_c["text_primary"]} !important;
    }}

    /* Number inputs, text inputs */
    .stNumberInput input, .stTextInput input {{
        background: {_c["card"]} !important;
        border: 1px solid {_c["border"]} !important;
        border-radius: var(--radius-md) !important;
        color: {_c["text_primary"]} !important;
        min-height: 42px !important;
    }}
    .stNumberInput input:focus, .stTextInput input:focus {{
        border-color: {_c["primary"]} !important;
        box-shadow: 0 0 0 3px rgba(78, 143, 224, 0.2) !important;
    }}

    /* Primary Buttons */
    .stButton > button[kind="primary"] {{
        background: {_c["primary"]} !important;
        color: white !important;
        border: none !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        padding: 0.45rem 1.1rem !important;
        transition: background 0.15s ease, box-shadow 0.15s ease !important;
    }}
    .stButton > button[kind="primary"]:hover {{
        background: {_c["accent"]} !important;
        box-shadow: 0 3px 10px rgba(78, 143, 224, 0.3) !important;
    }}

    /* Secondary / Default Buttons */
    .stButton > button {{
        background: {_c["card"]} !important;
        color: {_c["primary"]} !important;
        border: 1px solid {_c["border"]} !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        padding: 0.45rem 1.1rem !important;
        transition: background 0.15s ease !important;
    }}
    .stButton > button:hover {{
        background: {_c["sidebar_hover"]} !important;
        border-color: {_c["accent"]} !important;
    }}

    /* Sidebar specific button (refresh) */
    [data-testid="stSidebar"] .stButton > button {{
        background: {_c["card"]} !important;
        color: {_c["primary"]} !important;
        border: 1px solid {_c["border"]} !important;
        font-size: 0.82rem !important;
        font-weight: 600 !important;
    }}
    [data-testid="stSidebar"] .stButton > button:hover {{
        background: {_c["sidebar_hover"]} !important;
    }}

    /* Expander Container & Header */
    [data-testid="stExpander"],
    .stExpander {{
        background-color: {_c["card"]} !important;
        border: 1px solid {_c["border"]} !important;
        border-radius: var(--radius-md) !important;
        overflow: hidden !important;
    }}

    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] details summary,
    .streamlit-expanderHeader {{
        background-color: {_c["card"]} !important;
        color: {_c["text_primary"]} !important;
        border-bottom: 1px solid {_c["border"]} !important;
        border-radius: var(--radius-md) !important;
        padding: 0.75rem 1.25rem !important;
    }}

    [data-testid="stExpander"] summary:hover,
    .streamlit-expanderHeader:hover {{
        background-color: {_c["sidebar_hover"]} !important;
    }}

    [data-testid="stExpander"] summary *,
    [data-testid="stExpander"] summary p,
    [data-testid="stExpander"] summary span,
    [data-testid="stExpander"] summary svg,
    .streamlit-expanderHeader * {{
        color: {_c["text_primary"]} !important;
        fill: {_c["text_primary"]} !important;
        font-weight: 600 !important;
    }}

    [data-testid="stExpanderDetails"],
    [data-testid="stExpander"] div[role="region"],
    .streamlit-expanderContent {{
        background-color: {_c["card"]} !important;
        color: {_c["text_primary"]} !important;
        padding: 1.25rem !important;
    }}

    /* Dataframe */
    [data-testid="stDataFrame"] {{
        border: 1px solid {_c["border"]} !important;
        border-radius: var(--radius-md) !important;
        overflow: hidden !important;
    }}

    /* Metrics (native st.metric — hide if accidentally used) */
    [data-testid="stMetricValue"] {{
        color: {_c["text_primary"]};
        font-size: 1.5rem;
    }}

    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {{
        background: {_c["bg_alt"]};
        border-radius: var(--radius-md);
        padding: 4px;
        gap: 4px;
        border: 1px solid {_c["border"]};
    }}
    .stTabs [data-baseweb="tab"] {{
        border-radius: var(--radius-sm) !important;
        color: {_c["text_secondary"]} !important;
        font-weight: 500 !important;
    }}
    .stTabs [aria-selected="true"] {{
        background: {_c["card"]} !important;
        color: {_c["primary"]} !important;
        font-weight: 700 !important;
    }}

    /* Toggle */
    [data-testid="stToggle"] label {{
        font-size: 0.83rem !important;
        color: {_c["sidebar_text"]} !important;
    }}

    /* Caption */
    .stCaption, [data-testid="stCaptionContainer"] {{
        color: {_c["text_muted"]} !important;
        font-size: 0.78rem !important;
    }}

    /* Download button */
    .stDownloadButton > button {{
        background: {_c["card"]} !important;
        color: {_c["primary"]} !important;
        border: 1px solid {_c["border"]} !important;
        border-radius: 10px !important;
        font-size: 0.83rem !important;
        font-weight: 600 !important;
    }}

    /* Sidebar group labels */
    .jict-nav-group {{
        font-size: 0.68rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: {_c["sidebar_group"]} !important;
        margin: 1.2rem 0 0.35rem 0.75rem;
        display: block;
    }}

    /* Sidebar brand area */
    .jict-sidebar-brand {{
        padding: 0.5rem 0 0.75rem 0;
        border-bottom: 1px solid {_c["border"]};
        margin-bottom: 0.5rem;
    }}
    .jict-sidebar-brand-name {{
        font-size: 1rem;
        font-weight: 700;
        color: {_c["text_primary"]} !important;
        display: block;
    }}
    .jict-sidebar-brand-sub {{
        font-size: 0.75rem;
        color: {_c["text_muted"]} !important;
        margin-top: 2px;
        display: block;
    }}

    /* Remove default Streamlit metric delta arrows etc if shown */
    [data-testid="stMetricDelta"] {{ display: none; }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


# =============================================================================
# COMPONENT FUNCTIONS
# =============================================================================

def global_header(last_refreshed_time: str = ""):
    """Renders the application-level header bar."""
    time_str = f"Updated {last_refreshed_time}" if last_refreshed_time else ""
    html = f"""
    <div class="jict-app-header">
        <div class="jict-app-header-brand">
            <div class="jict-app-header-title">JICT Fuel Intelligence</div>
            <div class="jict-app-header-sub">Operations Decision Support Platform</div>
        </div>
        <div class="jict-app-header-status">
            <div class="jict-status-dot">System Online</div>
            <div class="jict-header-time">{time_str}</div>
        </div>
    </div>
    """
    st.html(html)


def page_header(title: str, description: str = "", context: str = ""):
    """Renders a standardized page title block."""
    parts = [f'<div class="jict-page-header">']
    parts.append(f'<h1 class="jict-page-title">{title}</h1>')
    if description:
        parts.append(f'<p class="jict-page-desc">{description}</p>')
    if context:
        parts.append(f'<p class="jict-page-meta">{context}</p>')
    parts.append('</div>')
    st.html("".join(parts))


def section_header(title: str):
    """Inline section header with uppercase label style."""
    st.html(f'<div class="jict-section-hdr">{title}</div>')


def metric_card(label: str, value: str, subtext: str = "", status: str = ""):
    """
    Renders a KPI metric card.
    status: 'success' | 'danger' | 'warning' | 'info' | '' (neutral)
    """
    _c = get_colors()
    accent_colors = {
        "success": _c["success"],
        "danger":  _c["danger"],
        "warning": _c["warning"],
        "info":    _c["primary"],
    }
    sub_classes = {
        "success": "jm-sub-success",
        "danger":  "jm-sub-danger",
        "warning": "jm-sub-warning",
        "info":    "jm-sub-info",
    }
    accent_color = accent_colors.get(status, _c["primary"])
    sub_class = sub_classes.get(status, "")

    accent_line = f'<div class="jm-card-accent-top" style="background:{accent_color};"></div>' if status else ""
    sub_html = f'<div class="jm-sub {sub_class}">{subtext}</div>' if subtext else ""

    html = (
        f'<div class="jm-card">'
        f'{accent_line}'
        f'<div class="jm-label">{label}</div>'
        f'<div class="jm-value jict-metric-value">{value}</div>'
        f'{sub_html}'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def status_badge(text: str, severity: str = "info") -> str:
    """Returns inline HTML for a status badge (used inside st.markdown)."""
    sev = severity.lower()
    cls_map = {
        "success": "jb-success", "healthy": "jb-success", "pass": "jb-success",
        "warning": "jb-warning", "watch":   "jb-warning", "amber": "jb-warning",
        "danger":  "jb-danger",  "critical":"jb-danger",  "failed":"jb-danger",
        "info":    "jb-info",    "low":     "jb-neutral",
    }
    cls = cls_map.get(sev, "jb-info")
    return f'<span class="jict-badge {cls}">{text}</span>'


def insight_card(title: str, content: str):
    """Renders a highlighted insight/recommendation card."""
    html = (
        f'<div class="jict-insight">'
        f'<div class="jict-insight-title">{title}</div>'
        f'<div class="jict-insight-body">{content}</div>'
        f'</div>'
    )
    st.html(html)


def chart_card_header(title: str, description: str = ""):
    """Renders a chart card header (use before st.plotly_chart)."""
    desc_html = f'<div class="jict-chart-card-desc">{description}</div>' if description else ""
    html = (
        f'<div class="jict-chart-card-title">{title}</div>'
        f'{desc_html}'
    )
    st.html(html)


def format_chart(fig):
    """
    Standardize Plotly chart styling to match active theme (Light/Dark).
    """
    _c = get_colors()
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(
            family="Inter, ui-sans-serif, system-ui, sans-serif",
            color=_c["text_secondary"],
            size=12,
        ),
        title=dict(font=dict(color=_c["text_primary"], size=13, family="Inter")),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=11, color=_c["text_secondary"]),
            bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(l=8, r=8, t=36, b=8),
        hoverlabel=dict(
            bgcolor=_c["card"],
            bordercolor=_c["border"],
            font_color=_c["text_primary"],
        ),
    )
    fig.update_xaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor=_c["grid"],
        zeroline=False,
        showline=True,
        linewidth=1,
        linecolor=_c["border"],
        tickfont=dict(size=11, color=_c["text_muted"]),
        title_font=dict(size=11, color=_c["text_secondary"]),
    )
    fig.update_yaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor=_c["grid"],
        zeroline=False,
        showline=False,
        tickfont=dict(size=11, color=_c["text_muted"]),
        title_font=dict(size=11, color=_c["text_secondary"]),
    )
    return fig

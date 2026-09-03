"""
formatting.py
=============
Helper pemformatan angka untuk dashboard (Rupiah, liter, persentase) --
dipusatkan di sini supaya konsisten di semua halaman.
"""
from __future__ import annotations

import math
from datetime import datetime


RECOMMENDATION_EVIDENCE_LABELS = {
    "health_score": "Health score",
    "critical_anomaly_count": "Anomali kritis",
    "trend_percentage": "Tren konsumsi",
    "gap_to_target": "Gap terhadap target",
    "required_daily_reduction": "Pengurangan yang diperlukan",
    "anomaly_count": "Jumlah anomali",
    "data_completeness": "Kelengkapan data",
    "change_point_penalty": "Skor perubahan pola",
    "last_change_point": "Perubahan terakhir",
}

RECOMMENDATION_ROLE_LABELS = {
    "Operations Admin": "Admin Operasional",
    "Equipment": "Tim Equipment",
    "Maintenance": "Maintenance",
    "Fuel Administration": "Admin BBM",
    "Supervisor": "Supervisor",
    "ICT/Data Team": "Tim ICT/Data",
}


def format_liter(value, decimals: int = 0) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value:,.{decimals}f} L"


def format_rupiah(value, decimals: int = 0) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"Rp {value:,.{decimals}f}"


def format_percentage(value, decimals: int = 1) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value:,.{decimals}f}%"


def format_number(value, decimals: int = 0) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value:,.{decimals}f}"


def format_recommendation_evidence(evidence: str) -> str:
    """Translate evidence keys while preserving the rule engine's values."""
    items = []
    for item in str(evidence).split(", "):
        key, separator, value = item.partition("=")
        label = RECOMMENDATION_EVIDENCE_LABELS.get(key, key.replace("_", " ").title())
        items.append(f"{label}: {value}" if separator else item)
    return " · ".join(items)


def format_recommendation_role(role: str) -> str:
    return RECOMMENDATION_ROLE_LABELS.get(str(role), str(role))


def format_date_label(value) -> str:
    """Format ISO-like dates for compact operational display."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(value)
    return parsed.strftime("%d %b %Y")


def severity_color(severity: str) -> str:
    return {
        "NORMAL": "#2ca02c", "LOW": "#8dd3c7", "MEDIUM": "#ffcc00",
        "HIGH": "#ff7f0e", "CRITICAL": "#d62728", "INSUFFICIENT_DATA": "#999999",
    }.get(severity, "#999999")


def health_status_color(status: str) -> str:
    return {
        "HEALTHY": "#2ca02c", "MONITOR": "#ffcc00", "REVIEW": "#ff7f0e",
        "CRITICAL": "#d62728", "INSUFFICIENT_DATA": "#999999",
    }.get(status, "#999999")


def priority_color(priority: str) -> str:
    return {"HIGH": "#d62728", "MEDIUM": "#ff7f0e", "LOW": "#2ca02c"}.get(priority, "#999999")

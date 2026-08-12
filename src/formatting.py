"""
formatting.py
=============
Helper pemformatan angka untuk dashboard (Rupiah, liter, persentase) --
dipusatkan di sini supaya konsisten di semua halaman.
"""
from __future__ import annotations

import math


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

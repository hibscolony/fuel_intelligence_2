"""Selection of one explicit reporting period for dashboard totals."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


_MONTHS_ID = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "Mei", 6: "Jun",
    7: "Jul", 8: "Agu", 9: "Sep", 10: "Okt", 11: "Nov", 12: "Des",
}


def _format_date_id(value: pd.Timestamp) -> str:
    return f"{value.day} {_MONTHS_ID[value.month]} {value.year}"


@dataclass(frozen=True)
class ReportingPeriod:
    year: int
    data: pd.DataFrame
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    n_calendar_days: int
    n_observed_days: int
    missing_calendar_days: int
    total_liter: float
    is_complete_year: bool
    label: str


def available_reporting_years(cleaned: pd.DataFrame) -> list[int]:
    """Return sorted years that have at least one valid dated source row."""
    if "date" not in cleaned.columns:
        return []
    dates = pd.to_datetime(cleaned["date"], errors="coerce").dropna()
    return sorted(int(year) for year in dates.dt.year.unique())


def default_reporting_year(cleaned: pd.DataFrame) -> int:
    """Prefer the latest complete year; otherwise use the latest partial year."""
    years = available_reporting_years(cleaned)
    if not years:
        raise ValueError("Tidak ada tahun laporan yang tersedia.")
    for year in reversed(years):
        if select_reporting_period(cleaned, year).is_complete_year:
            return year
    return years[-1]


def select_reporting_period(cleaned: pd.DataFrame, year: int) -> ReportingPeriod:
    """Select exactly one year and describe its observed calendar coverage."""
    required = {"date", "fuel_liter", "data_status"}
    missing = sorted(required.difference(cleaned.columns))
    if missing:
        raise ValueError(f"Kolom periode laporan tidak lengkap: {missing}")

    year = int(year)
    data = cleaned.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data = data[
        data["date"].notna()
        & data["date"].dt.year.eq(year)
        & data["data_status"].ne("INVALID_DATE")
    ].copy()
    if data.empty:
        raise ValueError(f"Tidak ada data bertanggal valid untuk tahun {year}.")

    start_date = pd.Timestamp(data["date"].min()).normalize()
    end_date = pd.Timestamp(data["date"].max()).normalize()
    n_calendar_days = int((end_date - start_date).days + 1)
    n_observed_days = int(data["date"].dt.normalize().nunique())
    expected_year_days = int((pd.Timestamp(year + 1, 1, 1) - pd.Timestamp(year, 1, 1)).days)
    is_complete_year = (
        start_date == pd.Timestamp(year=year, month=1, day=1)
        and end_date == pd.Timestamp(year=year, month=12, day=31)
        and n_observed_days == expected_year_days
    )
    if is_complete_year:
        label = str(year)
    elif start_date.month == 1 and start_date.day == 1:
        label = f"{year} YTD (s.d. {_format_date_id(end_date)})"
    else:
        label = f"{year} parsial ({_format_date_id(start_date)}–{_format_date_id(end_date)})"

    total_liter = float(pd.to_numeric(data["fuel_liter"], errors="coerce").sum())
    return ReportingPeriod(
        year=year,
        data=data,
        start_date=start_date,
        end_date=end_date,
        n_calendar_days=n_calendar_days,
        n_observed_days=n_observed_days,
        missing_calendar_days=n_calendar_days - n_observed_days,
        total_liter=total_liter,
        is_complete_year=is_complete_year,
        label=label,
    )

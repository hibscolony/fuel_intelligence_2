"""
change_point.py
================
Deteksi perubahan pola konsumsi yang MENETAP (bukan lonjakan sesaat -- itu
sudah ditangani anomaly_detection.py) per equipment, memakai:

1. PELT (Pruned Exact Linear Time, dari `ruptures`) dengan model "rbf" --
   menangkap perubahan level (mean) MAUPUN sebaran (variance) sekaligus.
2. Rolling-mean-shift sebagai sinyal penguat independen: dipakai untuk
   menambah `confidence` saat kedua metode sepakat ada breakpoint di sekitar
   titik yang sama.

Equipment dengan observasi < config.CHANGE_POINT_MIN_OBSERVATIONS TIDAK
diproses (bukan dipaksakan) -- terlalu sedikit data untuk memisahkan
"perubahan pola menetap" dari sekadar noise.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import ruptures as rpt

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config


def _build_equipment_series(cleaned: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    """Kembalikan {(kategori, equipment_id): DataFrame[date, fuel_liter]}
    terurut tanggal, hanya baris dgn nilai liter numerik (VALID/UNUSUALLY_HIGH).
    """
    valid = cleaned[cleaned["data_status"].isin(["VALID", "UNUSUALLY_HIGH"])]
    series_map = {}
    for (cat, eq_id), sub in valid.groupby(["equipment_category", "equipment_id"]):
        series_map[(cat, eq_id)] = sub[["date", "fuel_liter"]].sort_values("date").reset_index(drop=True)
    return series_map


def _rolling_mean_shift_confirms(signal: np.ndarray, breakpoint_idx: int, window: int = 7) -> bool:
    """Cek sederhana: apakah rata-rata `window` titik sebelum vs sesudah
    breakpoint_idx berbeda cukup besar (>= 1 std pooled) -- dipakai sbg
    sinyal penguat independen dari PELT.
    """
    before = signal[max(0, breakpoint_idx - window):breakpoint_idx]
    after = signal[breakpoint_idx: breakpoint_idx + window]
    if len(before) < 2 or len(after) < 2:
        return False
    pooled_std = np.std(np.concatenate([before, after]))
    if pooled_std == 0:
        return False
    return abs(np.mean(after) - np.mean(before)) >= pooled_std


def _classify_change_direction(before_mean: float, after_mean: float,
                                before_std: float, after_std: float) -> str:
    """Tentukan jenis perubahan: level (naik/turun) atau sebaran (volatilitas)."""
    mean_diff_pct = abs(after_mean - before_mean) / before_mean * 100 if before_mean else np.inf
    std_diff_pct = abs(after_std - before_std) / before_std * 100 if before_std else np.inf

    if mean_diff_pct >= 15:
        return "INCREASE" if after_mean > before_mean else "DECREASE"
    if std_diff_pct >= 30:
        return "VOLATILITY_INCREASE" if after_std > before_std else "VOLATILITY_DECREASE"
    return "MINOR_SHIFT"


def _classify_confidence(mean_diff_pct: float, rolling_confirms: bool,
                          before_n: int, after_n: int) -> str:
    """Confidence kualitatif, mengombinasikan besaran perubahan, konfirmasi
    rolling-mean-shift, dan kecukupan jumlah observasi di kedua sisi.
    """
    enough_obs = before_n >= 5 and after_n >= 5
    if not enough_obs:
        return "LOW"
    if mean_diff_pct >= 40 and rolling_confirms:
        return "HIGH"
    if mean_diff_pct >= 15 or rolling_confirms:
        return "MEDIUM"
    return "LOW"


def detect_change_points_for_equipment(dates: pd.Series, values: np.ndarray,
                                        penalty: Optional[float] = None) -> list[dict]:
    """Jalankan PELT (model rbf) pada satu deret nilai equipment, kembalikan
    daftar change point mentah (posisi, before/after mean & std).
    """
    penalty = penalty or config.CHANGE_POINT_PENALTY
    signal = values.reshape(-1, 1).astype(float)

    try:
        algo = rpt.Pelt(model="rbf", min_size=5).fit(signal)
        breakpoints = algo.predict(pen=penalty)
    except Exception:
        return []  # sinyal terlalu pendek/konstan utk PELT -- tidak ada change point dilaporkan

    breakpoints = [b for b in breakpoints if b < len(values)]  # buang breakpoint akhir (len(values))
    results = []
    prev_edge = 0
    for bp in breakpoints:
        before_segment = values[prev_edge:bp]
        # segmen "sesudah" dibatasi sampai breakpoint berikutnya atau akhir data
        next_edge = breakpoints[breakpoints.index(bp) + 1] if breakpoints.index(bp) + 1 < len(breakpoints) else len(values)
        after_segment = values[bp:next_edge]
        if len(before_segment) < 2 or len(after_segment) < 2:
            prev_edge = bp
            continue

        before_mean, after_mean = float(np.mean(before_segment)), float(np.mean(after_segment))
        before_std, after_std = float(np.std(before_segment)), float(np.std(after_segment))
        rolling_confirms = _rolling_mean_shift_confirms(values, bp)
        mean_diff_pct = abs(after_mean - before_mean) / before_mean * 100 if before_mean else np.inf

        results.append({
            "change_index": bp,
            "change_date": dates.iloc[bp],
            "before_mean": round(before_mean, 1),
            "after_mean": round(after_mean, 1),
            "before_std": round(before_std, 1),
            "after_std": round(after_std, 1),
            "absolute_change": round(after_mean - before_mean, 1),
            "percentage_change": round(mean_diff_pct, 1) if np.isfinite(mean_diff_pct) else None,
            "change_direction": _classify_change_direction(before_mean, after_mean, before_std, after_std),
            "confidence": _classify_confidence(mean_diff_pct if np.isfinite(mean_diff_pct) else 0,
                                                rolling_confirms, len(before_segment), len(after_segment)),
            "n_obs_before": len(before_segment),
            "n_obs_after": len(after_segment),
        })
        prev_edge = bp
    return results


def detect_all_change_points(cleaned: pd.DataFrame) -> pd.DataFrame:
    """Entry point utama: jalankan change-point detection untuk semua
    equipment dengan observasi cukup, kembalikan tabel sesuai skema
    spesifikasi proyek.
    """
    series_map = _build_equipment_series(cleaned)
    rows = []
    skipped_insufficient = []

    for (cat, eq_id), sub in series_map.items():
        if len(sub) < config.CHANGE_POINT_MIN_OBSERVATIONS:
            skipped_insufficient.append((cat, eq_id, len(sub)))
            continue
        cps = detect_change_points_for_equipment(sub["date"], sub["fuel_liter"].values)
        for cp in cps:
            rows.append({
                "equipment_id": eq_id, "equipment_category": cat,
                "change_date": cp["change_date"], "before_mean": cp["before_mean"],
                "after_mean": cp["after_mean"], "absolute_change": cp["absolute_change"],
                "percentage_change": cp["percentage_change"],
                "change_direction": cp["change_direction"], "confidence": cp["confidence"],
                "review_status": "PENDING_REVIEW",
            })

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["equipment_category", "equipment_id", "change_date"]).reset_index(drop=True)

    n_skipped = len(skipped_insufficient)
    if n_skipped:
        warnings.warn(
            f"{n_skipped} equipment dilewati dari change-point detection karena observasi "
            f"< {config.CHANGE_POINT_MIN_OBSERVATIONS} (terlalu sedikit utk dipisahkan dari noise).",
            stacklevel=2,
        )
    return result


def summarize_change_points(change_points: pd.DataFrame) -> pd.DataFrame:
    """Ringkasan jumlah change point per kategori & arah perubahan."""
    if change_points.empty:
        return pd.DataFrame(columns=["equipment_category", "change_direction", "n_change_points"])
    return (change_points.groupby(["equipment_category", "change_direction"])
            .size().rename("n_change_points").reset_index()
            .sort_values(["equipment_category", "n_change_points"], ascending=[True, False]))


def save_outputs(change_points: pd.DataFrame, output_dir: Optional[Path] = None) -> None:
    output_dir = output_dir or config.PROCESSED_DATA_DIR
    change_points.to_csv(output_dir / "change_point_report.csv", index=False)
    summarize_change_points(change_points).to_csv(
        output_dir / "change_point_summary_by_category.csv", index=False)


if __name__ == "__main__":
    from src.data_cleaning import run_cleaning_pipeline

    result = run_cleaning_pipeline()
    change_points = detect_all_change_points(result.cleaned_fuel_data)
    save_outputs(change_points)

    print(f"Total change point terdeteksi: {len(change_points)}")
    print("\n=== Ringkasan per kategori & arah perubahan ===")
    print(summarize_change_points(change_points))

    print("\n=== Contoh change point confidence HIGH ===")
    high_conf = change_points[change_points["confidence"] == "HIGH"]
    print(high_conf.head(15))

    print(f"\nEquipment dengan >1 change point (indikasi pola berubah beberapa kali):")
    multi = change_points.groupby(["equipment_category", "equipment_id"]).size()
    print(multi[multi > 1].sort_values(ascending=False).head(10))

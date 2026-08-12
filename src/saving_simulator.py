"""
saving_simulator.py
====================
Simulator anggaran & target penghematan solar. Semua ANGKA DEFAULT di sini
adalah PARAMETER SIMULASI awal (lihat config.py) -- pengguna Streamlit
nantinya bisa mengubah semuanya lewat input di dashboard (Bagian H
spesifikasi). Modul ini TIDAK memutuskan target mana yang "benar"; ia hanya
menghitung konsekuensi numerik dari asumsi yang dimasukkan.

PENTING: L/TEU (liter per TEU) HANYA valid dihitung jika throughput TEU
AKTUAL tersedia. Tanpa itu, angka L/TEU di bawah adalah PROYEKSI berbasis
target/asumsi throughput, bukan realisasi -- selalu diberi peringatan.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config


@dataclass
class SavingSimulatorInputs:
    baseline_total_liter: float
    fuel_price_per_liter: float = config.DEFAULT_FUEL_PRICE_PER_LITER
    forecast_total_liter: Optional[float] = None       # default: sama dgn baseline jika tidak diisi
    saving_target_percentage: float = config.DEFAULT_SAVING_TARGET_PCT
    saving_target_liter: Optional[float] = None         # default: dihitung dari persentase jika tidak diisi
    target_throughput_teu: Optional[float] = config.DEFAULT_TARGET_THROUGHPUT_TEU
    current_liter_per_teu: float = config.DEFAULT_CURRENT_L_PER_TEU
    target_liter_per_teu: float = config.DEFAULT_TARGET_L_PER_TEU
    actual_teu: Optional[float] = None                  # throughput AKTUAL, jika ada -- lihat calculate_l_per_teu

    def __post_init__(self):
        if self.forecast_total_liter is None:
            self.forecast_total_liter = self.baseline_total_liter
        if self.saving_target_liter is None:
            self.saving_target_liter = self.baseline_total_liter * self.saving_target_percentage / 100


def calculate_l_per_teu(total_fuel_liter: float, total_teu: Optional[float] = None) -> dict:
    """Hitung efisiensi L/TEU dan proyeksi terhadap target -- TIDAK menghitung
    L/TEU aktual kecuali `total_teu` (throughput AKTUAL) diberikan.
    """
    max_liter_for_target = config.DEFAULT_TARGET_L_PER_TEU * config.DEFAULT_TARGET_THROUGHPUT_TEU
    result = {
        "baseline_l_per_teu": config.DEFAULT_CURRENT_L_PER_TEU,
        "target_l_per_teu": config.DEFAULT_TARGET_L_PER_TEU,
        "target_throughput_teu": config.DEFAULT_TARGET_THROUGHPUT_TEU,
        "max_liter_allowed_for_target": max_liter_for_target,
    }
    if total_teu is None or total_teu <= 0:
        result["actual_l_per_teu"] = None
        result["warning"] = ("Throughput TEU AKTUAL tidak tersedia -- L/TEU aktual TIDAK dihitung. "
                              "Angka L/TEU lain di modul ini adalah PROYEKSI, bukan realisasi.")
        return result

    actual_l_per_teu = total_fuel_liter / total_teu
    savings_needed_liter = max(0.0, total_fuel_liter - max_liter_for_target)
    result.update({
        "actual_l_per_teu": actual_l_per_teu,
        "savings_needed_liter": savings_needed_liter,
        "reduction_percentage_needed": (savings_needed_liter / total_fuel_liter * 100
                                         if total_fuel_liter else 0.0),
        "meets_target": actual_l_per_teu <= config.DEFAULT_TARGET_L_PER_TEU,
    })
    return result


def _build_scenario(name: str, reduction_pct: float, inputs: SavingSimulatorInputs) -> dict:
    baseline = inputs.baseline_total_liter
    projected_consumption = baseline * (1 - reduction_pct / 100)
    projected_cost = projected_consumption * inputs.fuel_price_per_liter
    saving_liter = baseline - projected_consumption
    saving_cost = saving_liter * inputs.fuel_price_per_liter
    gap_to_target = inputs.saving_target_liter - saving_liter

    projected_liter_per_teu = (
        projected_consumption / inputs.target_throughput_teu
        if inputs.target_throughput_teu else None
    )
    target_achievement_percentage = (
        saving_liter / inputs.saving_target_liter * 100 if inputs.saving_target_liter else None
    )

    return {
        "scenario": name,
        "reduction_percentage_applied": round(reduction_pct, 2),
        "projected_consumption": round(projected_consumption, 1),
        "projected_cost": round(projected_cost, 0),
        "saving_liter": round(saving_liter, 1),
        "saving_cost": round(saving_cost, 0),
        "gap_to_target_liter": round(gap_to_target, 1),
        "projected_liter_per_teu": round(projected_liter_per_teu, 4) if projected_liter_per_teu else None,
        "target_achievement_percentage": (round(target_achievement_percentage, 1)
                                           if target_achievement_percentage is not None else None),
    }


def run_saving_scenarios(inputs: SavingSimulatorInputs,
                          moderate_fraction_of_target: float = 0.5) -> pd.DataFrame:
    """Jalankan 3 skenario: Business as Usual, Moderate Saving, Target Saving.

    - BAU: tidak ada upaya penghematan (0% reduksi dari baseline).
    - Moderate Saving: mengejar SEBAGIAN target (`moderate_fraction_of_target`,
      default 50% dari target penghematan).
    - Target Saving: mengejar 100% target penghematan.
    """
    scenarios = [
        _build_scenario("Business as Usual", 0.0, inputs),
        _build_scenario("Moderate Saving", inputs.saving_target_percentage * moderate_fraction_of_target, inputs),
        _build_scenario("Target Saving", inputs.saving_target_percentage, inputs),
    ]
    df = pd.DataFrame(scenarios)

    # required_monthly/daily_reduction: konstan lintas skenario -- ini besaran yang
    # dibutuhkan supaya TARGET (bukan skenario tertentu) tercapai dalam waktu 1 tahun.
    df["required_monthly_reduction"] = round(inputs.saving_target_liter / 12, 1)
    df["required_daily_reduction"] = round(inputs.saving_target_liter / 365, 2)
    return df


def build_saving_report(inputs: SavingSimulatorInputs) -> dict:
    """Ringkasan lengkap: skenario + info L/TEU (dgn peringatan jika TEU aktual absen)."""
    scenarios_df = run_saving_scenarios(inputs)
    l_per_teu_info = calculate_l_per_teu(inputs.baseline_total_liter, inputs.actual_teu)
    return {
        "inputs": inputs.__dict__,
        "scenarios": scenarios_df,
        "l_per_teu_info": l_per_teu_info,
    }


def save_outputs(scenarios_df: pd.DataFrame, output_dir: Optional[Path] = None) -> None:
    output_dir = output_dir or config.PROCESSED_DATA_DIR
    scenarios_df.to_csv(output_dir / "saving_simulation_result.csv", index=False)


if __name__ == "__main__":
    from src.data_cleaning import run_cleaning_pipeline

    result = run_cleaning_pipeline()
    valid = result.cleaned_fuel_data[result.cleaned_fuel_data["data_status"] != "INVALID_DATE"]
    baseline_total = float(valid["fuel_liter"].sum())

    inputs = SavingSimulatorInputs(baseline_total_liter=baseline_total)
    report = build_saving_report(inputs)
    save_outputs(report["scenarios"])

    print(f"Baseline total 2025 (hasil hitung ulang): {baseline_total:,.0f} L")
    print(f"Target penghematan: {inputs.saving_target_liter:,.0f} L "
          f"({inputs.saving_target_percentage}%)")
    print(f"Harga solar/liter (default, PERLU DIVERIFIKASI): "
          f"Rp{inputs.fuel_price_per_liter:,.0f}")

    print("\n=== Skenario ===")
    print(report["scenarios"].to_string(index=False))

    print("\n=== Info L/TEU (tanpa throughput TEU aktual) ===")
    for k, v in report["l_per_teu_info"].items():
        print(f"  {k}: {v}")

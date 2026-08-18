from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analysis.accessibility import calculate_medical_score, calculate_living_score, calculate_transport_score
from src.analysis.infrastructure_risk import calculate_infrastructure_risk
from src.analysis.score import calculate_overall_score
from src.analysis.youth_analysis import calculate_youth_score

DATA_DIR = ROOT / "data" / "processed"


def build_region_latest() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / "region_panel.csv")

    df["transport_score"] = calculate_transport_score(df)
    df["medical_score"] = calculate_medical_score(df)
    df["living_score"] = calculate_living_score(df)
    df["infrastructure_risk"] = calculate_infrastructure_risk(df)
    df["infrastructure_stability"] = 100 - df["infrastructure_risk"]
    df["youth_score"] = calculate_youth_score(df)
    df["overall_score"] = calculate_overall_score(df)

    output_cols = [
        "region_code",
        "city",
        "district",
        "lat",
        "lon",
        "population",
        "youth_ratio",
        "elderly_ratio",
        "net_migration",
        "population_change",
        "business_count",
        "business_change",
        "hospital_count",
        "bus_stop_count",
        "vacant_house_ratio",
        "transport_score",
        "medical_score",
        "living_score",
        "youth_score",
        "infrastructure_stability",
        "overall_score",
    ]

    region_latest = df[output_cols].copy()
    region_latest = region_latest.round(2)
    return region_latest


def main() -> None:
    region_latest = build_region_latest()
    output_path = DATA_DIR / "region_latest.csv"
    region_latest.to_csv(output_path, index=False)
    print(f"Saved {len(region_latest)} rows to {output_path}")


if __name__ == "__main__":
    main()

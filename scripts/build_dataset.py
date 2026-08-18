from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def build_region_panel() -> pd.DataFrame:
    demo_path = DATA_DIR / "demo" / "demo_regions.csv"
    df = pd.read_csv(demo_path)
    df["year"] = 2024
    df["region_code"] = df["region_code"].astype(str)
    df["city"] = df["city"].astype(str)
    df["district"] = df["district"].astype(str)
    df["population"] = df["population"].astype(float)
    df["youth_population"] = df["youth_population"].astype(float)
    df["elderly_population"] = df["elderly_population"].astype(float)
    df["net_migration"] = df["net_migration"].astype(float)
    df["population_change"] = df["population_change"].astype(float)
    df["business_count"] = df["business_count"].astype(float)
    df["business_change"] = df["business_change"].astype(float)
    df["hospital_count"] = df["hospital_count"].astype(float)
    df["bus_stop_count"] = df["bus_stop_count"].astype(float)
    df["vacant_house_ratio"] = df["vacant_house_ratio"].astype(float)
    df["youth_ratio"] = (df["youth_population"] / df["population"]) * 100
    df["elderly_ratio"] = (df["elderly_population"] / df["population"]) * 100
    return df


def main() -> None:
    panel = build_region_panel()
    output_dir = DATA_DIR / "processed"
    output_dir.mkdir(parents=True, exist_ok=True)
    panel.to_csv(output_dir / "region_panel.csv", index=False)
    print(f"Saved {len(panel)} rows to {output_dir / 'region_panel.csv'}")


if __name__ == "__main__":
    main()

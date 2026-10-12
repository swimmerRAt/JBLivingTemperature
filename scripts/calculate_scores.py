from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dashboard_model import score_regions

DATA_DIR = ROOT / "data" / "processed"


def build_region_latest() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / "region_panel.csv")
    region_latest = score_regions(df, weights=None)
    region_latest = region_latest.round(2)
    return region_latest


def main() -> None:
    region_latest = build_region_latest()
    output_path = DATA_DIR / "region_latest.csv"
    region_latest.to_csv(output_path, index=False)
    print(f"Saved {len(region_latest)} rows to {output_path}")


if __name__ == "__main__":
    main()

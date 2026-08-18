from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


def load_demo_regions() -> pd.DataFrame:
    path = DATA_DIR / "demo" / "demo_regions.csv"
    return pd.read_csv(path)


def load_region_panel() -> pd.DataFrame:
    path = DATA_DIR / "processed" / "region_panel.csv"
    return pd.read_csv(path)


def load_region_latest() -> pd.DataFrame:
    path = DATA_DIR / "processed" / "region_latest.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist. Run scripts/build_dataset.py and scripts/calculate_scores.py first.")
    return pd.read_csv(path)

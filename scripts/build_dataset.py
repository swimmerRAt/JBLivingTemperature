"""Write the dashboard region panel and the town-year panel from the source database."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_pipeline import write_processed


def main() -> None:
    region, quality = write_processed(DATA_DIR)
    print(f"Saved {len(region)} {quality['unit']} rows to {DATA_DIR / 'processed' / 'region_panel.csv'}")
    print(f"Saved town-year panel to {DATA_DIR / 'processed' / 'town_year_panel.csv'}")
    for message in quality["warnings"].values():
        print(f"warning: {message}")


if __name__ == "__main__":
    main()

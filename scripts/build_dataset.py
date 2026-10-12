from __future__ import annotations

from pathlib import Path

import json
import sys

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_pipeline import build_region_panel


def main() -> None:
    panel, quality = build_region_panel(DATA_DIR)
    output_dir = DATA_DIR / "processed"
    output_dir.mkdir(parents=True, exist_ok=True)
    panel.to_csv(output_dir / "region_panel.csv", index=False)
    (output_dir / "data_quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2))
    print(f"Saved {len(panel)} rows to {output_dir / 'region_panel.csv'}")


if __name__ == "__main__":
    main()

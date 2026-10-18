"""Build expected-time 30-minute access counts from verified walk/bus network CSVs."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from src.accessibility import summarize_accessibility


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--network-dir', type=Path, default=ROOT / 'data/raw/accessibility')
    parser.add_argument('--wait-factor', type=float, default=0.5)
    args = parser.parse_args()
    frames = [pd.read_csv(args.network_dir / name, dtype={'region_code': str, 'node_id': str,
              'from_node': str, 'to_node': str, 'route_id': str, 'facility_id': str})
              for name in ('edges.csv', 'origins.csv', 'facilities.csv')]
    result = summarize_accessibility(*frames, wait_factor=args.wait_factor)
    target = ROOT / 'data/processed/accessibility_30m.csv'
    target.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(target, index=False)
    print(f'Saved {len(result)} region origins: {target}')


if __name__ == '__main__':
    main()

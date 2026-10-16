"""Fit youth-migration/infrastructure associations from a historical town panel."""
import argparse
import json
import sqlite3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from src.panel_analysis import fit_infrastructure_panel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel', type=Path, help="Optional prepared panel; otherwise build from the source DB")
    args = parser.parse_args()
    if args.panel:
        panel = pd.read_csv(args.panel, dtype={'region_code': str})
    else:
        from src.data_pipeline import database_path, load_units, build_town_year_panel, town_age_bands
        with sqlite3.connect(database_path(ROOT / 'data')) as connection:
            units, _ = load_units(connection)
            panel, _ = build_town_year_panel(connection, units)
            ages = town_age_bands(connection, units)
        bands = ages.pivot(index=['region_code', 'year'], columns='age_start', values='population')
        youth = bands[[20, 25, 30, 35]].sum(axis=1, min_count=4)
        expected = youth + (bands[15] - bands[35]) / 5
        previous = expected.rename('expected_youth').reset_index()
        previous['year'] += 1
        flow = youth.rename('current_youth').reset_index().merge(previous, on=['region_code', 'year'])
        flow['youth_net_migration_rate'] = (flow.current_youth / flow.expected_youth.where(flow.expected_youth > 0) - 1) * 1000
        panel = panel.merge(flow[['region_code', 'year', 'youth_net_migration_rate']], on=['region_code', 'year'])
        # Never combine the pre-2020 and post-2020 establishment survey frames.
        panel = panel[panel.year.ge(2020)].dropna(subset=['youth_net_migration_rate'])
    result = fit_infrastructure_panel(panel)
    if not args.panel:
        result["note"] += " 청년 순이동률은 5세 구간 내 균등 연령분포·사망 미반영의 1년 코호트 잔차 추정이며, 사업체는 2020년 이후 동일 조사기준만 사용합니다."
    target = ROOT / 'data/processed/infrastructure_analysis.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(f'Saved {result["observations"]} observations: {target}')


if __name__ == '__main__':
    main()

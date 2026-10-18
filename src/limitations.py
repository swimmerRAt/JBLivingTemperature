"""Observed medical changes and deterministic weight sensitivity, without forecasts."""
from pathlib import Path
import sqlite3

import pandas as pd
from src.data_pipeline import unit_name

from src.dashboard_model import DEFAULT_WEIGHTS, DRIVER_LABELS, _percentile, living_grade


def medical_history(data_dir: Path) -> pd.DataFrame:
    """Count mapped facilities; absent mapping for an entire period/kind stays unknown.

    Counts describe observations on a fixed boundary, not actual openings/closures.
    Unmapped facilities are reported alongside counts rather than assigned to towns.
    """
    database = (Path(data_dir) / 'processed/living_areas.sqlite').resolve()
    if not database.exists():
        return pd.DataFrame()
    with sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True) as connection:
        source = pd.read_sql_query('''
            SELECT snapshot_period, facility_kind, COUNT(*) AS source_count
            FROM medical_facility_snapshot GROUP BY 1,2
        ''', connection)
        mapped = pd.read_sql_query('''
            SELECT snapshot_period, facility_kind, admin_city_name AS city, town_name AS town, COUNT(*) AS count
            FROM medical_facility_admin WHERE town_code IS NOT NULL GROUP BY 1,2,3,4
        ''', connection)
        # Use the same set of analysis units as the current dashboard.
        regions = pd.read_sql_query('''
            SELECT DISTINCT town_code AS region_code, city_name AS city, town_name AS town FROM sgis_business_town
            WHERE industry_code = 'ALL' AND year = (SELECT MAX(year) FROM sgis_business_town)
        ''', connection)
    if source.empty or regions.empty:
        return pd.DataFrame()
    mapped['town'] = [unit_name(city, town) for city, town in zip(mapped.city, mapped.town)]
    mapped = mapped.merge(regions, on=['city', 'town'], how='inner')
    mapped = mapped.groupby(['snapshot_period', 'facility_kind', 'region_code'], as_index=False)['count'].sum()
    regions = regions[['region_code']]
    source['period'] = pd.to_datetime(source.snapshot_period, format='%Y.%m')
    totals = mapped.groupby(['snapshot_period', 'facility_kind'], as_index=False)['count'].sum()
    totals = totals.rename(columns={'count': 'mapped_count'})
    source = source.merge(totals, how='left', on=['snapshot_period', 'facility_kind'])
    source['mapped_count'] = source.mapped_count.fillna(0)
    source['mapping_coverage'] = source.mapped_count / source.source_count
    grid = regions.merge(source, how='cross').merge(mapped, how='left',
                                                    on=['region_code', 'snapshot_period', 'facility_kind'])
    # A town with no mapped facility has zero only when this period/kind was mapped.
    grid['count'] = grid['count'].fillna(0).where(grid.mapped_count.gt(0))
    return grid.sort_values(['region_code', 'facility_kind', 'period']).reset_index(drop=True)


def medical_changes(history: pd.DataFrame, region_code: str) -> pd.DataFrame:
    """Latest observation versus nearest observation at least 12 months earlier."""
    if history.empty:
        return pd.DataFrame()
    selected = history[history.region_code.astype(str).eq(str(region_code))]
    records = []
    for kind, group in selected.groupby('facility_kind'):
        group = group.sort_values('period')
        latest = group.iloc[-1]
        candidates = group[group.period.le(latest.period - pd.DateOffset(years=1)) & group['count'].notna()]
        baseline = candidates.iloc[-1] if not candidates.empty else None
        comparable = baseline is not None and pd.notna(latest['count'])
        delta = float(latest['count'] - baseline['count']) if comparable else None
        percent = delta / float(baseline['count']) * 100 if comparable and baseline['count'] > 0 else None
        records.append({'kind': kind, 'current_period': latest.snapshot_period, 'current_count': latest['count'],
                        'baseline_period': baseline.snapshot_period if baseline is not None else None,
                        'baseline_count': baseline['count'] if baseline is not None else None,
                        'change': delta, 'change_pct': percent,
                        'current_coverage': float(latest.mapping_coverage),
                        'baseline_coverage': float(baseline.mapping_coverage) if baseline is not None else None})
    return pd.DataFrame(records)


def weight_sensitivity(scored: pd.DataFrame, weights: dict | None = None, fraction: float = .2) -> pd.DataFrame:
    """Rank ranges from one-at-a-time +/-20% changes, not confidence intervals."""
    if not 0 < fraction < 1:
        raise ValueError('fraction must be between zero and one')
    if scored.empty:
        return pd.DataFrame()
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    weights = {key: max(float(weights[key]), 0) for key in DRIVER_LABELS}
    if sum(weights.values()) == 0:
        return pd.DataFrame()
    scenarios = [weights]
    for key in DRIVER_LABELS:
        if not weights[key]:
            continue
        for multiplier in (1-fraction, 1+fraction):
            scenarios.append({**weights, key: weights[key]*multiplier})
    ranks, grades = [], []
    for scenario in scenarios:
        total = sum(scenario.values())
        scores = sum(scored[f'{key}_component'] * value / total for key, value in scenario.items())
        ranks.append(scores.rank(ascending=False, method='min'))
        grades.append(_percentile(scores).map(living_grade))
    rank_frame = pd.concat(ranks, axis=1)
    grade_frame = pd.concat(grades, axis=1)
    result = scored[['region_code', 'region_name']].copy()
    result['current_rank'] = ranks[0].astype(int)
    result['best_rank'] = rank_frame.min(axis=1).astype(int)
    result['worst_rank'] = rank_frame.max(axis=1).astype(int)
    result['grade_variants'] = grade_frame.apply(lambda row: ' / '.join(sorted(set(row))), axis=1)
    result['top5_scenarios'] = rank_frame.le(5).sum(axis=1)
    result['scenario_count'] = len(scenarios)
    return result


def component_evidence(row: dict, forecast_connected: bool | None = None) -> pd.DataFrame:
    fields = {
        'transport': (['bus_stop_density', 'bus_route_count'], '읍면동 현재 스냅샷', '정류장·노선 수 대리지표. 실제 이동시간 아님'),
        'medical': (['hospital_count'], '읍면동 현재 스냅샷', '기관 수 대리지표. 진료과목·운영시간·거리 미반영'),
        'business': (['business_count'], '읍면동', '사업체 수. 실제 구인·고용 기회와 다름'),
        'vacancy': (['city_vacant_house_ratio'], '시군', '같은 시군의 읍면동에 동일 값 사용'),
        'youth': (['youth_ratio', 'youth_change_5y'], '읍면동', '인구 규모·행정경계 변경의 영향 확인 필요'),
        'stability': (['youth_forecast_change_3y', 'business_decline_prob_1y'], '읍면동 모델 예측', '예측 오차 존재. 의료·교통 감소 예측은 포함하지 않음'),
    }
    records = []
    for key, (columns, level, note) in fields.items():
        missing = [column for column in columns if pd.isna(row.get(column))]
        use_trend = forecast_connected is False or (forecast_connected is None and len(missing) == len(columns))
        if key == 'stability' and use_trend:
            note = '모델 미연결: 최근 인구 변화·고령 비율로 대체'
            columns = ['population_change_5y', 'elderly_ratio']
            missing = [column for column in columns if pd.isna(row.get(column))]
            level = '읍면동 과거 추세'
        status = '모두 결측 → 중립 50점' if len(missing) == len(columns) else '일부 지표 결측 → 있는 값만 반영' if missing else '연결됨'
        records.append({'평가 항목': DRIVER_LABELS[key], '자료 범위': level, '입력 상태': status, '해석': note})
    return pd.DataFrame(records)

"""Lagged infrastructure associations with youth migration, with two-way fixed effects.

Associations do not establish causal effects. Suggestions only cover statistically
supported components and never silently replace the dashboard's default weights.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

FEATURES = {'transport': 'transport_access', 'medical': 'medical_access',
            'business': 'establishments', 'vacancy': 'city_vacant_house_ratio'}


def fit_infrastructure_panel(panel: pd.DataFrame, min_regions: int = 10) -> dict:
    import statsmodels.api as sm
    required = {'region_code', 'year', 'youth_net_migration_rate'}
    if required - set(panel):
        raise ValueError(f'Missing panel columns: {sorted(required - set(panel))}')
    frame = panel.copy().sort_values(['region_code', 'year'])
    if frame[list(required)].isna().any(axis=None) or frame.duplicated(['region_code', 'year']).any():
        raise ValueError('Panel keys and outcome must be nonmissing; duplicate region-years are invalid')
    features = {k: v for k, v in FEATURES.items() if v in frame}
    if not features:
        raise ValueError('No infrastructure history available')
    consecutive = frame.groupby('region_code').year.diff().eq(1)
    terms = []
    for key, column in features.items():
        # All components oriented so a larger value means better infrastructure.
        values = pd.to_numeric(frame[column], errors='coerce') * (-1 if key == 'vacancy' else 1)
        frame[key] = values.groupby(frame.region_code).shift().where(consecutive)
        terms.append(key)
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=terms + ['youth_net_migration_rate'])
    if frame.region_code.nunique() < min_regions or frame.year.nunique() < 3:
        raise ValueError('Need at least 10 regions and 3 complete outcome years after lagging')
    # Full entity/year dummies work for both balanced and unbalanced panels.
    fixed = pd.get_dummies(frame[['region_code', 'year']].astype(str), drop_first=True, dtype=float)
    nuisance = sm.add_constant(fixed, has_constant='add').to_numpy(dtype=float)
    x = frame[terms].astype(float)
    scales = x.std(ddof=0)
    if (scales <= 0).any():
        raise ValueError('Time-invariant or constant infrastructure cannot identify a coefficient')
    x = (x - x.mean()) / scales
    residual = x.to_numpy() - nuisance @ np.linalg.lstsq(nuisance, x.to_numpy(), rcond=None)[0]
    if np.linalg.matrix_rank(residual, tol=1e-8) < len(terms):
        raise ValueError('Infrastructure is absorbed by fixed effects or collinear')
    design = pd.concat([x, fixed], axis=1)
    design = sm.add_constant(design, has_constant='add')
    if len(frame) <= design.shape[1] + 1:
        raise ValueError('Insufficient residual degrees of freedom')
    cluster = 'city' if 'city' in frame and frame.city.notna().all() else 'region_code'
    if frame[cluster].nunique() < 10:
        raise ValueError('Need at least 10 independent geographic clusters')
    fit = sm.OLS(frame.youth_net_migration_rate.astype(float), design).fit(
        cov_type='cluster', cov_kwds={'groups': frame[cluster]}, use_t=True)
    coefficients = []
    for term in terms:
        low, high = fit.conf_int().loc[term]
        coefficients.append({'component': term, 'coefficient': float(fit.params[term]),
                             'p_value': float(fit.pvalues[term]), 'ci_low': float(low), 'ci_high': float(high)})
    supported = {c['component']: c['coefficient'] for c in coefficients
                 if c['ci_low'] > 0 and c['p_value'] < 0.05}
    total = sum(supported.values())
    return {
            'observations': int(len(frame)), 'regions': int(frame.region_code.nunique()),
            'year_start': int(frame.year.min()), 'year_end': int(frame.year.max()),
            'cluster_level': cluster, 'clusters': int(frame[cluster].nunique()),
            'coefficients': coefficients,
            'suggested_weights': {key: value / total for key, value in supported.items()} if total else {},
            'note': f'읍면동·연도 고정효과, 전년도 인프라, {"시군" if cluster == "city" else "읍면동"} 군집 표준오차. 인과효과가 아닌 연관성입니다. '
                    '제안 가중치는 유의한 양의 계수끼리만 정규화하며 분석되지 않은 항목은 포함하지 않습니다.'}

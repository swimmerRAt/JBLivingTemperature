"""Youth (20-39) population forecasts for the 242 towns.

Step 1 estimates town net migration with the cohort residual method and builds
the Hamilton-Perry cohort-change-ratio projection as the demographic baseline.
Step 3 trains a LightGBM panel model on the same rows, compares both with
simple baselines in a rolling-origin backtest, and explains the final forecast
with SHAP. Mortality below age 40 is ignored, so estimated net migration is
slightly negative-biased (about 0.3% of the cohort over five years).
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.forecast.data import (
    ELDERLY_BANDS,
    ForecastInputs,
    at_year,
    log_ratio,
    same_business_frame,
    youth_total,
)

HORIZON = 3
FIRST_YEAR = 2008  # first year of resident-registration five-year bands
SECTOR_SHARES = {"C": "share_manufacturing", "G": "share_retail", "I": "share_food_lodging", "Q": "share_health_welfare"}
CATEGORICAL = ["city", "town_type"]
# Huber loss: small towns swing by tens of percent, and squared loss chasing them cost about
# 0.8%p of weighted backtest error. Pure L1 cut town error further but fits the median, which
# pulled the province total 2-6% low in recent origins; alpha 0.3 (about +/-35%) beat the
# squared-loss model on both town and province error.
LGBM_PARAMS = dict(objective="huber", alpha=0.3, n_estimators=200, learning_rate=0.03, num_leaves=15, min_child_samples=60,
                   subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=7, verbose=-1)
FEATURE_LABELS = {
    "log_youth": "청년 인구 규모", "log_population": "총인구 규모", "youth_ratio": "청년 비율", "elderly_ratio": "고령 비율",
    "child_ratio": "유소년 비율", "share_15_19": "곧 청년이 될 15~19세 비중", "share_35_39": "곧 청년을 벗어날 35~39세 비중",
    "youth_growth_1y": "최근 1년 청년 증감", "youth_growth_3y": "최근 3년 청년 증감", "youth_growth_5y": "최근 5년 청년 증감",
    "population_growth_3y": "최근 3년 인구 증감", "net_migration_rate_3y": "최근 3년 청년 순이동률",
    "net_migration_rate_5y": "최근 5년 청년 순이동률", "log_establishments": "사업체 규모", "establishments_per_1k": "인구 대비 사업체",
    "establishment_growth_3y": "최근 3년 사업체 증감", "share_manufacturing": "제조업 비중", "share_retail": "도소매업 비중",
    "share_food_lodging": "숙박·음식점 비중", "share_health_welfare": "보건·복지 비중",
    "city_vacant_house_ratio": "시군 빈집 비율", "city_youth_net_migration_rate": "시군 청년 순이동률",
    "city_youth_net_migration_rate_3y": "시군 청년 순이동률(3년 평균)", "city_medical_per_10k": "시군 인구 대비 의료기관",
    "log_density": "인구 밀도", "new_business_frame": "사업체 조사기준(2020~)", "hp_growth": "코호트 추계(해밀턴-페리)",
    "aging_only_growth": "연령 구조 효과(15~19세 유입·35~39세 이탈)", "city": "시군 고유 효과", "town_type": "읍·면·동 구분",
}


def no_migration_youth(bands: pd.DataFrame, horizon: int) -> pd.Series:
    """Youth after `horizon` years if nobody moved, assuming ages are uniform within bands."""
    return youth_total(bands) + horizon / 5 * (bands[15] - bands[35])


def cohort_net_migration(inputs: ForecastInputs, start: int, end: int) -> pd.Series:
    """Net migration of the youth cohort between two years (people), by town."""
    return youth_total(at_year(inputs.bands, end)) - no_migration_youth(at_year(inputs.bands, start), end - start)


def hamilton_perry(inputs: ForecastInputs, origin: int, horizon: int = HORIZON) -> pd.Series:
    """Youth projected `horizon` years ahead from the latest five-year cohort change ratios."""
    before, now = at_year(inputs.bands, origin - 5), at_year(inputs.bands, origin)
    projected = 0
    for age in (20, 25, 30, 35):
        ratio = (now[age] / before[age - 5]).where(before[age - 5] >= 5).clip(0.2, 5).fillna(1.0)
        projected = projected + ratio * now[age - 5]
    youth = youth_total(now)
    return youth * (projected / youth) ** (horizon / 5)


def hamilton_perry_elderly(inputs: ForecastInputs, origin: int, horizon: int = HORIZON) -> pd.Series:
    """Population aged 65+ projected `horizon` years ahead with five-year cohort change ratios."""
    before, now = at_year(inputs.bands, origin - 5), at_year(inputs.bands, origin)
    projected = 0
    for age in range(65, 100, 5):
        ratio = (now[age] / before[age - 5]).where(before[age - 5] >= 5).clip(0, 5).fillna(1.0)
        projected = projected + ratio * now[age - 5]
    # 100+ survives from 95+ five years earlier.
    oldest = (now[100] / (before[95] + before[100])).where(before[95] + before[100] >= 5).clip(0, 5).fillna(1.0)
    projected = projected + oldest * (now[95] + now[100])
    elderly = now[list(ELDERLY_BANDS)].sum(axis=1)
    return elderly * (projected / elderly) ** (horizon / 5)


def town_features(inputs: ForecastInputs, origin: int, horizon: int = HORIZON) -> pd.DataFrame:
    """Features for every town using data up to `origin` only."""
    bands = at_year(inputs.bands, origin)
    total = bands.sum(axis=1, min_count=1)
    youth = youth_total(bands)
    features = pd.DataFrame(index=inputs.units["region_code"])
    features["log_youth"] = np.log(youth)
    features["log_population"] = np.log(total)
    features["youth_ratio"] = youth / total
    features["elderly_ratio"] = bands[list(ELDERLY_BANDS)].sum(axis=1) / total
    features["child_ratio"] = bands[[0, 5, 10]].sum(axis=1) / total
    features["share_15_19"] = bands[15] / youth
    features["share_35_39"] = bands[35] / youth
    for years in (1, 3, 5):
        if origin - years >= FIRST_YEAR:
            features[f"youth_growth_{years}y"] = log_ratio(youth, youth_total(at_year(inputs.bands, origin - years)))
    if origin - 3 >= FIRST_YEAR:
        earlier = at_year(inputs.bands, origin - 3)
        features["population_growth_3y"] = log_ratio(total, earlier.sum(axis=1, min_count=1))
        features["net_migration_rate_3y"] = cohort_net_migration(inputs, origin - 3, origin) / youth_total(earlier)
    if origin - 5 >= FIRST_YEAR:
        features["net_migration_rate_5y"] = (cohort_net_migration(inputs, origin - 5, origin)
                                             / no_migration_youth(at_year(inputs.bands, origin - 5), 5))
        features["hp_growth"] = log_ratio(hamilton_perry(inputs, origin, horizon), youth)
    features["aging_only_growth"] = log_ratio(no_migration_youth(bands, horizon), youth)

    panel = inputs.panel.set_index(["region_code", "year"])
    now = panel.xs(origin, level="year")
    features["log_establishments"] = np.log(now["establishments"])
    features["establishments_per_1k"] = now["establishments"] / total * 1000
    if same_business_frame(origin - 3, origin) and origin - 3 in panel.index.get_level_values("year"):
        features["establishment_growth_3y"] = log_ratio(now["establishments"], panel.xs(origin - 3, level="year")["establishments"])
    sectors = inputs.sectors[inputs.sectors["year"].eq(origin)].pivot_table(
        index="region_code", columns="industry_code", values="establishments", aggfunc="sum")
    for code, name in SECTOR_SHARES.items():
        features[name] = (sectors[code] if code in sectors else np.nan) / now["establishments"]
    features["city_vacant_house_ratio"] = now["city_vacant_house_ratio"]
    features["city_youth_net_migration_rate"] = now["city_youth_net_migration_rate"]
    recent = panel[panel.index.get_level_values("year").isin(range(origin - 2, origin + 1))]
    features["city_youth_net_migration_rate_3y"] = recent.groupby(level="region_code")["city_youth_net_migration_rate"].mean()

    units = inputs.units.set_index("region_code")
    city_population = total.groupby(units["city"]).sum()
    medical = inputs.city_medical[inputs.city_medical["year"].eq(origin)].set_index("city")["facility_count"]
    features["city_medical_per_10k"] = units["city"].map(medical / city_population * 10000)
    features["log_density"] = np.log(total / units["area_km2"])
    features["new_business_frame"] = int(origin >= 2020)
    features["city"] = pd.Categorical(units["city"], categories=sorted(units["city"].unique()))
    features["town_type"] = pd.Categorical(units["town_type"], categories=["동", "읍", "면"])
    features.insert(0, "origin", origin)
    return features


def build_dataset(inputs: ForecastInputs, horizon: int = HORIZON) -> pd.DataFrame:
    """One row per town and origin year with a known youth outcome `horizon` years later."""
    years = sorted(inputs.bands.index.get_level_values("year").unique())
    rows = []
    for origin in range(FIRST_YEAR + 5, years[-1] - horizon + 1):
        features = town_features(inputs, origin, horizon)
        youth_now = youth_total(at_year(inputs.bands, origin))
        youth_later = youth_total(at_year(inputs.bands, origin + horizon))
        features["youth"] = youth_now
        features["youth_future"] = youth_later
        features["target"] = log_ratio(youth_later, youth_now)
        rows.append(features)
    data = pd.concat(rows).reset_index()
    return data.dropna(subset=["target"]).reset_index(drop=True)


def feature_columns(data: pd.DataFrame) -> list[str]:
    # new_business_frame is a business-survey flag; for youth it would only be an "after 2020"
    # dummy that is constant in every backtest training set and so never validated.
    excluded = {"region_code", "origin", "youth", "youth_future", "target", "new_business_frame"}
    return [column for column in data.columns if column not in excluded]


def baseline_growth(data: pd.DataFrame) -> pd.Series:
    """Hamilton-Perry where five years of history exist, otherwise ageing only."""
    return data["hp_growth"].fillna(data["aging_only_growth"])


def fit_model(train: pd.DataFrame, columns: list[str], residual: bool) -> lgb.LGBMRegressor:
    target = train["target"] - baseline_growth(train) if residual else train["target"]
    model = lgb.LGBMRegressor(**LGBM_PARAMS)
    model.fit(train[columns], target, categorical_feature=CATEGORICAL)
    return model


def predict(model: lgb.LGBMRegressor, rows: pd.DataFrame, columns: list[str], residual: bool) -> np.ndarray:
    prediction = model.predict(rows[columns])
    return prediction + baseline_growth(rows).to_numpy() if residual else prediction


def backtest(data: pd.DataFrame, horizon: int = HORIZON) -> pd.DataFrame:
    """Rolling origin: each test origin is predicted by models trained on outcomes known by then."""
    columns = feature_columns(data)
    origins = sorted(data["origin"].unique())
    results = []
    for test_origin in origins:
        train = data[data["origin"] <= test_origin - horizon]
        if train["origin"].nunique() < 1:
            continue
        test = data[data["origin"].eq(test_origin)].copy()
        test["no_change"] = 0.0
        test["trend_3y"] = test["youth_growth_3y"].fillna(0.0)
        test["aging_only"] = test["aging_only_growth"]
        test["hamilton_perry"] = baseline_growth(test)
        for name, residual in (("lgbm_direct", False), ("lgbm_residual", True)):
            test[name] = predict(fit_model(train, columns, residual), test, columns, residual)
        results.append(test)
    return pd.concat(results, ignore_index=True)


METHODS = ("no_change", "trend_3y", "aging_only", "hamilton_perry", "lgbm_direct", "lgbm_residual")


def score(results: pd.DataFrame) -> pd.DataFrame:
    """Errors in percentage points of the h-year youth change, plain and weighted by youth population."""
    actual = np.expm1(results["target"]) * 100
    weights = results["youth"]
    rows = []
    by_origin = results.groupby("origin")
    for method in METHODS:
        error = (np.expm1(results[method]) * 100 - actual).abs()
        youth_error = (results["youth"] * np.exp(results[method]) - results["youth_future"]).abs()
        rows.append({
            "method": method,
            "mae_pp": error.mean(),
            "median_ae_pp": error.median(),
            "weighted_mae_pp": np.average(error, weights=weights),
            "youth_mae_people": youth_error.mean(),
            "province_error_pct": (results["youth"] * np.exp(results[method])).sum() / results["youth_future"].sum() * 100 - 100,
            # Pooled over origins, over- and under-predicting years cancel; this one does not.
            "province_origin_abs_error_pct": by_origin.apply(
                lambda part, method=method: abs((part["youth"] * np.exp(part[method])).sum() / part["youth_future"].sum() * 100 - 100)).mean(),
        })
    return pd.DataFrame(rows).set_index("method")


def size_group(log_youth: pd.Series, edges: list[float]) -> pd.Series:
    return pd.Series(np.digitize(log_youth, edges), index=log_youth.index)


def conformal_widths(results: pd.DataFrame, method: str, coverage: float = 0.8) -> tuple[list[float], dict[int, float], float]:
    """Interval half-widths (log scale) by town-size group, and their leave-one-origin-out coverage."""
    residual = (results["target"] - results[method]).abs()
    edges = list(np.quantile(results["log_youth"], [1 / 3, 2 / 3]))
    groups = size_group(results["log_youth"], edges)
    widths = {group: float(residual[groups.eq(group)].quantile(coverage)) for group in sorted(groups.unique())}
    hits = []
    for origin in results["origin"].unique():
        held = results["origin"].eq(origin)
        for group in widths:
            mask = groups.eq(group)
            width = residual[mask & ~held].quantile(coverage)
            hits.append((residual[mask & held] <= width).to_numpy())
    return edges, widths, float(np.concatenate(hits).mean())


def migration_check(inputs: ForecastInputs) -> pd.DataFrame:
    """Five-year town estimates summed by city against KOSIS city net migration of ages 20-39."""
    units = inputs.units.set_index("region_code")
    city_flow = inputs.panel.drop_duplicates(["city", "year"]).set_index(["city", "year"])["city_youth_net_migration"]
    years = sorted(inputs.bands.index.get_level_values("year").unique())
    rows = []
    for end in range(FIRST_YEAR + 5, years[-1] + 1):
        estimate = cohort_net_migration(inputs, end - 5, end)
        for city, values in estimate.groupby(units["city"]):
            if values.isna().any():
                continue  # a boundary reset leaves part of the city without a comparable cohort
            observed = sum(city_flow.get((city, year), np.nan) for year in range(end - 4, end + 1))
            rows.append({"city": city, "end_year": end, "estimated": values.sum(), "observed": observed})
    return pd.DataFrame(rows)


def shap_drivers(model: lgb.LGBMRegressor, rows: pd.DataFrame, columns: list[str], residual: bool,
                 top: int = 3) -> tuple[pd.DataFrame, pd.Series]:
    """Per-town top contributions (log points) and mean |SHAP| by feature."""
    import shap

    values = pd.DataFrame(shap.TreeExplainer(model).shap_values(rows[columns]), index=rows.index, columns=columns)
    if residual:
        values.insert(0, "hp_growth_base", baseline_growth(rows))
    importance = values.abs().mean().sort_values(ascending=False)
    labels = {**FEATURE_LABELS, "hp_growth_base": "코호트 추계 기준선"}
    text = []
    for _, row in values.iterrows():
        down = row[row < 0].sort_values()[: top - 1]
        up = row[row > 0].sort_values(ascending=False)[:1]
        parts = []
        if not down.empty:
            parts.append("감소 요인: " + ", ".join(f"{labels.get(name, name)} {value * 100:+.1f}%p" for name, value in down.items()))
        if not up.empty:
            parts.append("완화 요인: " + ", ".join(f"{labels.get(name, name)} {value * 100:+.1f}%p" for name, value in up.items()))
        text.append(" / ".join(parts))
    return pd.DataFrame({"drivers": text}, index=rows.index), importance


def forecast(inputs: ForecastInputs, data: pd.DataFrame, method: str, widths: dict[int, float],
             edges: list[float], horizon: int = HORIZON) -> tuple[pd.DataFrame, pd.Series]:
    """Train on every known outcome and forecast from the latest year."""
    columns = feature_columns(data)
    residual = method == "lgbm_residual"
    origin = int(inputs.bands.index.get_level_values("year").max())
    rows = town_features(inputs, origin, horizon).reset_index()
    youth = youth_total(at_year(inputs.bands, origin)).reindex(rows["region_code"]).to_numpy()
    if method.startswith("lgbm"):
        model = fit_model(data, columns, residual)
        growth = predict(model, rows, columns, residual)
        drivers, importance = shap_drivers(model, rows, columns, residual)
    else:
        growth = baseline_growth(rows).to_numpy()
        drivers, importance = pd.DataFrame({"drivers": ""}, index=rows.index), pd.Series(dtype=float)
    half = size_group(rows["log_youth"], edges).map(widths).to_numpy()
    no_migration = no_migration_youth(at_year(inputs.bands, origin), horizon).reindex(rows["region_code"]).to_numpy()
    predicted = youth * np.exp(growth)
    female_youth = youth_total(at_year(inputs.female_bands, origin))
    elderly_now = at_year(inputs.bands, origin)[list(ELDERLY_BANDS)].sum(axis=1)
    result = pd.DataFrame({
        "region_code": rows["region_code"],
        "origin_year": origin,
        "target_year": origin + horizon,
        "youth": youth,
        "youth_forecast": predicted,
        "youth_forecast_low": youth * np.exp(growth - half),
        "youth_forecast_high": youth * np.exp(growth + half),
        "youth_change_pct": np.expm1(growth) * 100,
        "youth_change_low_pct": np.expm1(growth - half) * 100,
        "youth_change_high_pct": np.expm1(growth + half) * 100,
        "hamilton_perry_change_pct": np.expm1(baseline_growth(rows).to_numpy()) * 100,
        "net_migration_forecast": predicted - no_migration,
        "net_migration_last_5y": cohort_net_migration(inputs, origin - 5, origin).reindex(rows["region_code"]).to_numpy(),
        "drivers": drivers["drivers"].to_numpy(),
        # 지방소멸위험지수 in the target year: women 20-39 are assumed to change at the forecast youth
        # rate, and the 65+ population follows the Hamilton-Perry cohort projection.
        "extinction_risk_index": (female_youth / elderly_now).reindex(rows["region_code"]).to_numpy(),
        "extinction_risk_index_forecast": (female_youth.reindex(rows["region_code"]).to_numpy() * np.exp(growth)
                                           / hamilton_perry_elderly(inputs, origin, horizon).reindex(rows["region_code"]).to_numpy()),
        # Towns reset by a boundary change have no past trend, so the model sees mostly averages.
        "forecast_note": np.where(rows["youth_growth_3y"].isna(), "경계 조정으로 과거 추세가 없어 신뢰도 낮음", ""),
    })
    return result, importance

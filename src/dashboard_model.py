"""Score towns and assemble the dashboard snapshot.

Components are percentile ranks (0-100) among the towns, so a score says how a
town compares with the rest of Jeonbuk, not an absolute level. When the model
outputs of scripts/train_models.py are present, the future-stability component
and the 3-year risk stages come from the youth forecast and the living-service
business decline risk; otherwise they fall back to recent population trends and
the dashboard says that no forecast is connected.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.recommendation.explanation import generate_reason

DEFAULT_WEIGHTS = {
    "transport": 3,
    "medical": 3,
    "business": 2,
    "vacancy": 1,
    "youth": 1,
    "stability": 1,
}

DRIVER_LABELS = {
    "transport": "대중교통 접근성",
    "medical": "의료 접근성",
    "business": "생활 인프라(사업체)",
    "vacancy": "주거 안정성(시군)",
    "youth": "청년 기반",
    "stability": "미래 안정성(3년 예측)",
}

GRADES = ("A", "B", "C", "D", "E")
STAGES = ("안정", "주의", "위험")
# 지방소멸위험지수 tiers of 한국고용정보원 (이상호): women 20-39 / everyone 65+.
# Below 0.5 is a 소멸위험지역, which the dashboard shows as 위험; 0.5-1.0 is 주의.
EXTINCTION_TIERS = ((0.2, "소멸고위험"), (0.5, "소멸위험진입"), (1.0, "소멸주의"), (1.5, "보통"))
EXTINCTION_TOP_TIER = "소멸저위험"
EXTINCTION_STAGE = {"소멸고위험": "위험", "소멸위험진입": "위험", "소멸주의": "주의", "보통": "안정", "소멸저위험": "안정"}
# Business stage: 위험 when the town's establishments already meet the 도시재생법
# 시행령 제17조 industrial-decline criterion, or when the town has fewer than
# SMALL_TOWN_ESTABLISHMENTS establishments: there a handful of closures crosses the
# 5% line, such towns meet the criterion about twice as often (1 year: 30% vs 17%),
# and the model barely beats persistence on them, so the town is treated as fragile. 주의 when the 1-year model gives at least BUSINESS_WATCH_PROBABILITY.
BUSINESS_WATCH_PROBABILITY = 0.5
SMALL_TOWN_ESTABLISHMENTS = 200
PREDICTION_NOTE = "예측 결과가 연결되지 않았습니다. scripts/train_models.py를 실행하면 3년 예측이 반영됩니다."
FORECAST_NOTE = ("위험 단계 기준: 청년은 한국고용정보원 지방소멸위험지수(20~39세 여성 ÷ 65세 이상, 0.5 미만 위험·"
                 "0.5~1.0 주의), 사업체는 도시재생법 시행령 제17조 산업 쇠퇴 기준(최근 최고치 대비 5% 이상 감소 또는 "
                 "3년 이상 연속 감소; 이미 해당하거나 사업체가 200개 미만이면 위험, 1년 안에 해당할 확률 50% 이상이면 주의)입니다. "
                 "근거와 성능은 docs/model_report.md, 서비스 설명은 docs/서비스_명세.md에 있습니다.")


def _numeric(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[name], errors="coerce")


def _percentile(values: pd.Series, higher_is_better: bool = True) -> pd.Series:
    """Rank on 0-100 (lowest 0, highest 100); missing values stay missing."""
    values = values if higher_is_better else -values
    count = values.notna().sum()
    if count <= 1:
        return pd.Series(50.0, index=values.index).where(values.notna())
    return (values.rank(method="average") - 1) / (count - 1) * 100


def _combine(parts: list[tuple[pd.Series, float]]) -> pd.Series:
    """Weighted mean that skips missing parts; neutral 50 when every part is missing."""
    frame = pd.concat([series * weight for series, weight in parts], axis=1)
    weights = pd.concat([series.notna() * weight for series, weight in parts], axis=1)
    return (frame.sum(axis=1, min_count=1) / weights.sum(axis=1)).fillna(50.0).clip(0, 100)


def living_grade(percentile: float) -> str:
    """A for the top fifth of towns down to E for the bottom fifth."""
    index = min(int((100 - percentile) // 20), len(GRADES) - 1)
    return GRADES[index]


def extinction_tier(index: float) -> str | None:
    if pd.isna(index):
        return None
    return next((label for cut, label in EXTINCTION_TIERS if index < cut), EXTINCTION_TOP_TIER)


def youth_stage(index: float) -> str | None:
    """Dashboard stage of a 지방소멸위험지수 value."""
    tier = extinction_tier(index)
    return EXTINCTION_STAGE[tier] if tier else None


def business_stage(meets: object, probability_1y: float, establishments: float = float("nan")) -> str | None:
    if not pd.isna(establishments) and establishments < SMALL_TOWN_ESTABLISHMENTS:
        return "위험"
    if meets is None or pd.isna(meets):
        return None
    if bool(meets):
        return "위험"
    return "주의" if not pd.isna(probability_1y) and probability_1y >= BUSINESS_WATCH_PROBABILITY else "안정"


def worst_stage(*stages: str | None) -> str | None:
    known = [stage for stage in stages if isinstance(stage, str) and stage in STAGES]
    return max(known, key=STAGES.index) if known else None


def has_forecast(regions: pd.DataFrame) -> bool:
    return _numeric(regions, "youth_forecast_change_3y").notna().any()


def score_regions(regions: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.DataFrame:
    scored = regions.copy()
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    total_weight = sum(max(float(value), 0.0) for value in weights.values()) or 1.0
    weights = {key: max(float(value), 0.0) / total_weight for key, value in weights.items()}

    scored["transport_component"] = _combine([
        (_percentile(_numeric(scored, "bus_stop_density")), 0.5),
        (_percentile(_numeric(scored, "bus_route_count")), 0.5),
    ])
    scored["medical_component"] = _combine([(_percentile(_numeric(scored, "hospital_count")), 1.0)])
    scored["business_component"] = _combine([(_percentile(_numeric(scored, "business_count")), 1.0)])
    scored["vacancy_component"] = _combine([
        (_percentile(_numeric(scored, "city_vacant_house_ratio"), higher_is_better=False), 1.0),
    ])
    scored["youth_component"] = _combine([
        (_percentile(_numeric(scored, "youth_ratio")), 0.5),
        (_percentile(_numeric(scored, "youth_change_5y")), 0.5),
    ])
    if has_forecast(scored):
        scored["stability_component"] = _combine([
            (_percentile(_numeric(scored, "youth_forecast_change_3y")), 0.5),
            (_percentile(_numeric(scored, "business_decline_prob_1y"), higher_is_better=False), 0.5),
        ])
    else:
        scored["stability_component"] = _combine([
            (_percentile(_numeric(scored, "population_change_5y")), 0.6),
            (_percentile(_numeric(scored, "elderly_ratio"), higher_is_better=False), 0.4),
        ])

    for key in DRIVER_LABELS:
        scored[f"contrib_{key}"] = scored[f"{key}_component"] * weights[key]
    scored["living_score"] = sum(scored[f"contrib_{key}"] for key in DRIVER_LABELS).clip(0, 100)
    scored["living_percentile"] = _percentile(scored["living_score"])
    scored["living_grade"] = scored["living_percentile"].map(living_grade)
    scored["extinction_risk_tier"] = _numeric(scored, "extinction_risk_index").map(extinction_tier)
    scored["extinction_risk_tier_forecast"] = _numeric(scored, "extinction_risk_index_forecast").map(extinction_tier)
    scored["youth_risk_stage"] = _numeric(scored, "extinction_risk_index_forecast").map(youth_stage)
    meets = scored["business_statute_meets"] if "business_statute_meets" in scored else pd.Series(None, index=scored.index)
    scored["business_risk_stage"] = [business_stage(flag, probability, count) for flag, probability, count
                                     in zip(meets, _numeric(scored, "business_decline_prob_1y"),
                                            _numeric(scored, "business_count"))]
    scored["forecast_stage"] = [worst_stage(youth, business) for youth, business
                                in zip(scored["youth_risk_stage"], scored["business_risk_stage"])]
    return scored.sort_values("living_score", ascending=False).reset_index(drop=True)


def _record(row: pd.Series) -> dict[str, Any]:
    result = row.to_dict()
    for key, value in result.items():
        if not isinstance(value, (list, dict)) and pd.isna(value):
            result[key] = None
    return result


def build_dashboard_snapshot(
    regions: pd.DataFrame,
    quality: dict[str, object],
    selected_region: str | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, object]:
    scored = score_regions(regions, weights)
    notes = [FORECAST_NOTE if has_forecast(scored) else PREDICTION_NOTE]
    if scored.empty:
        return {"regions": scored, "selected_region": {}, "selected_drivers": [],
                "recommendations": [], "quality": quality, "notes": notes}

    selected = scored[scored["region_code"].eq(selected_region)].head(1) if selected_region else scored.head(1)
    if selected.empty:
        selected = scored.head(1)
    selected_row = selected.iloc[0]
    drivers = [
        {
            "key": key,
            "label": label,
            "score": round(float(selected_row[f"{key}_component"]), 1),
            "contribution": round(float(selected_row[f"contrib_{key}"]), 1),
        }
        for key, label in DRIVER_LABELS.items()
    ]
    recommendations = scored.head(5).copy()
    recommendations["reason"] = recommendations.apply(generate_reason, axis=1)
    return {
        "regions": scored,
        "selected_region": _record(selected_row),
        "selected_drivers": drivers,
        "recommendations": [_record(row) for _, row in recommendations.iterrows()],
        "quality": quality,
        "notes": notes,
    }

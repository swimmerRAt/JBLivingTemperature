from __future__ import annotations

from typing import Any

import pandas as pd

from src.recommendation.explanation import generate_reason

DEFAULT_WEIGHTS = {
    "transport": 0.30,
    "medical": 0.25,
    "vacancy": 0.20,
    "youth": 0.15,
    "stability": 0.10,
}

DRIVER_LABELS = {
    "transport": "대중교통 접근성",
    "medical": "의료 접근성",
    "vacancy": "주거 안정성",
    "youth": "청년 기반",
    "stability": "인구 안정성",
}


def _numeric(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[name], errors="coerce")


def _normalize(values: pd.Series, higher_is_better: bool = True, default: float = 50.0) -> pd.Series:
    values = pd.to_numeric(values, errors="coerce")
    valid = values.dropna()
    if valid.empty:
        return pd.Series(default, index=values.index, dtype="float64")
    minimum, maximum = valid.min(), valid.max()
    if minimum == maximum:
        return pd.Series(default, index=values.index, dtype="float64")
    normalized = (values - minimum) / (maximum - minimum) * 100
    if not higher_is_better:
        normalized = 100 - normalized
    return normalized.fillna(default).clip(0, 100)


def risk_grade(living_score: float) -> str:
    if living_score >= 70:
        return "안정"
    if living_score >= 50:
        return "관찰"
    return "위험"


def score_regions(regions: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.DataFrame:
    scored = regions.copy()
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    total_weight = sum(max(float(value), 0.0) for value in weights.values()) or 1.0
    weights = {key: max(float(value), 0.0) / total_weight for key, value in weights.items()}

    hospital_rate = _numeric(scored, "hospital_count") / _numeric(scored, "population") * 100000
    transport = _numeric(scored, "transport_score")
    if transport.dropna().empty:
        transport_component = pd.Series(50.0, index=scored.index)
    else:
        transport_component = transport.fillna(transport.median()).clip(0, 100)

    scored["transport_component"] = transport_component
    scored["medical_component"] = _normalize(hospital_rate)
    scored["vacancy_component"] = _normalize(_numeric(scored, "vacant_house_ratio"), higher_is_better=False)
    scored["youth_component"] = _normalize(_numeric(scored, "youth_ratio"))
    population_change = _normalize(_numeric(scored, "population_change"))
    elderly_component = _normalize(_numeric(scored, "elderly_ratio"), higher_is_better=False)
    scored["stability_component"] = (population_change * 0.6 + elderly_component * 0.4).clip(0, 100)

    component_map = {
        "transport": "transport_component",
        "medical": "medical_component",
        "vacancy": "vacancy_component",
        "youth": "youth_component",
        "stability": "stability_component",
    }
    for key, column in component_map.items():
        scored[f"contrib_{key}"] = scored[column] * weights[key]
    scored["living_score"] = sum(scored[f"contrib_{key}"] for key in component_map).clip(0, 100)
    scored["risk_score"] = (100 - scored["living_score"]).clip(0, 100)
    scored["risk_grade"] = scored["living_score"].map(risk_grade)
    scored["overall_score"] = scored["living_score"]
    return scored.sort_values("living_score", ascending=False).reset_index(drop=True)


def _record(row: pd.Series) -> dict[str, Any]:
    result = row.to_dict()
    for key, value in result.items():
        if pd.isna(value):
            result[key] = None
    return result


def build_dashboard_snapshot(
    regions: pd.DataFrame,
    quality: dict[str, object],
    selected_region: str | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, object]:
    scored = score_regions(regions, weights)
    if scored.empty:
        return {
            "regions": scored,
            "selected_region": {},
            "selected_drivers": [],
            "recommendations": [],
            "trend": [],
            "quality": quality,
        }

    selected = scored[scored["city"].eq(selected_region)].head(1) if selected_region else scored.head(1)
    if selected.empty:
        selected = scored.head(1)
    selected_row = selected.iloc[0]
    driver_keys = list(DRIVER_LABELS)
    drivers = [
        {
            "key": key,
            "label": DRIVER_LABELS[key],
            "score": round(float(selected_row[f"{key}_component"]), 1),
            "contribution": round(float(selected_row[f"contrib_{key}"]), 1),
        }
        for key in driver_keys
    ]

    recommendations = scored.head(5).copy()
    recommendations["reason"] = recommendations.apply(generate_reason, axis=1)
    trend = [
        {"label": "인구 증감률", "value": selected_row.get("population_change")},
        {"label": "청년 비율", "value": selected_row.get("youth_ratio")},
        {"label": "빈집 비율", "value": selected_row.get("vacant_house_ratio")},
    ]
    return {
        "regions": scored,
        "selected_region": _record(selected_row),
        "selected_drivers": drivers,
        "recommendations": [_record(row) for _, row in recommendations.iterrows()],
        "trend": trend,
        "quality": quality,
    }

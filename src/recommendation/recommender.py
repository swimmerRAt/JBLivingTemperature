from __future__ import annotations

import pandas as pd


def recommend_regions(
    df: pd.DataFrame,
    transport_weight: float = 1.0,
    medical_weight: float = 1.0,
    living_weight: float = 1.0,
    stability_weight: float = 1.0,
    exclude_cities: list[str] | None = None,
) -> pd.DataFrame:
    excluded = set(exclude_cities or [])
    filtered = df[~df["city"].isin(excluded)].copy()

    effective_transport = filtered["transport_score"].copy()

    total_weight = transport_weight + medical_weight + living_weight + stability_weight
    if total_weight <= 0:
        total_weight = 1.0

    filtered["contrib_transport"] = effective_transport * transport_weight
    filtered["contrib_medical"] = filtered["medical_score"] * medical_weight
    filtered["contrib_living"] = filtered["living_score"] * living_weight
    filtered["contrib_stability"] = filtered["infrastructure_stability"] * stability_weight

    filtered["user_score"] = (
        filtered["contrib_transport"]
        + filtered["contrib_medical"]
        + filtered["contrib_living"]
        + filtered["contrib_stability"]
    )

    filtered["user_score_100"] = (filtered["user_score"] / total_weight).clip(0, 100)
    filtered["transport_score_adjusted"] = effective_transport

    return filtered.sort_values("user_score", ascending=False).reset_index(drop=True)

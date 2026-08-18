from __future__ import annotations

import pandas as pd


def recommend_regions(
    df: pd.DataFrame,
    transport_weight: float = 1.0,
    medical_weight: float = 1.0,
    living_weight: float = 1.0,
    stability_weight: float = 1.0,
    exclude_cities: list[str] | None = None,
    no_car: bool = False,
) -> pd.DataFrame:
    excluded = set(exclude_cities or [])
    filtered = df[~df["city"].isin(excluded)].copy()

    if no_car:
        filtered = filtered.copy()
        filtered["transport_score"] = filtered["transport_score"] * 1.10

    filtered["user_score"] = (
        filtered["transport_score"] * transport_weight
        + filtered["medical_score"] * medical_weight
        + filtered["living_score"] * living_weight
        + filtered["infrastructure_stability"] * stability_weight
    )

    return filtered.sort_values("user_score", ascending=False).reset_index(drop=True)

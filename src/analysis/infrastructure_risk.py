from __future__ import annotations

import pandas as pd


def calculate_infrastructure_risk(df: pd.DataFrame) -> pd.Series:
    """Baseline heuristic risk score. This can later be replaced by model-based prediction."""
    pop_decline = (df["population_change"].clip(lower=-100, upper=0).abs() / 10.0)
    youth_outflow = (df["net_migration"].clip(lower=-1000, upper=0).abs() / 20.0)
    business_decline = (df["business_change"].clip(lower=-100, upper=0).abs() / 10.0)
    elderly_ratio = df["elderly_ratio"] / 2.0
    vacancy_ratio = df["vacant_house_ratio"]

    risk = (
        pop_decline * 0.25
        + youth_outflow * 0.20
        + business_decline * 0.20
        + elderly_ratio * 0.15
        + vacancy_ratio * 0.20
    )
    return risk.clip(0, 100)

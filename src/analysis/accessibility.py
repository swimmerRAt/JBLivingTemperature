from __future__ import annotations

import pandas as pd


def normalize_to_0_100(series: pd.Series, lower: float | None = None, upper: float | None = None) -> pd.Series:
    if lower is None:
        lower = series.min()
    if upper is None:
        upper = series.max()
    if upper == lower:
        return pd.Series(50.0, index=series.index)
    return ((series - lower) / (upper - lower)) * 100


def calculate_transport_score(df: pd.DataFrame) -> pd.Series:
    bus_score = normalize_to_0_100(df["bus_stop_count"])
    density_score = normalize_to_0_100(df["population"])
    living_access = normalize_to_0_100(df["business_count"])
    score = (bus_score * 0.60) + (density_score * 0.20) + (living_access * 0.20)
    return score.clip(0, 100)


def calculate_medical_score(df: pd.DataFrame) -> pd.Series:
    medical_score = normalize_to_0_100(df["hospital_count"])
    pop_score = normalize_to_0_100(df["population"])
    score = (medical_score * 0.70) + (pop_score * 0.30)
    return score.clip(0, 100)


def calculate_living_score(df: pd.DataFrame) -> pd.Series:
    business_score = normalize_to_0_100(df["business_count"])
    migration_score = normalize_to_0_100(df["net_migration"].abs())
    vacancy_penalty = normalize_to_0_100(df["vacant_house_ratio"].max() - df["vacant_house_ratio"])
    score = (business_score * 0.50) + (migration_score * 0.20) + (vacancy_penalty * 0.30)
    return score.clip(0, 100)

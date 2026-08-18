from __future__ import annotations

import pandas as pd


def calculate_overall_score(df: pd.DataFrame) -> pd.Series:
    score = (
        df["transport_score"] * 0.30
        + df["medical_score"] * 0.20
        + df["living_score"] * 0.20
        + df["youth_score"] * 0.10
        + df["infrastructure_stability"] * 0.20
    )
    return score.clip(0, 100)

from __future__ import annotations

import pandas as pd


def calculate_youth_score(df: pd.DataFrame) -> pd.Series:
    youth_ratio = df["youth_ratio"]
    migration_score = (df["net_migration"] - df["net_migration"].min()) / (df["net_migration"].max() - df["net_migration"].min() + 1e-9)
    score = (youth_ratio / youth_ratio.max()) * 40 + (migration_score * 60)
    return score.clip(0, 100)

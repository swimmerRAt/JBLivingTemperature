from __future__ import annotations

import plotly.express as px
import pandas as pd


def create_corr_chart(df: pd.DataFrame):
    corr = df[["youth_ratio", "transport_score", "medical_score", "living_score", "infrastructure_stability"]].corr().round(2)
    corr = corr.reset_index().melt(id_vars="index", var_name="feature", value_name="value")
    return px.bar(
        corr,
        x="feature",
        y="value",
        color="value",
        facet_col="index",
        title="청년 유출 관련 지표 상관관계",
        range_color=[-1, 1],
    )


def create_score_ranking_chart(df: pd.DataFrame):
    ranking = df[["city", "district", "overall_score", "transport_score", "medical_score", "living_score", "infrastructure_stability"]].copy()
    ranking["label"] = ranking["city"] + " " + ranking["district"]
    ranking = ranking.sort_values("overall_score", ascending=False)
    return px.bar(
        ranking,
        x="label",
        y="overall_score",
        title="전북 생활권 점수 순위",
        color="overall_score",
        color_continuous_scale="Viridis",
    )

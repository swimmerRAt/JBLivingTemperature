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
    score_col = "no_car_temperature" if "no_car_temperature" in df.columns else "overall_score"
    ranking[score_col] = df[score_col]
    ranking = ranking.sort_values(score_col, ascending=False)
    return px.bar(
        ranking,
        x="label",
        y=score_col,
        title="무차량 생활 온도 순위" if score_col == "no_car_temperature" else "전북 생활권 점수 순위",
        color=score_col,
        color_continuous_scale="Viridis",
    )

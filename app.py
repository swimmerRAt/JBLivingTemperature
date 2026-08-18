from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.recommendation.explanation import generate_reason
from src.recommendation.recommender import recommend_regions
from src.visualization.charts import create_corr_chart, create_score_ranking_chart
from src.visualization.map import create_folium_map

st.set_page_config(page_title="전북 생활권 에이전트", layout="wide")
st.title("전북 생활권 에이전트 MVP")

DATA_PATH = Path(__file__).resolve().parent / "data" / "processed" / "region_latest.csv"

if not DATA_PATH.exists():
    st.warning("데이터가 아직 생성되지 않았습니다. 먼저 `python scripts/build_dataset.py`와 `python scripts/calculate_scores.py`를 실행하세요.")
    st.stop()

regions = pd.read_csv(DATA_PATH)

with st.sidebar:
    st.header("조건 설정")
    car_ownership = st.selectbox("자동차 보유", ["없음", "있음"], index=0)
    age_group = st.selectbox("연령", ["20대", "30대", "40대", "50대 이상"], index=0)
    excluded_cities = st.multiselect("제외할 지역", sorted(regions["city"].unique()), default=["전주시"])
    transport_weight = st.slider("대중교통 중요도", 1, 5, 5)
    medical_weight = st.slider("의료 중요도", 1, 5, 4)
    living_weight = st.slider("생활 인프라 중요도", 1, 5, 4)
    stability_weight = st.slider("미래 안정성 중요도", 1, 5, 3)

    st.caption("MVP 단계에서는 실제 이동시간 대신 정류장 밀도와 생활 편의 시설 접근성을 근사하여 점수를 산출합니다.")

    run_recommendation = st.button("추천 받기", use_container_width=True)


if run_recommendation:
    recommendation = recommend_regions(
        regions,
        transport_weight=transport_weight,
        medical_weight=medical_weight,
        living_weight=living_weight,
        stability_weight=stability_weight,
        exclude_cities=excluded_cities,
        no_car=(car_ownership == "없음"),
    )

    st.subheader("추천 결과")
    for idx, row in recommendation.head(3).iterrows():
        rank = idx + 1
        reason = generate_reason(row)
        st.markdown(f"### {rank}위 {row['city']} {row['district']} {row['overall_score']:.1f}점")
        st.write(reason)
        st.write(f"- 대중교통: {row['transport_score']:.1f}점")
        st.write(f"- 의료 접근성: {row['medical_score']:.1f}점")
        st.write(f"- 생활 인프라: {row['living_score']:.1f}점")
        st.write(f"- 미래 안정성: {row['infrastructure_stability']:.1f}점")
        st.write(f"- 청년 거주 적합도: {row['youth_score']:.1f}점")
        st.markdown("---")


raw_map = create_folium_map(regions)
map_html = raw_map._repr_html_()
st.components.v1.html(map_html, height=650)

col1, col2, col3 = st.columns(3)
with col1:
    st.subheader("지역별 생활권 점수")
    st.dataframe(regions[["city", "district", "overall_score", "transport_score", "medical_score", "infrastructure_stability"]].sort_values("overall_score", ascending=False).reset_index(drop=True), use_container_width=True)
with col2:
    st.subheader("인프라 위험순위")
    risk_df = regions[["city", "district", "infrastructure_stability"]].sort_values("infrastructure_stability", ascending=True).reset_index(drop=True)
    st.dataframe(risk_df, use_container_width=True)
with col3:
    st.subheader("청년 유출 영향요인")
    corr = regions[["youth_score", "transport_score", "medical_score", "living_score", "infrastructure_stability"]].corr().round(2)
    st.dataframe(corr, use_container_width=True)

st.subheader("청년 유출 상관분석")
chart_obj = create_corr_chart(regions)
st.plotly_chart(chart_obj, use_container_width=True)

st.subheader("생활권 점수 순위")
ranking_chart = create_score_ranking_chart(regions)
st.plotly_chart(ranking_chart, use_container_width=True)

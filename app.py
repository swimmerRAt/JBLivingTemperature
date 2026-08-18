from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from src.recommendation.explanation import generate_reason
from src.recommendation.recommender import recommend_regions
from src.visualization.charts import create_corr_chart, create_score_ranking_chart
from src.visualization.map import create_folium_map

st.set_page_config(page_title="전북 생활권 에이전트", layout="wide")
st.title("전북 생활권 에이전트 MVP")

DATA_PATH = Path(__file__).resolve().parent / "data" / "processed" / "region_latest.csv"

if not DATA_PATH.exists():
    st.warning("데이터가 아직 생성되지 않았습니다. 먼저 python scripts/build_dataset.py와 python scripts/calculate_scores.py를 실행하세요.")
    st.stop()

regions = pd.read_csv(DATA_PATH)


def classify_risk_grade(stability: float) -> str:
    if stability >= 70:
        return "안정"
    if stability >= 40:
        return "주의"
    return "위험"


regions["no_car_temperature"] = (
    regions["transport_score"] * 0.50
    + regions["medical_score"] * 0.20
    + regions["living_score"] * 0.20
    + regions["infrastructure_stability"] * 0.10
).clip(0, 100)
regions["risk_grade"] = regions["infrastructure_stability"].apply(classify_risk_grade)

with st.sidebar:
    st.header("무차량 청년 추천 조건")
    excluded_cities = st.multiselect("제외할 지역", sorted(regions["city"].unique()), default=["전주시"])
    allowed_risk_grades = st.multiselect("허용 위험도 등급", ["안정", "주의", "위험"], default=["안정", "주의"])

    with st.expander("대중교통 조건", expanded=True):
        transport_weight = st.slider("대중교통 중요도", 1, 10, 6)
        min_transport_score = st.slider("최소 교통 점수", 0, 100, 55)
        min_bus_stop_count = st.slider("최소 버스정류장 수", 0, int(regions["bus_stop_count"].max()), 30)

    with st.expander("의료 조건", expanded=True):
        medical_weight = st.slider("의료 중요도", 1, 10, 5)
        min_medical_score = st.slider("최소 의료 점수", 0, 100, 45)
        min_hospital_count = st.slider("최소 의료기관 수", 0, int(regions["hospital_count"].max()), 10)

    with st.expander("생활 인프라 조건", expanded=True):
        living_weight = st.slider("생활 인프라 중요도", 1, 10, 5)
        min_living_score = st.slider("최소 생활 인프라 점수", 0, 100, 45)
        max_vacant_house_ratio = st.slider("최대 빈집 비율", 0.0, 30.0, 22.0, 0.1)

    with st.expander("미래 안정성 조건", expanded=True):
        stability_weight = st.slider("미래 안정성 중요도", 1, 10, 4)
        min_stability_score = st.slider("최소 미래 안정성 점수", 0, 100, 50)
        min_population_change = st.slider("최소 인구 증감률(%)", -10.0, 5.0, -3.0, 0.1)

    st.caption("핵심 지표는 무차량 생활 온도입니다. 교통/의료/생활/미래 안정성을 세분화해 필터링 후 추천합니다.")
    run_recommendation = st.button("추천 받기", use_container_width=True)

st.markdown(
    """
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
      <span style="background:#fde0dd;color:#a50f15;padding:6px 10px;border-radius:999px;font-size:13px;">핵심 지표: 무차량 생활 온도</span>
      <span style="background:#e5f5e0;color:#1b5e20;padding:6px 10px;border-radius:999px;font-size:13px;">안정: 인프라 안정성 70 이상</span>
      <span style="background:#fff7bc;color:#8d6e00;padding:6px 10px;border-radius:999px;font-size:13px;">주의: 인프라 안정성 40~69</span>
      <span style="background:#f2f0f7;color:#4a1486;padding:6px 10px;border-radius:999px;font-size:13px;">위험: 인프라 안정성 40 미만</span>
    </div>
    """,
    unsafe_allow_html=True,
)

map_tab, analysis_tab, rec_tab, youth_tab = st.tabs(["생활권 지도", "지역 분석", "거주 추천", "청년 유출 분석"])

with map_tab:
    st.subheader("무차량 생활 온도 지도")
    map_df = regions.sort_values("no_car_temperature", ascending=False).copy()
    raw_map = create_folium_map(map_df)
    map_html = raw_map._repr_html_()
    st.components.v1.html(map_html, height=650)

    st.dataframe(
        map_df[["city", "district", "no_car_temperature", "overall_score", "transport_score", "medical_score", "risk_grade"]]
        .rename(
            columns={
                "city": "시군",
                "district": "읍면동",
                "no_car_temperature": "무차량 생활 온도",
                "overall_score": "종합 점수",
                "transport_score": "교통",
                "medical_score": "의료",
                "risk_grade": "위험도 등급",
            }
        )
        .round(2),
        use_container_width=True,
    )

with analysis_tab:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("생활권 점수 순위")
        ranking_chart = create_score_ranking_chart(regions)
        st.plotly_chart(ranking_chart, use_container_width=True)
    with c2:
        st.subheader("인프라 위험 등급 현황")
        risk_summary = (
            regions.groupby("risk_grade", as_index=False)
            .agg(region_count=("region_code", "count"), avg_stability=("infrastructure_stability", "mean"))
            .sort_values("avg_stability", ascending=False)
        )
        st.dataframe(
            risk_summary.rename(
                columns={
                    "risk_grade": "등급",
                    "region_count": "지역 수",
                    "avg_stability": "평균 안정성",
                }
            ).round(2),
            use_container_width=True,
        )

with rec_tab:
    st.subheader("조건 기반 추천")
    if run_recommendation:
        candidates = regions.copy()
        candidates = candidates[candidates["risk_grade"].isin(allowed_risk_grades)]
        candidates = candidates[~candidates["city"].isin(excluded_cities)]
        candidates = candidates[candidates["transport_score"] >= min_transport_score]
        candidates = candidates[candidates["bus_stop_count"] >= min_bus_stop_count]
        candidates = candidates[candidates["medical_score"] >= min_medical_score]
        candidates = candidates[candidates["hospital_count"] >= min_hospital_count]
        candidates = candidates[candidates["living_score"] >= min_living_score]
        candidates = candidates[candidates["vacant_house_ratio"] <= max_vacant_house_ratio]
        candidates = candidates[candidates["infrastructure_stability"] >= min_stability_score]
        candidates = candidates[candidates["population_change"] >= min_population_change]

        st.caption(f"조건 통과 지역: {len(candidates)}개")

        if candidates.empty:
            st.warning("현재 조건을 만족하는 지역이 없습니다. 최소 점수/정량 조건을 완화해 주세요.")
            recommendation = pd.DataFrame()
        else:
            recommendation = recommend_regions(
                candidates,
                transport_weight=transport_weight,
                medical_weight=medical_weight,
                living_weight=living_weight,
                stability_weight=stability_weight,
                exclude_cities=None,
            )

        if recommendation.empty:
            st.info("필터를 완화한 뒤 다시 추천을 실행해 주세요.")
        else:
            top_row = recommendation.iloc[0]
            st.markdown(
                f"### 1위 {top_row['city']} {top_row['district']} | 무차량 생활 온도 {top_row['user_score_100']:.1f}"
            )

            for rank_num, (_, row) in enumerate(recommendation.head(3).iterrows(), start=1):
                reason = generate_reason(row)
                st.markdown(
                    f"#### {rank_num}위 {row['city']} {row['district']} | 추천 점수 {row['user_score_100']:.1f} | 위험도 {row['risk_grade']}"
                )
                st.write(reason)
                st.write(f"- 대중교통(조정): {row['transport_score_adjusted']:.1f}")
                st.write(f"- 의료 접근성: {row['medical_score']:.1f}")
                st.write(f"- 생활 인프라: {row['living_score']:.1f}")
                st.write(f"- 미래 안정성: {row['infrastructure_stability']:.1f}")

            contrib_top3 = recommendation.head(3).copy()
            contrib_top3["label"] = contrib_top3["city"] + " " + contrib_top3["district"]
            contrib_long = contrib_top3[["label", "contrib_transport", "contrib_medical", "contrib_living", "contrib_stability"]].melt(
                id_vars="label",
                var_name="factor",
                value_name="contribution",
            )
            factor_names = {
                "contrib_transport": "교통 기여도",
                "contrib_medical": "의료 기여도",
                "contrib_living": "생활 인프라 기여도",
                "contrib_stability": "미래 안정성 기여도",
            }
            contrib_long["factor"] = contrib_long["factor"].map(factor_names)

            contrib_fig = px.bar(
                contrib_long,
                x="label",
                y="contribution",
                color="factor",
                barmode="stack",
                title="추천 점수 기여도 분해 (Top 3)",
            )
            st.plotly_chart(contrib_fig, use_container_width=True)
    else:
        st.info("사이드바에서 조건을 설정하고 추천 받기를 눌러주세요.")

with youth_tab:
    st.subheader("청년 유출 상관분석")
    corr_chart = create_corr_chart(regions)
    st.plotly_chart(corr_chart, use_container_width=True)

    st.subheader("무차량 생활 온도 vs 청년 지표")
    scatter = px.scatter(
        regions,
        x="no_car_temperature",
        y="youth_score",
        color="risk_grade",
        hover_data=["city", "district"],
        title="무차량 생활 온도와 청년 거주 적합도",
    )
    st.plotly_chart(scatter, use_container_width=True)

st.caption(f"데이터 샘플: {len(regions)}개 지역 | 타겟: 무차량 청년 | Baseline 데모 데이터")

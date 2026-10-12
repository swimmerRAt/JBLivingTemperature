from __future__ import annotations

from numbers import Real
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from src.visualization.map import create_folium_map


def format_metric(value: Any, suffix: str = "", digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "데이터 없음"
    if isinstance(value, Real):
        return f"{value:,.{digits}f}{suffix}"
    return f"{value}{suffix}"


def build_status_cards(snapshot: dict[str, object]) -> list[dict[str, str]]:
    row = snapshot.get("selected_region", {})
    return [
        {"label": "생활 온도", "value": format_metric(row.get("living_score")), "detail": "100점 기준"},
        {"label": "위험 등급", "value": str(row.get("risk_grade", "데이터 없음")), "detail": "종합 신호"},
        {"label": "인구", "value": format_metric(row.get("population"), digits=0), "detail": "최근 추계"},
        {"label": "인구 증감", "value": format_metric(row.get("population_change"), suffix="%", digits=1), "detail": "전년 대비"},
    ]


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
          --ink: #0d1b2a;
          --muted: #64748b;
          --line: #dbe5ef;
          --surface: #ffffff;
          --canvas: #f4f7fb;
          --blue: #2f65d9;
          --blue-soft: #e9f0ff;
          --amber: #efb34d;
          --green: #2d9d78;
          --red: #d96161;
        }
        .stApp { background: var(--canvas); color: var(--ink); }
        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stSidebar"] { background: #11253a; }
        [data-testid="stSidebar"] * { color: #e8f0f8; }
        [data-testid="stSidebar"] label { color: #adc0d4 !important; }
        .block-container { max-width: 1440px; padding-top: 2rem; padding-bottom: 3rem; }
        .dashboard-kicker { color: var(--blue); font-size: .78rem; font-weight: 700; letter-spacing: .05em; margin-bottom: .4rem; }
        .dashboard-title { color: var(--ink); font-size: 2.2rem; line-height: 1.08; font-weight: 750; margin: 0; }
        .dashboard-subtitle { color: var(--muted); margin: .55rem 0 0; font-size: .98rem; }
        .section-title { color: var(--ink); font-size: 1.12rem; font-weight: 700; margin: .4rem 0 .8rem; }
        .status-card { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 1rem 1.05rem; min-height: 112px; }
        .status-label { color: var(--muted); font-size: .82rem; }
        .status-value { color: var(--ink); font-size: 1.65rem; font-weight: 750; margin-top: .35rem; }
        .status-detail { color: #8b9bad; font-size: .75rem; margin-top: .25rem; }
        .panel { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; padding: 1.1rem 1.2rem; height: 100%; }
        .score-ring { background: var(--blue-soft); border: 1px solid #cfddff; border-radius: 50%; width: 148px; height: 148px; display: grid; place-items: center; margin: .5rem auto 1.1rem; }
        .score-number { color: var(--blue); font-size: 2.5rem; font-weight: 800; line-height: 1; }
        .score-caption { color: var(--muted); font-size: .78rem; text-align: center; margin-top: .25rem; }
        .recommendation { border-top: 1px solid var(--line); padding: .8rem 0; }
        .recommendation:first-child { border-top: 0; padding-top: .1rem; }
        .rec-rank { color: var(--blue); font-size: .82rem; font-weight: 750; }
        .rec-name { color: var(--ink); font-weight: 700; font-size: 1rem; }
        .rec-score { color: var(--blue); font-weight: 750; }
        .source-note { color: var(--muted); font-size: .78rem; line-height: 1.5; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(snapshot: dict[str, object]) -> None:
    city = snapshot.get("selected_region", {}).get("city", "전북 전체")
    st.markdown('<div class="dashboard-kicker">CIVIC TECH / JEONBUK</div>', unsafe_allow_html=True)
    st.markdown(f'<h1 class="dashboard-title">{city} 생활권 인사이트</h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="dashboard-subtitle">생활 인프라와 인구 흐름을 하나의 온도로 읽습니다.</p>',
        unsafe_allow_html=True,
    )


def render_sidebar(regions: pd.DataFrame) -> dict[str, object]:
    with st.sidebar:
        st.markdown("## 전북 생활권")
        st.caption("지역을 고르면 아래 분석 카드가 함께 바뀝니다.")
        cities = sorted(regions["city"].dropna().astype(str).unique().tolist())
        selected = st.selectbox("분석 지역", cities, index=0 if cities else None)
        st.markdown("---")
        st.markdown("### 추천 가중치")
        st.caption("내가 중요하게 보는 생활 조건을 조정하세요.")
        weights = {
            "transport": st.slider("대중교통", 0, 10, 3),
            "medical": st.slider("의료 접근성", 0, 10, 3),
            "vacancy": st.slider("주거 안정성", 0, 10, 2),
            "youth": st.slider("청년 기반", 0, 10, 1),
            "stability": st.slider("인구 안정성", 0, 10, 1),
        }
        st.markdown("---")
        st.caption("점수는 연결된 원천자료를 0–100으로 정규화한 설명 가능한 기준입니다.")
    return {"selected_region": selected, "weights": weights}


def render_status_cards(snapshot: dict[str, object]) -> None:
    cards = build_status_cards(snapshot)
    columns = st.columns(len(cards))
    for column, card in zip(columns, cards):
        with column:
            st.markdown(
                f'<div class="status-card"><div class="status-label">{card["label"]}</div>'
                f'<div class="status-value">{card["value"]}</div>'
                f'<div class="status-detail">{card["detail"]}</div></div>',
                unsafe_allow_html=True,
            )


def _render_driver_chart(snapshot: dict[str, object]) -> None:
    drivers = pd.DataFrame(snapshot.get("selected_drivers", []))
    if drivers.empty:
        st.info("선택 지역의 세부 점수를 계산할 데이터가 없습니다.")
        return
    figure = px.bar(
        drivers.sort_values("score"),
        x="score",
        y="label",
        orientation="h",
        range_x=[0, 100],
        color="score",
        color_continuous_scale=["#dce8ff", "#2f65d9"],
    )
    figure.update_layout(
        height=270,
        margin=dict(l=0, r=0, t=8, b=0),
        showlegend=False,
        coloraxis_showscale=False,
        xaxis_title=None,
        yaxis_title=None,
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})


def render_dashboard(snapshot: dict[str, object]) -> None:
    render_header(snapshot)
    st.write("")
    render_status_cards(snapshot)
    st.write("")

    left, right = st.columns([1.65, 1])
    with left:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">전북 생활권 위험 지도</div>', unsafe_allow_html=True)
        regions = snapshot.get("regions", pd.DataFrame())
        if regions.empty or regions[["lat", "lon"]].isna().all(axis=None):
            st.info("지도 좌표가 연결된 지역이 없습니다.")
        else:
            map_html = create_folium_map(regions)._repr_html_()
            st.components.v1.html(map_html, height=540)
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        row = snapshot.get("selected_region", {})
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">선택 지역의 생활 온도</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="score-ring"><div class="score-number">{format_metric(row.get("living_score"))}</div></div>'
            f'<div class="score-caption">위험 등급 · {row.get("risk_grade", "데이터 없음")} / 높을수록 안정적</div>',
            unsafe_allow_html=True,
        )
        _render_driver_chart(snapshot)
        st.markdown("</div>", unsafe_allow_html=True)

    st.write("")
    left, right = st.columns([1, 1])
    with left:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">추천 지역</div>', unsafe_allow_html=True)
        for rank, item in enumerate(snapshot.get("recommendations", []), start=1):
            st.markdown(
                f'<div class="recommendation"><div class="rec-rank">0{rank} · {item.get("risk_grade", "관찰")}</div>'
                f'<div class="rec-name">{item.get("city", "지역")} <span class="rec-score">{format_metric(item.get("living_score"))}</span></div>'
                f'<div class="source-note">{item.get("reason", "근거 설명이 없습니다.")}</div></div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">핵심 지표</div>', unsafe_allow_html=True)
        row = snapshot.get("selected_region", {})
        indicators = pd.DataFrame(
            [
                ["청년 인구 비율", format_metric(row.get("youth_ratio"), suffix="%")],
                ["고령 인구 비율", format_metric(row.get("elderly_ratio"), suffix="%")],
                ["빈집 비율", format_metric(row.get("vacant_house_ratio"), suffix="%")],
                ["의료기관 수", format_metric(row.get("hospital_count"), digits=0)],
            ],
            columns=["지표", "값"],
        )
        st.dataframe(indicators, hide_index=True, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    quality = snapshot.get("quality", {})
    warnings = quality.get("warnings", {})
    st.write("")
    if warnings:
        st.warning(" · ".join(warnings.values()))
    st.markdown(
        f'<div class="source-note">연결된 원천 {quality.get("source_count", 0)}개 · '
        f'분석 지역 {quality.get("region_count", len(snapshot.get("regions", [])))}개 · '
        "지표는 최신 연결 자료 기준</div>",
        unsafe_allow_html=True,
    )

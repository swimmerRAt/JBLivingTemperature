from __future__ import annotations

from numbers import Real
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from src.dashboard_model import DEFAULT_WEIGHTS, DRIVER_LABELS
from src.visualization.map import create_folium_map


def format_metric(value: Any, suffix: str = "", digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "데이터 없음"
    if isinstance(value, Real):
        return f"{value:,.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _percent(value: Any) -> str:
    return "데이터 없음" if value is None or pd.isna(value) else f"{float(value):.0%}"


def build_status_cards(snapshot: dict[str, object]) -> list[dict[str, str]]:
    row = snapshot.get("selected_region", {})
    year = row.get("youth_forecast_year")
    youth_detail = (f"{int(year)}년 예측 · 80% 구간 {format_metric(row.get('youth_forecast_change_low'), '%')}"
                    f" ~ {format_metric(row.get('youth_forecast_change_high'), '%')}" if year else "예측 미연결")
    if row.get("youth_forecast_note"):
        youth_detail += " · " + row["youth_forecast_note"]
    stage_detail = (f"지방소멸위험지수 {format_metric(row.get('extinction_risk_index_forecast'), digits=2)}"
                    f"({row.get('extinction_risk_tier_forecast') or '-'}) · 사업체 {row.get('business_risk_stage') or '-'}"
                    if row.get("forecast_stage") else "예측 미연결")
    return [
        {"label": "생활 점수", "value": format_metric(row.get("living_score")), "detail": "전북 읍면동 백분위 기반"},
        {"label": "상대 등급", "value": str(row.get("living_grade") or "데이터 없음"), "detail": "현재 지표와 3년 예측을 합한 A~E"},
        {"label": "3년 후 청년 인구", "value": format_metric(row.get("youth_forecast_change_3y"), suffix="%"),
         "detail": youth_detail},
        {"label": "3년 감소 위험", "value": str(row.get("forecast_stage") or "데이터 없음"), "detail": stage_detail},
    ]


def _statute_text(row: dict[str, Any]) -> str:
    meets = row.get("business_statute_meets")
    if meets is None or pd.isna(meets):
        return "데이터 없음"
    reasons = [label for key, label in (("business_statute_by_peak", "최고치 대비 5%↓"),
                                         ("business_statute_by_run", "3년 연속 감소")) if row.get(key) is True or row.get(key) == 1]
    state = f"해당({', '.join(reasons)})" if bool(meets) else "미해당"
    drawdown = row.get("business_drawdown")
    detail = "" if drawdown is None or pd.isna(drawdown) else f" · 최고치 대비 {-float(drawdown) + 0.0:+.1%}"
    return f"{format_metric(row.get('business_count_latest'), '개', 0)} · {state}{detail}"


def build_forecast_rows(row: dict[str, Any]) -> pd.DataFrame:
    """The planning document's "current state / 3-year risk" table for one town."""
    youth_now, youth_next = row.get("youth_forecast"), row.get("youth_forecast_change_3y")
    youth_current = None
    if youth_now is not None and youth_next is not None and not pd.isna(youth_next):
        youth_current = youth_now / (1 + youth_next / 100)
    return pd.DataFrame(
        [
            ["청년 인구(20~39세)", format_metric(youth_current, "명", 0),
             f"{format_metric(youth_now, '명', 0)} ({format_metric(youth_next, '%')})", "-"],
            ["지방소멸위험지수(20~39세 여성÷65세+)",
             f"{format_metric(row.get('extinction_risk_index'), digits=2)} ({row.get('extinction_risk_tier') or '-'})",
             f"{format_metric(row.get('extinction_risk_index_forecast'), digits=2)} ({row.get('extinction_risk_tier_forecast') or '-'})",
             row.get("youth_risk_stage") or "-"],
            ["예상 청년 순이동(3년)", "-", format_metric(row.get("youth_net_migration_forecast"), "명", 0), "-"],
            ["사업체 쇠퇴(도시재생법 기준)", _statute_text(row),
             f"해당 확률 1년 {_percent(row.get('business_decline_prob_1y'))} · 3년 내 {_percent(row.get('business_decline_prob_3y'))}",
             row.get("business_risk_stage") or "-"],
            ["가장 먼저 감소할 업종", row.get("first_risk_sector") or "-",
             f"1년 {_percent(row.get('first_risk_prob_1y'))} · 3년 {_percent(row.get('first_risk_prob_3y'))}", "-"],
        ],
        columns=["항목", "현재", "3년 예측", "위험 단계"],
    )


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
        [data-testid="stSidebar"] [data-baseweb="select"] * { color: var(--ink) !important; }
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
    city = snapshot.get("selected_region", {}).get("region_name", "전북 전체")
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
        city = st.selectbox("시군", cities, index=0 if cities else None)
        towns = regions[regions["city"].eq(city)].sort_values("town")
        codes = towns["region_code"].astype(str).tolist()
        names = dict(zip(codes, towns["town"]))
        selected = st.selectbox("읍면동", codes, format_func=names.get, index=0 if codes else None)
        st.markdown("---")
        st.markdown("### 추천 가중치")
        st.caption("내가 중요하게 보는 생활 조건을 조정하세요.")
        weights = {key: st.slider(label, 0, 10, DEFAULT_WEIGHTS[key]) for key, label in DRIVER_LABELS.items()}
        st.markdown("---")
        map_mode = st.radio("지도 색상", ["생활 점수 등급", "3년 감소 위험"], index=0)
        st.markdown("---")
        st.caption("점수는 전북 읍면동 242곳 사이의 백분위 순위로 만든 상대 지표입니다.")
    return {"selected_region": selected, "weights": weights, "map_mode": map_mode}


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


def render_dashboard(snapshot: dict[str, object], map_mode: str = "생활 점수 등급") -> None:
    render_header(snapshot)
    st.write("")
    render_status_cards(snapshot)
    st.write("")

    left, right = st.columns([1.65, 1])
    with left:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown(f'<div class="section-title">전북 읍면동 지도 · {map_mode}</div>', unsafe_allow_html=True)
        regions = snapshot.get("regions", pd.DataFrame())
        if regions.empty or regions[["lat", "lon"]].isna().all(axis=None):
            st.info("지도 좌표가 연결된 지역이 없습니다.")
        else:
            map_html = create_folium_map(regions, color_by="risk" if map_mode == "3년 감소 위험" else "grade")._repr_html_()
            st.components.v1.html(map_html, height=540)
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        row = snapshot.get("selected_region", {})
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">선택 지역의 생활 점수</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="score-ring"><div class="score-number">{format_metric(row.get("living_score"))}</div></div>'
            f'<div class="score-caption">상대 등급 · {row.get("living_grade") or "데이터 없음"} / 높을수록 생활 여건이 좋음</div>',
            unsafe_allow_html=True,
        )
        _render_driver_chart(snapshot)
        st.markdown("</div>", unsafe_allow_html=True)

    st.write("")
    row = snapshot.get("selected_region", {})
    left, right = st.columns([1.7, 1])
    with left:
        st.markdown('<div class="section-title">3년 내 감소 위험 (예측)</div>', unsafe_allow_html=True)
        st.dataframe(build_forecast_rows(row), hide_index=True, use_container_width=True)
        if row.get("youth_forecast_drivers"):
            st.markdown(f'<div class="source-note">청년 예측 요인(SHAP) · {row["youth_forecast_drivers"]}</div>',
                        unsafe_allow_html=True)
        if row.get("youth_forecast_note"):
            st.warning(row["youth_forecast_note"])
    with right:
        st.markdown('<div class="section-title">먼저 감소할 위험이 큰 업종</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="source-note">{row.get("top3_risk_sectors") or "예측 결과가 없습니다."}</div>',
                    unsafe_allow_html=True)
        if row.get("first_risk_drivers"):
            st.markdown(f'<div class="source-note">가장 위험한 업종의 위험 요인(SHAP) · {row["first_risk_drivers"]}</div>',
                        unsafe_allow_html=True)

    st.write("")
    left, right = st.columns([1, 1])
    with left:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">추천 지역</div>', unsafe_allow_html=True)
        for rank, item in enumerate(snapshot.get("recommendations", []), start=1):
            st.markdown(
                f'<div class="recommendation"><div class="rec-rank">0{rank} · {item.get("living_grade") or "-"}등급'
                f' · 3년 위험 {item.get("forecast_stage") or "-"}</div>'
                f'<div class="rec-name">{item.get("region_name", "지역")} <span class="rec-score">{format_metric(item.get("living_score"))}</span></div>'
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
                ["인구", format_metric(row.get("population"), digits=0)],
                ["5년 인구 변화", format_metric(row.get("population_change_5y"), suffix="%")],
                ["청년(20~39세) 비율", format_metric(row.get("youth_ratio"), suffix="%")],
                ["청년 인구 5년 변화", format_metric(row.get("youth_change_5y"), suffix="%")],
                ["5년 변화 기준 자료", row.get("change_5y_source") or "데이터 없음"],
                ["고령(65세+) 비율", format_metric(row.get("elderly_ratio"), suffix="%")],
                ["버스 정류장(승강장) 수", format_metric(row.get("bus_stop_count"), digits=0)],
                ["경유 버스 노선 수", format_metric(row.get("bus_route_count"), digits=0)],
                ["의료기관 수", format_metric(row.get("hospital_count"), digits=0)],
                ["약국 수", format_metric(row.get("pharmacy_count"), digits=0)],
                ["사업체 수", format_metric(row.get("business_count"), digits=0)],
                ["사업체 변화(2020년 이후)", format_metric(row.get("business_change_since_2020"), suffix="%")],
                ["빈집 비율 (시군)", format_metric(row.get("city_vacant_house_ratio"), suffix="%")],
                ["청년 순이동 (시군, 청년 천 명당)", format_metric(row.get("city_youth_net_migration_rate"))],
            ],
            columns=["지표", "값"],
        )
        st.dataframe(indicators, hide_index=True, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    quality = snapshot.get("quality", {})
    warnings = quality.get("warnings", {})
    st.write("")
    for note in snapshot.get("notes", []):
        st.info(note.replace("~", "\\~"))  # "~" pairs would render as strikethrough
    if warnings:
        st.warning(" · ".join(warnings.values()))
    sources = " · ".join(quality.get("sources", {}).values())
    st.markdown(
        f'<div class="source-note">분석 단위 {quality.get("unit", "지역")} '
        f'{quality.get("region_count", len(snapshot.get("regions", [])))}곳 · 기준 자료: {sources}</div>',
        unsafe_allow_html=True,
    )

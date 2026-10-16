from __future__ import annotations

import base64
from datetime import date
from numbers import Real
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from src.dashboard_model import DEFAULT_WEIGHTS, DRIVER_LABELS, has_forecast
from src.visualization.map import GRADE_COLORS, MISSING_COLOR, create_folium_map
from src.limitations import medical_changes, weight_sensitivity, component_evidence


def format_metric(value: Any, suffix: str = "", digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "데이터 없음"
    if isinstance(value, Real):
        return f"{value:,.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _percent(value: Any) -> str:
    return "데이터 없음" if value is None or pd.isna(value) else f"{float(value):.0%}"


def accessibility_rows(development: dict[str, Any], region_code: Any) -> pd.DataFrame:
    """All departure samples of the selected town's 30-minute car-free access result."""
    frame = development.get("accessibility_samples", development.get("accessibility", pd.DataFrame()))
    if frame is None or frame.empty:
        return pd.DataFrame()
    return frame[frame.region_code.astype(str).eq(str(region_code))]


def _access_card(rows: pd.DataFrame) -> tuple[str, str]:
    if rows.empty:
        return "계산 전", "KTDB 시간표·보행망 결과가 아직 생성되지 않았습니다"
    if "departure_time" in rows:
        rows = rows.sort_values("departure_time")
    row = rows.iloc[0]
    if row.get("status") == "unconfirmed_center":
        return "계산 불가", "행정복지센터 위치 미확인"
    if row.get("status") == "unmapped_origin":
        return "계산 불가", "출발점이 보행망에 연결되지 않음"
    departure = f"평일 {str(row['departure_time'])[:5]} 출발 · " if "departure_time" in row else ""
    pharmacy = row.get("accessible_pharmacy_30m")
    parts = [f"병의원 {int(row.accessible_medical_30m)}"]
    if pharmacy is not None and pd.notna(pharmacy):
        parts.append(f"약국 {int(pharmacy)}")
    return f"{int(row.accessible_facilities_30m):,}곳", departure + " · ".join(parts)


def _medical_card(row: dict[str, Any], history: pd.DataFrame) -> tuple[str, str]:
    hospital, pharmacy = row.get("hospital_count"), row.get("pharmacy_count")
    if hospital is None or pd.isna(hospital):
        return "데이터 없음", "의료기관 자료 미연결"
    total = float(hospital) + (0 if pharmacy is None or pd.isna(pharmacy) else float(pharmacy))
    detail = f"병의원 {format_metric(hospital, digits=0)} · 약국 {format_metric(pharmacy, digits=0)}"
    changes = medical_changes(history, str(row.get("region_code"))) if history is not None else pd.DataFrame()
    if not changes.empty and changes.change.notna().all():
        detail += f" · 1년 전보다 {changes.change.sum():+.0f}"
    return f"{total:,.0f}곳", detail


def _score_rank(snapshot: dict[str, object]) -> str:
    regions = snapshot.get("regions", pd.DataFrame())
    code = snapshot.get("selected_region", {}).get("region_code")
    if regions is None or regions.empty or "living_score" not in regions:
        return ""
    ranks = regions.living_score.rank(ascending=False, method="min")
    selected = ranks[regions.region_code.astype(str).eq(str(code))]
    if selected.empty or pd.isna(selected.iloc[0]):
        return ""
    return f" · {len(regions)}곳 중 {int(selected.iloc[0])}위"


def build_status_cards(snapshot: dict[str, object]) -> list[dict[str, str]]:
    row = snapshot.get("selected_region", {})
    development = snapshot.get("development", {}) or {}
    year = row.get("youth_forecast_year")
    youth_detail = (f"{int(year)}년 예측 · 80% 구간 {format_metric(row.get('youth_forecast_change_low'), '%')}"
                    f" ~ {format_metric(row.get('youth_forecast_change_high'), '%')}" if year else "예측 미연결")
    stage_detail = (f"지방소멸위험지수 {format_metric(row.get('extinction_risk_index_forecast'), digits=2)}"
                    f"({row.get('extinction_risk_tier_forecast') or '-'}) · 사업체 {row.get('business_risk_stage') or '-'}"
                    if row.get("forecast_stage") else "예측 미연결")
    access_value, access_detail = _access_card(accessibility_rows(development, row.get("region_code")))
    medical_value, medical_detail = _medical_card(row, development.get("medical_history", pd.DataFrame()))
    return [
        {"key": "score", "label": "생활 점수", "value": format_metric(row.get("living_score")),
         "detail": f"상대 등급 {row.get('living_grade') or '-'}{_score_rank(snapshot)}"},
        {"key": "access", "label": "차 없이 30분 이용 시설", "value": access_value, "detail": access_detail},
        {"key": "youth", "label": "3년 후 청년 인구", "value": format_metric(row.get("youth_forecast_change_3y"), suffix="%"),
         "detail": youth_detail},
        {"key": "risk", "label": "3년 감소 위험", "value": str(row.get("forecast_stage") or "데이터 없음"), "detail": stage_detail},
        {"key": "medical", "label": "의료시설", "value": medical_value, "detail": medical_detail},
        {"key": "population", "label": "인구", "value": format_metric(row.get("population"), "명", 0),
         "detail": f"청년 {format_metric(row.get('youth_ratio'), '%')} · 고령 {format_metric(row.get('elderly_ratio'), '%')}"
                   f" · 5년 {format_metric(row.get('population_change_5y'), '%')}"},
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


LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "logo.svg"
STAGE_CLASS = {"안정": "stable", "주의": "caution", "위험": "danger"}


def _logo_img() -> str:
    try:
        encoded = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f'<img src="data:image/svg+xml;base64,{encoded}" alt="전북 생활 온도 로고">'


def _chip(text: Any, tone: str) -> str:
    return f'<span class="chip chip-{tone}">{text}</span>'


def _stage_chip(stage: Any) -> str:
    return _chip(stage, STAGE_CLASS[stage]) if stage in STAGE_CLASS else _chip(stage or "데이터 없음", "muted")


def inject_styles() -> None:
    st.markdown(
        """
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
        <style>
        :root {
          --orange: #ff6f0f;
          --orange-strong: #e85d00;
          --orange-soft: #fff2e8;
          --ink: #212124;
          --body: #4d5159;
          --muted: #868b94;
          --line: #eaebee;
          --surface: #ffffff;
          --canvas: #f2f3f6;
          --green: #00a05b;
          --green-soft: #e6f6ee;
          --amber: #d98a00;
          --amber-soft: #fff6e0;
          --red: #f04438;
          --red-soft: #feeceb;
          --radius: 18px;
          --shadow: 0 1px 2px rgba(33, 33, 36, .04), 0 4px 14px rgba(33, 33, 36, .05);
        }
        html, body, .stApp, .stApp button, .stApp input, .stApp textarea, [data-testid="stDialog"] {
          font-family: Pretendard, -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif !important;
        }
        .stApp { background: var(--canvas); color: var(--ink); }
        /* Keep native Streamlit text readable on the fixed light canvas,
           including when a browser session previously selected dark mode. */
        [data-testid="stMain"], [data-testid="stDialog"] {
          --text-color: #212124;
          --background-color: #f2f3f6;
          --secondary-background-color: #ffffff;
          color: var(--ink);
          color-scheme: light;
        }
        [data-testid="stMain"] [data-testid="stMarkdownContainer"],
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stDialog"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stDialog"] h4,
        [data-testid="stMain"] [data-testid="stHeading"] h4,
        [data-testid="stMetricLabel"], [data-testid="stMetricValue"],
        [data-testid="stWidgetLabel"],
        [data-testid="stMain"] [data-testid="stExpander"] summary {
          color: var(--ink) !important;
        }
        [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: var(--muted) !important; }
        [data-baseweb="select"] > div { background-color: #ffffff; color: var(--ink); border-radius: 12px; }
        [data-testid="stHeader"] { background: transparent; }
        .block-container { max-width: 1280px; padding-top: 1.6rem; padding-bottom: 3rem; }
        /* Sidebar: white sheet with soft section dividers. */
        [data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid var(--line); }
        [data-testid="stSidebar"] * { color: var(--ink); }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { color: var(--muted) !important; }
        [data-testid="stSidebar"] hr { border-color: var(--line); margin: 1rem 0; }
        [data-testid="stSidebar"] [data-baseweb="select"] > div { background: #f7f8fa; border: 1px solid var(--line); }
        .side-brand { display: flex; align-items: center; gap: .5rem; font-size: 1.15rem; font-weight: 800; color: var(--ink); }
        .side-brand img { width: 34px; height: 34px; display: block; }
        .side-section { font-size: .95rem; font-weight: 700; color: var(--ink); margin: .2rem 0 .1rem; }
        /* Header */
        .loc-chip { display: inline-flex; align-items: center; gap: .3rem; background: #ffffff; border: 1px solid var(--line);
          border-radius: 999px; padding: .3rem .75rem; font-size: .82rem; font-weight: 600; color: var(--body); }
        .dashboard-title { color: var(--ink); font-size: 2rem; line-height: 1.2; font-weight: 800; margin: .6rem 0 0; letter-spacing: -.02em; }
        .dashboard-subtitle { color: var(--muted); margin: .35rem 0 0; font-size: .98rem; }
        /* Cards */
        .status-card, .hero-card { background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow);
          border: 1px solid transparent; transition: border-color .15s, box-shadow .15s, transform .15s; }
        .status-card { padding: 1.05rem 1.1rem 1rem; min-height: 176px; display: flex; flex-direction: column; }
        .status-foot { margin-top: auto; padding-top: .6rem; }
        .status-more { color: var(--muted); font-size: .78rem; font-weight: 600; white-space: nowrap; }
        .status-label { color: var(--body); font-size: .86rem; font-weight: 600; }
        .status-value { color: var(--ink); font-size: 1.5rem; font-weight: 800; margin-top: .55rem; letter-spacing: -.02em; }
        .status-detail { color: var(--muted); font-size: .76rem; margin-top: .3rem; line-height: 1.45; }
        .status-value .chip { font-size: 1rem; padding: .25rem .7rem; }
        .hero-card { padding: 1.3rem 1.5rem; }
        .hero-top { display: flex; justify-content: space-between; align-items: center; }
        .hero-label { font-size: .95rem; font-weight: 700; color: var(--ink); }
        .hero-main { display: flex; align-items: baseline; gap: .6rem; margin-top: .4rem; }
        .hero-temp { font-size: 2.6rem; font-weight: 800; letter-spacing: -.03em; line-height: 1; }
        .hero-side { margin-left: auto; text-align: right; color: var(--body); font-size: .86rem; line-height: 1.6; }
        .temp-track { height: 10px; border-radius: 999px; background: #eceef1; margin-top: .9rem; overflow: hidden; }
        .temp-fill { height: 100%; border-radius: 999px; }
        .temp-scale { display: flex; justify-content: space-between; color: var(--muted); font-size: .72rem; margin-top: .35rem; }
        /* The whole card is a click target: the Streamlit button is stretched invisibly over it. */
        [class*="st-key-kpi_"] { position: relative; }
        [class*="st-key-kpi_"] [class*="st-key-open_"] { position: absolute; inset: 0; z-index: 2; width: 100% !important; max-width: none; }
        [class*="st-key-kpi_"] [class*="st-key-open_"] div { width: 100%; height: 100%; }
        [class*="st-key-kpi_"] [class*="st-key-open_"] button { width: 100%; height: 100%; opacity: 0; cursor: pointer; }
        [class*="st-key-kpi_"]:hover .status-card, [class*="st-key-kpi_"]:hover .hero-card,
        [class*="st-key-kpi_"]:has(button:focus-visible) .status-card,
        [class*="st-key-kpi_"]:has(button:focus-visible) .hero-card {
          border-color: #ffd2b3; box-shadow: 0 8px 24px rgba(255, 111, 15, .14); transform: translateY(-2px); }
        [class*="st-key-kpi_"]:hover .status-more { color: var(--orange); }
        /* Chips */
        .chip { display: inline-block; border-radius: 999px; padding: .12rem .55rem; font-size: .74rem; font-weight: 700; vertical-align: middle; }
        .chip-stable { background: var(--green-soft); color: var(--green); }
        .chip-caution { background: var(--amber-soft); color: var(--amber); }
        .chip-danger { background: var(--red-soft); color: var(--red); }
        .chip-muted { background: #f2f3f6; color: var(--muted); }
        .chip-orange { background: var(--orange-soft); color: var(--orange-strong); }
        /* White panels for the map and recommendation list */
        [class*="st-key-panel_"] { background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow); padding: 1.1rem 1.2rem; }
        .section-title { color: var(--ink); font-size: 1.08rem; font-weight: 800; margin: 0 0 .2rem; }
        .section-sub { color: var(--muted); font-size: .8rem; margin-bottom: .6rem; }
        .rec-item { display: flex; gap: .8rem; align-items: flex-start; padding: .85rem 0; border-top: 1px solid var(--line); }
        .rec-item:first-of-type { border-top: 0; }
        .rec-rank { flex: none; width: 28px; height: 28px; border-radius: 50%; background: var(--orange-soft); color: var(--orange-strong);
          font-weight: 800; font-size: .85rem; display: grid; place-items: center; }
        .rec-rank.top { background: var(--orange); color: #fff; }
        .rec-body { flex: 1; min-width: 0; }
        .rec-name { color: var(--ink); font-weight: 700; font-size: .98rem; display: flex; flex-wrap: wrap; gap: .3rem; align-items: center; }
        .rec-reason { color: var(--muted); font-size: .8rem; margin-top: .2rem; line-height: 1.45; }
        .rec-score { flex: none; color: var(--orange); font-weight: 800; font-size: 1.05rem; }
        .source-note { color: var(--muted); font-size: .8rem; line-height: 1.55; }
        /* Detail dialogs as rounded sheets */
        [data-testid="stDialog"] [role="dialog"] { border-radius: 22px; }
        [data-testid="stMetric"] { background: #f7f8fa; border-radius: 14px; padding: .7rem .9rem; }
        [data-testid="stMetricValue"] { font-weight: 800; }
        [data-testid="stExpander"] details { background: var(--surface); border: 0; border-radius: var(--radius); box-shadow: var(--shadow); }
        .stButton button[kind="primary"], .stButton button[kind="secondary"] { border-radius: 12px; }
        [data-testid="stAlert"] { border-radius: 14px; }
        .st-key-report_action { gap: .35rem; padding-bottom: .3rem; }
        .st-key-report_action button { border-radius: 14px; min-height: 3rem; font-weight: 700;
          background: var(--orange); border-color: var(--orange); box-shadow: 0 6px 16px rgba(255, 111, 15, .25); }
        .st-key-report_action button:hover { background: var(--orange-strong); border-color: var(--orange-strong); }
        .st-key-report_action button p { font-size: .98rem; font-weight: 700; color: #fff !important; }
        .report-hint { color: var(--muted); font-size: .76rem; text-align: center; }
        [data-testid="stDialog"] [data-testid="stCaptionContainer"] p { color: #6b7079 !important; }
        @media (max-width: 640px) {
          .dashboard-title { font-size: 1.6rem; word-break: keep-all; }
          .hero-main { flex-wrap: wrap; row-gap: .5rem; }
          .hero-side { margin-left: 0; text-align: left; flex-basis: 100%; }
          .hero-temp { font-size: 2.2rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(snapshot: dict[str, object]) -> None:
    row = snapshot.get("selected_region", {})
    city, town = row.get("city"), row.get("town")
    title = town or row.get("region_name", "전북 전체")
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    with left:
        st.markdown(f'<span class="loc-chip">전북 {city or ""}</span>', unsafe_allow_html=True)
        st.markdown(f'<h1 class="dashboard-title">{title} 생활 리포트</h1>', unsafe_allow_html=True)
        st.markdown('<p class="dashboard-subtitle">우리 동네 생활 온도와 3년 뒤 변화를 한눈에 확인해요.</p>',
                    unsafe_allow_html=True)
    with right, st.container(key="report_action"):
        render_report_button(snapshot)


def render_report_button(snapshot: dict[str, object]) -> None:
    from src.report import build_report_pdf, report_font_available  # imports src.ui, so load lazily

    row = snapshot.get("selected_region", {})
    name = str(row.get("region_name") or "전북").replace(" ", "_")
    available = bool(row) and report_font_available()
    # The PDF is built only when the button is pressed, not on every rerun.
    st.download_button("보고서 만들기", data=lambda: build_report_pdf(snapshot),
                       file_name=f"전북생활온도_{name}_{date.today():%Y%m%d}.pdf", mime="application/pdf",
                       on_click="ignore", type="primary", icon=None, width="stretch", disabled=not available,
                       key="download_report")
    st.markdown('<div class="report-hint">선택한 동네 분석을 PDF로 받아요</div>' if available else
                '<div class="report-hint">보고서용 한글 폰트가 없어 만들 수 없어요</div>', unsafe_allow_html=True)


def render_sidebar(regions: pd.DataFrame, analysis: dict | None = None) -> dict[str, object]:
    with st.sidebar:
        st.markdown(f'<div class="side-brand">{_logo_img()}전북 생활 온도</div>', unsafe_allow_html=True)
        st.caption("내 동네를 고르면 리포트가 바로 바뀌어요.")
        st.markdown('<div class="side-section">동네 고르기</div>', unsafe_allow_html=True)
        cities = sorted(regions["city"].dropna().astype(str).unique().tolist())
        city = st.selectbox("시군", cities, index=0 if cities else None)
        towns = regions[regions["city"].eq(city)].sort_values("town")
        codes = towns["region_code"].astype(str).tolist()
        names = dict(zip(codes, towns["town"]))
        selected = st.selectbox("읍면동", codes, format_func=names.get, index=0 if codes else None)
        st.markdown("---")
        st.markdown('<div class="side-section">나에게 중요한 조건</div>', unsafe_allow_html=True)
        st.caption("중요할수록 오른쪽으로 옮겨 주세요. 추천 동네가 달라져요.")
        weights = {key: st.slider(label, 0, 10, DEFAULT_WEIGHTS[key]) for key, label in DRIVER_LABELS.items()}
        suggested = (analysis or {}).get("suggested_weights", {})
        if suggested and st.checkbox("패널 분석 제안 가중치 적용", value=False):
            # Preserve the combined slider budget of the supported components.
            budget = sum(weights[key] for key in suggested)
            weights.update({key: value * budget for key, value in suggested.items()})
            st.caption("유의한 양의 연관성이 확인된 항목 사이의 비중만 조정합니다.")
        st.markdown("---")
        st.markdown('<div class="side-section">지도 보기</div>', unsafe_allow_html=True)
        map_mode = st.radio("지도 색상", ["생활 점수 등급", "3년 감소 위험"], index=0, label_visibility="collapsed")
        st.markdown("---")
        st.caption("생활 온도는 전북 읍면동 242곳 사이의 백분위 순위로 만든 상대 지표예요.")
    return {"selected_region": selected, "weights": weights, "map_mode": map_mode}


def _clickable(card: dict[str, str], snapshot: dict[str, object], body: str) -> None:
    with st.container(key=f"kpi_{card['key']}"):
        st.markdown(body, unsafe_allow_html=True)
        if st.button(f'{card["label"]} 자세히 보기', key=f"open_{card['key']}", width="stretch"):
            DETAIL_DIALOGS[card["key"]](snapshot)


def _hero_html(card: dict[str, str], row: dict[str, Any]) -> str:
    score, grade = row.get("living_score"), row.get("living_grade")
    color = GRADE_COLORS.get(grade, MISSING_COLOR)
    width = 0 if score is None or pd.isna(score) else max(0.0, min(100.0, float(score)))
    value = card["value"] if card["value"] == "데이터 없음" else f'{card["value"]}°'
    return (
        f'<div class="hero-card"><div class="hero-top"><span class="hero-label">우리 동네 생활 온도</span>'
        f'<span class="status-more">자세히 보기 ›</span></div>'
        f'<div class="hero-main"><span class="hero-temp" style="color:{color}">{value}</span>'
        f'<span class="hero-side">{card["detail"]}<br>전북 읍면동 백분위 기반 상대 점수</span></div>'
        f'<div class="temp-track"><div class="temp-fill" style="width:{width:.1f}%;'
        f'background:linear-gradient(90deg,{GRADE_COLORS["E"]},{color})"></div></div>'
        f'<div class="temp-scale"><span>0° 쌀쌀해요</span><span>50°</span><span>100° 따뜻해요</span></div></div>'
    )


def render_status_cards(snapshot: dict[str, object]) -> None:
    cards = build_status_cards(snapshot)
    row = snapshot.get("selected_region", {})
    _clickable(cards[0], snapshot, _hero_html(cards[0], row))
    st.write("")
    for column, card in zip(st.columns(len(cards) - 1), cards[1:]):
        value = _stage_chip(card["value"]) if card["key"] == "risk" else card["value"]
        with column:
            _clickable(card, snapshot,
                       f'<div class="status-card"><div class="status-label">{card["label"]}</div>'
                       f'<div class="status-value">{value}</div>'
                       f'<div class="status-detail">{card["detail"]}</div>'
                       f'<div class="status-more status-foot">자세히 보기 ›</div></div>')


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
        color_continuous_scale=["#ffe2cc", "#ff6f0f"],
        text="score",
    )
    figure.update_traces(marker_line_width=0, texttemplate="%{text:.0f}", textposition="outside")
    figure.update_layout(
        height=270,
        margin=dict(l=0, r=0, t=8, b=0),
        showlegend=False,
        coloraxis_showscale=False,
        xaxis_title=None,
        yaxis_title=None,
        plot_bgcolor="white",
        paper_bgcolor="white",
        bargap=.45,
        font=dict(family="Pretendard, sans-serif", color="#4d5159"),
    )
    figure.update_xaxes(showgrid=True, gridcolor="#f2f3f6")
    st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})


def render_dashboard(snapshot: dict[str, object], map_mode: str = "생활 점수 등급") -> None:
    render_header(snapshot)
    st.write("")
    render_status_cards(snapshot)
    st.write("")

    left, right = st.columns([1.6, 1])
    with left, st.container(key="panel_map"):
        st.markdown('<div class="section-title">전북 동네 지도</div>'
                    f'<div class="section-sub">{map_mode} 기준으로 색칠했어요. 점을 누르면 요약이 보여요.</div>',
                    unsafe_allow_html=True)
        regions = snapshot.get("regions", pd.DataFrame())
        if regions.empty or regions[["lat", "lon"]].isna().all(axis=None):
            st.info("지도 좌표가 연결된 지역이 없습니다.")
        else:
            map_html = create_folium_map(regions, color_by="risk" if map_mode == "3년 감소 위험" else "grade")._repr_html_()
            st.components.v1.html(map_html, height=520)

    with right, st.container(key="panel_recommend"):
        st.markdown('<div class="section-title">이런 동네는 어때요?</div>'
                    '<div class="section-sub">내가 고른 조건으로 생활 온도가 가장 높은 곳이에요.</div>',
                    unsafe_allow_html=True)
        items = []
        for rank, item in enumerate(snapshot.get("recommendations", []), start=1):
            grade = item.get("living_grade")
            items.append(
                f'<div class="rec-item"><div class="rec-rank{" top" if rank == 1 else ""}">{rank}</div>'
                f'<div class="rec-body"><div class="rec-name">{item.get("region_name", "지역")}'
                f'{_chip(f"{grade}등급", "orange") if grade else ""}{_stage_chip(item.get("forecast_stage"))}</div>'
                f'<div class="rec-reason">{item.get("reason", "근거 설명이 없습니다.")}</div></div>'
                f'<div class="rec-score">{format_metric(item.get("living_score"))}°</div></div>'
            )
        st.markdown("".join(items) or '<div class="source-note">추천할 지역이 없습니다.</div>', unsafe_allow_html=True)

    for warning in (snapshot.get("development", {}) or {}).get("warnings", []):
        st.warning(warning)
    quality = snapshot.get("quality", {})
    st.write("")
    with st.expander("자료 출처와 유의사항", expanded=False):
        for note in snapshot.get("notes", []):
            st.info(note.replace("~", "\\~"))  # "~" pairs would render as strikethrough
        for warning in quality.get("warnings", {}).values():
            st.caption(f"· {warning}")
        sources = " · ".join(quality.get("sources", {}).values())
        st.markdown(
            f'<div class="source-note">분석 단위 {quality.get("unit", "지역")} '
            f'{quality.get("region_count", len(snapshot.get("regions", [])))}곳 · 기준 자료: {sources}</div>',
            unsafe_allow_html=True,
        )


def _score_detail(snapshot: dict[str, object]) -> None:
    row = snapshot.get("selected_region", {})
    a, b = st.columns(2)
    a.metric("생활 점수", format_metric(row.get("living_score")))
    b.metric("상대 등급", row.get("living_grade") or "데이터 없음")
    st.markdown("#### 항목별 점수")
    _render_driver_chart(snapshot)
    st.markdown("#### 가중치에 따른 순위 변동")
    sensitivity = weight_sensitivity(snapshot.get("regions", pd.DataFrame()), snapshot.get("weights"))
    selected = sensitivity[sensitivity.region_code.astype(str).eq(str(row.get("region_code")))] if not sensitivity.empty else sensitivity
    if selected.empty:
        st.info("가중치를 하나 이상 지정하면 순위 민감도를 계산합니다.")
    else:
        item = selected.iloc[0]
        a, b, c = st.columns(3)
        a.metric("현재 공동순위", int(item.current_rank))
        b.metric("가중치 변경 시 공동순위 범위", f"{item.best_rank}~{item.worst_rank}위")
        c.metric("나타난 상대 등급", item.grade_variants)
        st.caption(f"현재 가중치에서 항목 하나씩 ±20% 바꾼 {item.scenario_count}개 시나리오의 범위입니다. 선택한 시나리오에 대한 민감도이며 통계적 신뢰구간이나 전체 가중치 조합의 범위가 아닙니다. 생활 점수와 A~E는 자체 상대평가입니다.")
    st.markdown("#### 점수에 사용한 자료와 해석 범위")
    st.dataframe(component_evidence(row, has_forecast(snapshot.get("regions", pd.DataFrame()))), hide_index=True)
    st.caption("시군 빈집 비율과 시군 청년 순이동을 읍면동의 직접 관측값으로 해석하지 않습니다. 코호트 잔차로 구한 읍면동 순이동은 연령분포와 사망 미반영 가정이 있는 추정값입니다.")


def _access_detail(snapshot: dict[str, object]) -> None:
    development = snapshot.get("development", {}) or {}
    rows = accessibility_rows(development, snapshot.get("selected_region", {}).get("region_code"))
    if not rows.empty and "departure_time" in rows:
        departure = st.selectbox("평일 분석 출발 시각", sorted(rows.departure_time.unique()), key="accessibility_departure",
                                 format_func=lambda value: f"평일 {str(value)[:5]} 출발")
        rows = rows[rows.departure_time.eq(departure)]
    metadata = development.get("accessibility_metadata", {})
    if not rows.empty and pd.notna(rows.iloc[0].get("origin_name")):
        origin_row = rows.iloc[0]
        st.markdown(f"**출발 행정복지센터: {origin_row.origin_name}**")
        if pd.notna(origin_row.get("origin_source_url")):
            st.caption(f"위치 출처: {origin_row.origin_source_url}")
    if rows.empty:
        st.info("선택 지역의 생활권 결과가 없습니다. 도보망과 시간표를 준비한 뒤 계산하면 표시됩니다.")
    elif rows.iloc[0].get("status") == "unconfirmed_center":
        st.info("해당 읍면동의 행정복지센터 위치를 확인하지 못해 계산하지 않았습니다. 다른 대표점으로 대체하지 않습니다.")
    elif rows.iloc[0].get("status") == "unmapped_origin":
        st.info("출발점을 허용 거리 내 보행망에 연결하지 못해 계산하지 않았습니다. 시설이 0개라는 뜻이 아닙니다.")
    else:
        row = rows.iloc[0]
        a, b, c = st.columns(3)
        a.metric("30분 내 병의원·약국 합계" if row.get("method") == "ktdb_schedule_walk" else "이용 가능한 전체 시설",
                 int(row.accessible_facilities_30m))
        b.metric("병의원", int(row.accessible_medical_30m))
        if "accessible_pharmacy_30m" in row:
            c.metric("약국", int(row.accessible_pharmacy_30m))
        elif pd.notna(row.get("accessible_jobs_30m")):
            c.metric("일자리 거점", int(row.accessible_jobs_30m))
        if row.get("method") == "ktdb_schedule_walk":
            st.caption("KTDB 2025년 3월 평일 시간표·HIRA 2025년 3월 의료시설·OSM 2026년 10월 보행망을 결합한 추정입니다. 최신 실시간 운행이나 주말 결과가 아닙니다.")
            st.caption(f"행정복지센터 출발 · 도보→버스→도보 및 도보 단독 경로 · 승차·환승 대기는 다음 출발 시각까지 반영 · 매 승차 여유 {metadata.get('boarding_margin_seconds',30)}초. 실제 진료 가능 여부·일자리 접근성은 계산하지 않습니다.")
        else:
            st.caption(f"읍면동 대표 출발점 기준 · 도보+버스+도보 · 매 승차 대기는 배차간격의 {row.wait_factor:.0%}. 시간표 기반 실시간 도착 보장이 아닌 기대 이동시간입니다.")
    if metadata.get("method") == "ktdb_schedule_walk":
        walking = metadata.get("walking", {})
        st.caption(f"보행속도 가정 {walking.get('walking_metres_per_minute',80)}m/분 · 도로까지 최대 {walking.get('max_connector_m',100)}m 연결 구간은 직선 추정입니다. 횡단·출입구·경사·휠체어 통행은 별도 검증이 필요합니다.")
        st.caption(f"행정복지센터 기준 계산 가능 {metadata.get('computed_origins')}곳 / 전체 {metadata.get('origins')}곳. 센터 위치 미확인 {metadata.get('unconfirmed_centers',0)}곳, 보행망 미연결 {metadata.get('centers_off_network',0)}곳. 보행망에 연결된 시설만 집계하므로 실제 이용 가능 시설을 빠뜨릴 수 있습니다. 기존 교통 점수는 정류장·노선 수 기준을 유지합니다.")
    row = snapshot.get("selected_region", {})
    st.markdown("#### 현재 대중교통 기반")
    a, b = st.columns(2)
    a.metric("버스 정류장(승강장) 수", format_metric(row.get("bus_stop_count"), digits=0))
    b.metric("경유 버스 노선 수", format_metric(row.get("bus_route_count"), digits=0))


def _youth_detail(snapshot: dict[str, object]) -> None:
    row = snapshot.get("selected_region", {})
    rows = build_forecast_rows(row)
    st.dataframe(rows.iloc[:3], hide_index=True, use_container_width=True)
    if row.get("youth_forecast_drivers"):
        st.markdown(f'<div class="source-note">청년 예측 요인(SHAP) · {row["youth_forecast_drivers"]}</div>',
                    unsafe_allow_html=True)
    if row.get("youth_forecast_note"):
        st.warning(row["youth_forecast_note"])
    st.markdown("#### 청년 순이동과 전년도 생활 인프라의 관계")
    analysis = (snapshot.get("development", {}) or {}).get("analysis")
    if analysis:
        coefficients = pd.DataFrame(analysis["coefficients"])
        if not coefficients.empty:
            coefficients["component"] = coefficients.component.map(DRIVER_LABELS)
            st.dataframe(coefficients.rename(columns={"component": "항목", "coefficient": "표준화 계수",
                "p_value": "p값", "ci_low": "95% 구간 하한", "ci_high": "95% 구간 상한"}), hide_index=True)
        st.caption(analysis.get("note", ""))
        if not analysis.get("suggested_weights"):
            st.info("이번 분석에서는 가중치를 제안할 유의한 양의 연관성이 확인되지 않아 현재 가중치를 유지합니다.")
        st.caption(f"분석 관측값 {analysis.get('observations')}개 · {analysis.get('year_start')}~{analysis.get('year_end')}년")
    else:
        st.info("읍면동별 청년 순이동률과 연도별 인프라 이력이 연결되면 고정효과 분석과 가중치 제안을 제공합니다. 현재 스냅샷을 과거 연도에 복사해 분석하지 않습니다.")


def _risk_detail(snapshot: dict[str, object]) -> None:
    row = snapshot.get("selected_region", {})
    st.dataframe(build_forecast_rows(row), hide_index=True, use_container_width=True)
    st.markdown("#### 먼저 감소할 위험이 큰 업종")
    st.markdown(f'<div class="source-note">{row.get("top3_risk_sectors") or "예측 결과가 없습니다."}</div>',
                unsafe_allow_html=True)
    if row.get("first_risk_drivers"):
        st.markdown(f'<div class="source-note">가장 위험한 업종의 위험 요인(SHAP) · {row["first_risk_drivers"]}</div>',
                    unsafe_allow_html=True)
    a, b = st.columns(2)
    a.metric("사업체 수", format_metric(row.get("business_count"), digits=0))
    b.metric("사업체 변화(2020년 이후)", format_metric(row.get("business_change_since_2020"), suffix="%"))


def _medical_detail(snapshot: dict[str, object]) -> None:
    row = snapshot.get("selected_region", {})
    a, b = st.columns(2)
    a.metric("의료기관 수", format_metric(row.get("hospital_count"), digits=0))
    b.metric("약국 수", format_metric(row.get("pharmacy_count"), digits=0))
    st.markdown("#### 관측된 의료시설 증감")
    history = (snapshot.get("development", {}) or {}).get("medical_history", pd.DataFrame())
    changes = medical_changes(history, str(row.get("region_code")))
    if changes.empty:
        st.info("선택 지역의 비교 가능한 의료시설 이력이 없습니다.")
        return
    display = changes.copy()
    display["kind"] = display.kind.map({"hospital": "병의원", "pharmacy": "약국"})
    display["current_coverage"] *= 100
    display["baseline_coverage"] *= 100
    st.dataframe(display.rename(columns={"kind": "구분", "current_period": "최근 시점", "current_count": "최근 기관 수",
        "baseline_period": "비교 시점", "baseline_count": "비교 기관 수", "change": "증감 수", "change_pct": "증감률 (%)",
        "current_coverage": "최근 전북 위치 배정률 (%)", "baseline_coverage": "비교 전북 위치 배정률 (%)"}), hide_index=True)
    st.caption("최근 시점과 12개월 이상 앞선 가장 가까운 관측을 비교합니다. 증감은 관측 기관 수의 차이이며, 실제 개·폐업이나 향후 감소 예측이 아닙니다. 위치 미배정·주소 정정·이전의 영향을 받을 수 있습니다. 비교 기관 수가 0이면 증감률을 계산하지 않습니다.")
    selected = history[history.region_code.astype(str).eq(str(row.get("region_code")))].copy()
    selected["facility_kind"] = selected.facility_kind.map({"hospital": "병의원", "pharmacy": "약국"})
    st.dataframe(selected[["snapshot_period", "facility_kind", "count", "mapping_coverage"]].rename(
        columns={"snapshot_period": "관측 시점", "facility_kind": "구분", "count": "읍면동 관측 기관 수", "mapping_coverage": "전북 위치 배정률 (0~1)"}), hide_index=True)
    st.caption("수집 간격은 불규칙합니다. 해당 시점·종류 전체의 위치 배정 자료가 없으면 기관 수를 결측으로 표시합니다.")


def build_indicator_rows(row: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["인구", format_metric(row.get("population"), digits=0)],
            ["5년 인구 변화", format_metric(row.get("population_change_5y"), suffix="%")],
            ["청년(20~39세) 비율", format_metric(row.get("youth_ratio"), suffix="%")],
            ["청년 인구 5년 변화", format_metric(row.get("youth_change_5y"), suffix="%")],
            ["5년 변화 기준 자료", row.get("change_5y_source") or "데이터 없음"],
            ["고령(65세+) 비율", format_metric(row.get("elderly_ratio"), suffix="%")],
            ["빈집 비율 (시군)", format_metric(row.get("city_vacant_house_ratio"), suffix="%")],
            ["청년 순이동 (시군, 청년 천 명당)", format_metric(row.get("city_youth_net_migration_rate"))],
        ],
        columns=["지표", "값"],
    )


def _population_detail(snapshot: dict[str, object]) -> None:
    row = snapshot.get("selected_region", {})
    st.dataframe(build_indicator_rows(row), hide_index=True, use_container_width=True)
    st.caption("빈집 비율과 청년 순이동은 시군 단위 자료라 같은 시군의 읍면동에 같은 값이 들어갑니다.")


DETAIL_DIALOGS = {
    "score": st.dialog("생활 온도 자세히 보기", width="large")(_score_detail),
    "access": st.dialog("차 없이 30분 안에 갈 수 있는 곳", width="large")(_access_detail),
    "youth": st.dialog("3년 후 청년 인구 예측", width="large")(_youth_detail),
    "risk": st.dialog("3년 내 감소 위험 (예측)", width="large")(_risk_detail),
    "medical": st.dialog("의료시설 현황과 변화", width="large")(_medical_detail),
    "population": st.dialog("인구 구조와 핵심 지표", width="large")(_population_detail),
}

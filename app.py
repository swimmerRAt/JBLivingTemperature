from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.dashboard_model import build_dashboard_snapshot
from src.development import load_development_results
from src.data_pipeline import load_dashboard_data
from src.ui import inject_styles, render_dashboard, render_sidebar

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

st.set_page_config(
    page_title="전북 생활권 인사이트",
    page_icon=str(ROOT / "assets" / "favicon.png"),
    layout="wide",
    initial_sidebar_state="auto",
)


@st.cache_data(show_spinner="지역 데이터를 불러오는 중입니다.")
def get_dashboard_data():
    return load_dashboard_data(DATA_DIR)


inject_styles()

try:
    regions, quality = get_dashboard_data()
except FileNotFoundError as exc:
    st.error(f"필수 데이터가 없습니다: {exc}")
    st.stop()

if regions.empty:
    st.warning("표시할 지역 데이터가 없습니다.")
    st.stop()

development = load_development_results(DATA_DIR)
controls = render_sidebar(regions, development.get("analysis"))
snapshot = build_dashboard_snapshot(
    regions,
    quality,
    selected_region=controls["selected_region"],
    weights=controls["weights"],
)
snapshot["development"] = development
snapshot["weights"] = controls["weights"]
render_dashboard(snapshot, controls["map_mode"])

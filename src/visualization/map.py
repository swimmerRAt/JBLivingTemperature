from __future__ import annotations

import folium
import pandas as pd

GRADE_COLORS = {"A": "#1f7a5c", "B": "#2d9d78", "C": "#efb34d", "D": "#e58a4e", "E": "#d96161"}
STAGE_COLORS = {"안정": "#2d9d78", "주의": "#efb34d", "위험": "#d96161"}
MISSING_COLOR = "#8b9bad"
LEGENDS = {
    "grade": ("생활 점수 상대 등급", [("A 상위 20%", "A"), ("B", "B"), ("C", "C"), ("D", "D"), ("E 하위 20%", "E")], GRADE_COLORS),
    "risk": ("3년 감소 위험 (예측)", [("안정", "안정"), ("주의", "주의"), ("위험", "위험")], STAGE_COLORS),
}


def _count(value: object) -> str:
    return "데이터 없음" if value is None or pd.isna(value) else f"{float(value):,.0f}"


def _percent(value: object) -> str:
    return "데이터 없음" if value is None or pd.isna(value) else f"{float(value):+.1f}%"


def create_folium_map(df: pd.DataFrame, color_by: str = "grade") -> folium.Map:
    located = df.dropna(subset=["lat", "lon"])
    m = folium.Map(location=[located["lat"].mean(), located["lon"].mean()], zoom_start=9, tiles="OpenStreetMap")
    title, items, colors = LEGENDS[color_by]

    for _, row in located.iterrows():
        grade = row.get("living_grade") or "C"
        stage = row.get("forecast_stage") if isinstance(row.get("forecast_stage"), str) else None
        color = colors.get(grade if color_by == "grade" else stage, MISSING_COLOR)
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=6,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            weight=1,
            tooltip=row.get("region_name", row.get("city")),
            popup=(
                f"{row.get('region_name', row.get('city'))}<br>"
                f"생활 점수: {float(row.get('living_score', 0)):.1f} ({grade}등급)<br>"
                f"3년 감소 위험: {stage or '데이터 없음'}<br>"
                f"3년 후 청년 인구: {_percent(row.get('youth_forecast_change_3y'))}<br>"
                f"인구: {_count(row.get('population'))} · 버스 정류장: {_count(row.get('bus_stop_count'))}"
                f" · 의료기관: {_count(row.get('hospital_count'))}"
            ),
        ).add_to(m)

    rows = "".join(f'<span style="color:{colors[key]};">●</span> {label}<br>' for label, key in items)
    legend_html = f"""
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 9999; background: white; border: 1px solid #ccc; padding: 10px; font-size: 12px;">
      <b>{title}</b><br>{rows}
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    return m

from __future__ import annotations

import folium
import pandas as pd


def _risk_grade(stability: float) -> str:
    if stability >= 70:
        return "안정"
    if stability >= 40:
        return "주의"
    return "위험"


def create_folium_map(df: pd.DataFrame) -> folium.Map:
    center_lat = df["lat"].mean()
    center_lon = df["lon"].mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=9, tiles="CartoDB positron")

    for _, row in df.iterrows():
        temp = row.get("no_car_temperature", row["overall_score"])
        color = "#d73027" if temp >= 80 else "#fc8d59" if temp >= 60 else "#fee08b" if temp >= 40 else "#91bfdb"
        risk_grade = _risk_grade(float(row["infrastructure_stability"]))
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=10,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
            popup=(
                f"{row['city']} {row['district']}<br>"
                f"무차량 생활 온도: {temp:.1f}<br>"
                f"종합 점수: {row['overall_score']:.1f}<br>"
                f"교통: {row['transport_score']:.1f}<br>"
                f"의료: {row['medical_score']:.1f}<br>"
                f"인프라 위험 등급: {risk_grade}"
            ),
        ).add_to(m)

    legend_html = """
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 9999; background: white; border: 1px solid #ccc; padding: 10px; font-size: 12px;">
      <b>무차량 생활 온도</b><br>
      <span style="color:#d73027;">●</span> 80~100 매우 쉬움<br>
      <span style="color:#fc8d59;">●</span> 60~79 양호<br>
      <span style="color:#fee08b;">●</span> 40~59 보통<br>
      <span style="color:#91bfdb;">●</span> 0~39 어려움
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    return m

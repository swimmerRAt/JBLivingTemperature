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
        temp = row.get("living_score", row.get("no_car_temperature", row.get("overall_score", 50)))
        color = "#2d9d78" if temp >= 70 else "#efb34d" if temp >= 50 else "#d96161"
        risk_grade = row.get("risk_grade", _risk_grade(float(row.get("infrastructure_stability", 50))))
        district = row.get("district", "")
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=10,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
            popup=(
                f"{row['city']} {district}<br>"
                f"생활 온도: {float(temp):.1f}<br>"
                f"인구: {float(row.get('population', 0)):,.0f}<br>"
                f"청년 비율: {float(row.get('youth_ratio', 0)):.1f}%<br>"
                f"위험 등급: {risk_grade}"
            ),
        ).add_to(m)

    legend_html = """
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 9999; background: white; border: 1px solid #ccc; padding: 10px; font-size: 12px;">
      <b>생활 온도</b><br>
      <span style="color:#2d9d78;">●</span> 70~100 안정<br>
      <span style="color:#efb34d;">●</span> 50~69 관찰<br>
      <span style="color:#d96161;">●</span> 0~49 위험
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    return m

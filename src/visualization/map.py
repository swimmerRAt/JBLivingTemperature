from __future__ import annotations

import folium
import pandas as pd


def create_folium_map(df: pd.DataFrame) -> folium.Map:
    center_lat = df["lat"].mean()
    center_lon = df["lon"].mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=9, tiles="CartoDB positron")

    for _, row in df.iterrows():
        color = "green" if row["overall_score"] >= 80 else "orange" if row["overall_score"] >= 60 else "gold" if row["overall_score"] >= 40 else "red"
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=10,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
            popup=f"{row['city']} {row['district']}<br>종합 점수: {row['overall_score']:.1f}<br>교통: {row['transport_score']:.1f}<br>의료: {row['medical_score']:.1f}",
        ).add_to(m)
    return m

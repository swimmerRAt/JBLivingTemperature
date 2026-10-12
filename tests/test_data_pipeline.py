from pathlib import Path

import pandas as pd

from src.data_pipeline import (
    build_region_panel,
    discover_sources,
    load_dashboard_data,
)


def test_discover_sources_finds_checked_in_source_files():
    sources = discover_sources(Path("data"))

    assert "population" in sources
    assert "population_structure" in sources
    assert "vacancy" in sources
    assert "healthcare" in sources


def test_build_region_panel_returns_jeonbuk_city_rows_with_real_indicators():
    panel, quality = build_region_panel(Path("data"))

    assert len(panel) == 14
    assert {"city", "population", "youth_ratio", "elderly_ratio", "vacant_house_ratio"} <= set(panel.columns)
    assert panel["population"].notna().all()
    assert panel["city"].str.endswith(("시", "군")).all()
    assert quality["region_count"] == 14
    assert quality["source_count"] >= 4


def test_load_dashboard_data_keeps_missing_coordinates_explicit(tmp_path):
    panel = pd.DataFrame(
        {
            "city": ["테스트시"],
            "population": [1000],
            "youth_ratio": [12.0],
            "elderly_ratio": [20.0],
            "vacant_house_ratio": [8.0],
        }
    )
    panel.to_csv(tmp_path / "region_panel.csv", index=False)

    regions, quality = load_dashboard_data(tmp_path)

    assert regions.loc[0, "city"] == "테스트시"
    assert pd.isna(regions.loc[0, "lat"])
    assert "coordinates" in quality["warnings"]

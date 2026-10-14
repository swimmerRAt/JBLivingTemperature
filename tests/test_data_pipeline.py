import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src.data_pipeline import (
    YOUTH_AGE_END,
    YOUTH_AGE_START,
    build_region_panel,
    database_path,
    load_dashboard_data,
    suspected_transfers,
    unit_name,
)

DATA_DIR = Path("data")
needs_database = pytest.mark.skipif(not database_path(DATA_DIR).exists(), reason="source database not built")


def test_unit_name_applies_renames_merges_and_ignored_rows():
    assert unit_name("전주시", "동산동") == "여의동"
    assert unit_name("전주시", "금암2동") == "금암동"
    assert unit_name("전주시", "송천3동") == "송천1동"
    assert unit_name("익산시", "함열출장소") is None
    assert unit_name("군산시", "나운1동") == "나운1동"


def test_suspected_transfers_flags_offsetting_jumps_only():
    panel = pd.DataFrame(
        {
            "region_code": ["a", "a", "b", "b", "c", "c"],
            "city": ["가시"] * 6,
            "town": ["가동", "가동", "나동", "나동", "다동", "다동"],
            "year": [2022, 2023] * 3,
            "population": [20000, 36000, 31000, 15300, 5000, 9000],
        }
    )
    assert suspected_transfers(panel) == ["2023 가시 나동→가동 약 16,000명"]


@pytest.fixture(scope="module")
def built():
    return build_region_panel(DATA_DIR)


@needs_database
def test_region_panel_has_one_row_per_town_with_filled_indicators(built):
    region, quality, _ = built
    assert len(region) == 242 == quality["region_count"]
    assert region["region_code"].is_unique
    assert region["city"].nunique() == 14
    for column in ("population", "youth_ratio", "business_count", "bus_stop_count", "hospital_count",
                   "city_vacant_house_ratio", "city_youth_net_migration"):
        assert region[column].notna().all(), column


@needs_database
def test_transport_indicators_differ_between_towns(built):
    region, _, _ = built
    assert region["bus_stop_density"].nunique() > 100
    assert region.loc[region["city"].eq("완주군"), "bus_stop_count"].sum() > 1000


@needs_database
def test_youth_ratio_uses_ages_20_to_39(built):
    region, _, _ = built
    row = region[region["region_name"].eq("완주군 삼례읍")].iloc[0]
    with sqlite3.connect(database_path(DATA_DIR)) as connection:
        year = connection.execute("SELECT MAX(year) FROM area_year").fetchone()[0]
        total, youth = connection.execute(
            "SELECT a.population_total, SUM(p.population) FROM area_year a "
            "JOIN population_age p ON p.year = a.year AND p.admin_code = a.admin_code "
            "WHERE a.year = ? AND a.city_name = '완주군' AND a.name = '삼례읍' "
            "AND p.age_start >= ? AND p.age_end <= ?",
            (year, YOUTH_AGE_START, YOUTH_AGE_END),
        ).fetchone()
    assert row["youth_ratio"] == pytest.approx(youth / total * 100)


@needs_database
def test_town_population_sums_to_city_totals(built):
    _, _, panel = built
    with sqlite3.connect(database_path(DATA_DIR)) as connection:
        cities = pd.read_sql_query("SELECT year, city_name AS city, population_total FROM area_year "
                                   "WHERE level = 'city' AND year >= 2023", connection)
    towns = panel[panel["year"] >= 2023].groupby(["year", "city"], as_index=False)["population"].sum()
    merged = towns.merge(cities, on=["year", "city"])
    assert len(merged) == 14 * merged["year"].nunique()
    assert (merged["population"] == merged["population_total"]).all()


def test_load_dashboard_data_keeps_missing_coordinates_explicit(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    pd.DataFrame({"region_code": ["35000101"], "city": ["테스트시"], "town": ["가동"],
                  "population": [1000], "lat": [None], "lon": [None]}).to_csv(processed / "region_panel.csv", index=False)
    (processed / "data_quality.json").write_text(json.dumps({"warnings": {}}), encoding="utf-8")

    regions, quality = load_dashboard_data(tmp_path)

    assert regions.loc[0, "region_code"] == "35000101"
    assert pd.isna(regions.loc[0, "lat"])
    assert "coordinates" in quality["warnings"]


@needs_database
def test_census_population_is_boundary_consistent_where_registration_jumps(built):
    _, _, panel = built
    town = panel[(panel["city"] == "전주시") & (panel["town"] == "혁신동")].set_index("year")
    assert town.loc[2015:2024, "census_population"].notna().all()
    assert town.loc[2023, "census_population"] / town.loc[2022, "census_population"] < 1.1
    assert pd.isna(town.loc[2022, "population"])  # registration series reset at the 2023 transfer


@needs_database
def test_only_boundary_changed_towns_use_census_change(built):
    region, quality, _ = built
    census = region[region["change_5y_source"].str.startswith("등록센서스")]
    assert sorted(census["town"]) == ["여의동", "혁신동"]
    assert region["population_change_5y"].notna().all()
    assert region["youth_change_5y"].notna().all()
    assert "change_substitute" in quality["warnings"]

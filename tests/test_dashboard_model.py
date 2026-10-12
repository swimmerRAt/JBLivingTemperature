import pandas as pd

from src.dashboard_model import build_dashboard_snapshot, risk_grade, score_regions


def sample_regions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "city": ["가상시", "나눔군", "다온시"],
            "population": [100000, 50000, 30000],
            "youth_ratio": [14.0, 8.0, 11.0],
            "elderly_ratio": [16.0, 35.0, 25.0],
            "vacant_house_ratio": [5.0, 18.0, 10.0],
            "hospital_count": [80, 20, 25],
            "transport_score": [85.0, 45.0, 65.0],
            "population_change": [0.4, -2.0, -0.5],
            "lat": [35.8, 35.7, 35.6],
            "lon": [127.1, 127.2, 127.3],
        }
    )


def test_score_regions_bounds_components_and_orders_best_region_first():
    scored = score_regions(sample_regions(), {"transport": 0.4, "medical": 0.2, "vacancy": 0.2, "youth": 0.1, "stability": 0.1})

    assert scored["living_score"].between(0, 100).all()
    assert scored["risk_score"].between(0, 100).all()
    assert scored.iloc[0]["city"] == "가상시"
    assert scored.iloc[0]["contrib_transport"] > scored.iloc[1]["contrib_transport"]


def test_risk_grade_has_stable_monitor_and_risk_bands():
    assert risk_grade(80) == "안정"
    assert risk_grade(60) == "관찰"
    assert risk_grade(30) == "위험"


def test_dashboard_snapshot_contains_selected_region_drivers_and_quality():
    snapshot = build_dashboard_snapshot(
        sample_regions(),
        {"sources": {"population": "population.csv"}, "warnings": {}},
        selected_region="나눔군",
    )

    assert snapshot["selected_region"]["city"] == "나눔군"
    assert len(snapshot["selected_drivers"]) == 5
    assert len(snapshot["recommendations"]) == 3
    assert snapshot["quality"]["sources"]["population"] == "population.csv"

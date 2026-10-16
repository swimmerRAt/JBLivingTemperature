import pandas as pd

from src.dashboard_model import (
    FORECAST_NOTE,
    PREDICTION_NOTE,
    _percentile,
    build_dashboard_snapshot,
    business_stage,
    living_grade,
    score_regions,
    worst_stage,
    extinction_tier,
    youth_stage,
)


def sample_regions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "region_code": ["35000101", "35000102", "35000103"],
            "city": ["가상시", "나눔군", "다온시"],
            "town": ["가동", "나면", "다동"],
            "region_name": ["가상시 가동", "나눔군 나면", "다온시 다동"],
            "population": [30000, 2000, 12000],
            "youth_ratio": [28.0, 8.0, 18.0],
            "youth_change_5y": [5.0, -40.0, -10.0],
            "elderly_ratio": [14.0, 48.0, 25.0],
            "population_change_5y": [3.0, -20.0, -5.0],
            "city_vacant_house_ratio": [9.0, 17.0, 12.0],
            "hospital_count": [60, 1, 12],
            "business_count": [3000, 120, 900],
            "bus_stop_density": [20.0, 0.5, 4.0],
            "bus_route_count": [150, 4, 40],
            "lat": [35.8, 35.7, 35.6],
            "lon": [127.1, 127.2, 127.3],
        }
    )


def test_score_regions_bounds_components_and_orders_best_region_first():
    scored = score_regions(sample_regions())

    assert scored["living_score"].between(0, 100).all()
    assert scored.iloc[0]["region_name"] == "가상시 가동"
    assert scored.iloc[-1]["region_name"] == "나눔군 나면"
    assert scored.iloc[0]["contrib_transport"] > scored.iloc[1]["contrib_transport"]


def test_transport_weight_changes_the_ranking_when_towns_differ():
    regions = sample_regions()
    regions.loc[2, ["bus_stop_density", "bus_route_count"]] = [50.0, 400]
    transport_only = score_regions(regions, {key: 0 for key in ("medical", "business", "vacancy", "youth", "stability")})
    assert transport_only.iloc[0]["region_name"] == "다온시 다동"


def test_percentile_handles_direction_and_missing_values():
    values = pd.Series([1.0, None, 3.0, 2.0])
    assert _percentile(values).tolist()[0] == 0.0
    assert pd.isna(_percentile(values).iloc[1])
    assert _percentile(values, higher_is_better=False).iloc[0] == 100.0


def test_living_grade_splits_percentiles_into_fifths():
    assert living_grade(100) == "A"
    assert living_grade(85) == "A"
    assert living_grade(79.9) == "B"
    assert living_grade(50) == "C"
    assert living_grade(0) == "E"


def test_dashboard_snapshot_selects_by_region_code_and_states_no_prediction():
    snapshot = build_dashboard_snapshot(
        sample_regions(),
        {"sources": {"population": "주민등록 인구"}, "warnings": {}},
        selected_region="35000102",
    )

    assert snapshot["selected_region"]["region_name"] == "나눔군 나면"
    assert snapshot["selected_region"]["living_grade"] in {"A", "B", "C", "D", "E"}
    assert len(snapshot["selected_drivers"]) == 6
    assert len(snapshot["recommendations"]) == 3
    assert PREDICTION_NOTE in snapshot["notes"]
    assert "risk_grade" not in snapshot["regions"]


def test_risk_stages_use_documented_cutoffs():
    assert extinction_tier(0.19) == "소멸고위험"
    assert extinction_tier(0.2) == "소멸위험진입"
    assert extinction_tier(0.7) == "소멸주의"
    assert extinction_tier(1.2) == "보통"
    assert extinction_tier(1.5) == "소멸저위험"
    assert youth_stage(0.49) == "위험"
    assert youth_stage(0.5) == "주의"
    assert youth_stage(1.0) == "안정"
    assert business_stage(True, 0.1) == "위험"
    assert business_stage(False, 0.6) == "주의"
    assert business_stage(False, 0.2) == "안정"
    assert business_stage(None, 0.9) is None
    assert business_stage(False, 0.1, 199) == "위험"   # too few establishments to judge a 5% drop
    assert business_stage(False, 0.1, 200) == "안정"
    assert worst_stage("안정", "위험") == "위험"
    assert worst_stage(None, None) is None


def test_forecasts_drive_future_stability_and_stages():
    regions = sample_regions()
    regions["youth_forecast_change_3y"] = [-20.0, 2.0, -8.0]
    regions["extinction_risk_index_forecast"] = [0.3, 1.2, 0.8]
    regions["business_statute_meets"] = [True, False, False]
    regions["business_decline_prob_1y"] = [0.7, 0.1, 0.6]
    scored = score_regions(regions).set_index("region_name")
    assert scored.loc["나눔군 나면", "stability_component"] > scored.loc["가상시 가동", "stability_component"]
    assert scored.loc["가상시 가동", "forecast_stage"] == "위험"
    assert scored.loc["나눔군 나면", "forecast_stage"] == "위험"  # 120 establishments, below the small-town line
    assert scored.loc["다온시 다동", "forecast_stage"] == "주의"
    snapshot = build_dashboard_snapshot(regions, {"warnings": {}})
    assert FORECAST_NOTE in snapshot["notes"]


def test_missing_risk_values_do_not_break_stage_selection():
    assert worst_stage(None, float('nan')) is None
    assert worst_stage(float('nan'), '주의') == '주의'

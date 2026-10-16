import pandas as pd

from src.ui import build_forecast_rows, build_status_cards, format_metric


def test_format_metric_handles_none_and_percentages():
    assert format_metric(1234.5) == "1,234.5"
    assert format_metric(None) == "데이터 없음"
    assert format_metric(-2.3, suffix="%") == "-2.3%"


def test_build_status_cards_show_score_grade_and_forecast():
    snapshot = {
        "selected_region": {
            "region_name": "전주시 효자4동",
            "living_score": 72.4,
            "living_grade": "B",
            "youth_forecast_year": 2028,
            "youth_forecast_change_3y": -8.2,
            "youth_forecast_change_low": -15.0,
            "youth_forecast_change_high": 1.0,
            "forecast_stage": "주의",
            "youth_risk_stage": "주의",
            "business_risk_stage": "안정",
        }
    }

    cards = build_status_cards(snapshot)

    assert [card["label"] for card in cards] == ["생활 점수", "차 없이 30분 이용 시설", "3년 후 청년 인구",
                                                 "3년 감소 위험", "의료시설", "인구"]
    assert cards[0]["value"] == "72.4"
    assert "상대 등급 B" in cards[0]["detail"]
    assert cards[1]["value"] == "계산 전"
    assert cards[2]["value"] == "-8.2%"
    assert "2028년 예측" in cards[2]["detail"]
    assert cards[3]["value"] == "주의"


def test_access_card_shows_earliest_departure_counts():
    samples = pd.DataFrame([
        {"region_code": "1", "departure_time": "12:00:00", "status": "computed", "accessible_facilities_30m": 9,
         "accessible_medical_30m": 6, "accessible_pharmacy_30m": 3},
        {"region_code": "1", "departure_time": "08:00:00", "status": "computed", "accessible_facilities_30m": 5,
         "accessible_medical_30m": 3, "accessible_pharmacy_30m": 2},
        {"region_code": "2", "departure_time": "08:00:00", "status": "unconfirmed_center", "accessible_facilities_30m": None,
         "accessible_medical_30m": None, "accessible_pharmacy_30m": None},
    ])
    development = {"accessibility_samples": samples}
    card = build_status_cards({"selected_region": {"region_code": "1"}, "development": development})[1]
    assert card["value"] == "5곳"
    assert card["detail"] == "평일 08:00 출발 · 병의원 3 · 약국 2"
    card = build_status_cards({"selected_region": {"region_code": "2"}, "development": development})[1]
    assert card["value"] == "계산 불가"


def test_status_cards_say_when_no_forecast_is_connected():
    cards = build_status_cards({"selected_region": {"living_score": 50.0, "living_grade": "C"}})
    assert cards[2]["detail"] == "예측 미연결"
    assert cards[3]["value"] == "데이터 없음"
    assert cards[4]["value"] == "데이터 없음"


def test_forecast_rows_follow_the_planning_table():
    rows = build_forecast_rows({
        "youth_forecast": 920.0, "youth_forecast_change_3y": -8.0, "youth_risk_stage": "주의",
        "business_count_latest": 310, "business_statute_meets": True, "business_statute_by_peak": True,
        "business_drawdown": 0.07, "business_decline_prob_1y": 0.8, "business_decline_prob_3y": 0.6,
        "business_risk_stage": "위험", "first_risk_sector": "운수 및 창고업", "first_risk_prob_1y": 0.2, "first_risk_prob_3y": 0.3,
    })
    assert rows.columns.tolist() == ["항목", "현재", "3년 예측", "위험 단계"]
    assert rows.iloc[0]["현재"] == "1,000명"
    assert rows.iloc[3]["위험 단계"] == "위험"
    assert rows.iloc[3]["현재"] == "310개 · 해당(최고치 대비 5%↓) · 최고치 대비 -7.0%"
    assert rows.iloc[4]["현재"] == "운수 및 창고업"


def test_forecast_rows_show_extinction_index_tiers():
    rows = build_forecast_rows({"extinction_risk_index": 0.41, "extinction_risk_tier": "소멸위험진입",
                                "extinction_risk_index_forecast": 0.36, "extinction_risk_tier_forecast": "소멸위험진입",
                                "youth_risk_stage": "위험"})
    index_row = rows[rows["항목"].str.startswith("지방소멸위험지수")].iloc[0]
    assert index_row["현재"] == "0.41 (소멸위험진입)"
    assert index_row["위험 단계"] == "위험"

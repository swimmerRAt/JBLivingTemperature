from src.ui import build_status_cards, format_metric


def test_format_metric_handles_none_and_percentages():
    assert format_metric(1234.5) == "1,234.5"
    assert format_metric(None) == "데이터 없음"
    assert format_metric(-2.3, suffix="%") == "-2.3%"


def test_build_status_cards_uses_dashboard_labels_and_selected_row():
    snapshot = {
        "selected_region": {
            "city": "전주시",
            "living_score": 72.4,
            "risk_grade": "안정",
            "population": 655000,
            "population_change": -0.6,
        }
    }

    cards = build_status_cards(snapshot)

    assert [card["label"] for card in cards] == ["생활 온도", "위험 등급", "인구", "인구 증감"]
    assert cards[0]["value"] == "72.4"

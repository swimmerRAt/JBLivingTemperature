import pandas as pd

from src.report import build_report_pdf, report_font_available


def test_bundled_report_font_is_available():
    assert report_font_available()


def test_report_pdf_builds_for_sparse_town():
    snapshot = {
        "selected_region": {"region_code": "1", "region_name": "테스트군 테스트면", "living_score": 41.2,
                            "living_grade": "C", "forecast_stage": "주의"},
        "selected_drivers": [{"key": "transport", "label": "대중교통 접근성", "score": 40.0, "contribution": 1.0}],
        "regions": pd.DataFrame(),
        "recommendations": [],
        "development": {},
        "quality": {},
        "notes": ["예측은 & 기호 같은 특수문자도 <안전하게> 표시합니다."],
        "weights": {"transport": 3},
    }
    pdf = build_report_pdf(snapshot)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 10_000

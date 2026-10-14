from __future__ import annotations

import pandas as pd

# Components are percentile ranks among Jeonbuk towns; 75 means the top quarter.
STRENGTH_THRESHOLD = 75
STRENGTHS = (
    ("transport_component", "대중교통(정류장 밀도·경유 노선)"),
    ("medical_component", "의료기관 수"),
    ("business_component", "사업체 수"),
    ("vacancy_component", "주거 안정성(시군 빈집 비율)"),
    ("youth_component", "청년 기반(20~39세 비율·5년 변화)"),
    ("stability_component", "미래 안정성(3년 청년 예측·사업체 위험)"),
)


def generate_reason(row: pd.Series) -> str:
    strengths = [label for column, label in STRENGTHS if (row.get(column) or 0) >= STRENGTH_THRESHOLD]
    if not strengths:
        return "상위 25%에 드는 항목은 없지만 항목별 점수가 고르게 분포한 지역입니다."
    return f"{', '.join(strengths)} 항목이 전북 읍면동 상위 25%에 듭니다."

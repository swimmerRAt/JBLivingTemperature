"""Everyday facility categories counted in the 30-minute car-free access analysis."""
from __future__ import annotations

# category key -> (label, group). HIRA healthcare keeps its existing keys.
FACILITY_CATEGORIES: dict[str, tuple[str, str]] = {
    "hospital": ("병의원", "의료"),
    "pharmacy": ("약국", "의료"),
    "large_store": ("대형마트·백화점", "장보기"),
    "supermarket": ("슈퍼마켓", "장보기"),
    "convenience": ("편의점", "장보기"),
    "restaurant": ("음식점", "먹거리"),
    "cafe": ("카페", "먹거리"),
    "school": ("학교(초중고)", "교육·문화"),
    "library": ("도서관", "교육·문화"),
    "cinema": ("영화관", "교육·문화"),
    "senior_center": ("경로당", "복지"),
}
HEALTHCARE = ("hospital", "pharmacy")
LIVING = tuple(key for key in FACILITY_CATEGORIES if key not in HEALTHCARE)



# One line per source for captions and the PDF report (details: docs/개선기능_실행.md).
SOURCE_SUMMARY = ("병의원·약국 HIRA 2025년 3월 · 편의점·슈퍼·카페·음식점 소상공인 상가(상권)정보 2026년 6월 · "
                  "대형마트·영화관 행정안전부 인허가(영업 중) · 학교·도서관 전국 표준데이터 · "
                  "경로당 전북특별자치도 현황 2023년 7월(SGIS 지오코딩)")


def access_column(category: str) -> str:
    """Result column holding the count of a category reachable within 30 minutes."""
    return {"hospital": "accessible_medical_30m"}.get(category, f"accessible_{category}_30m")

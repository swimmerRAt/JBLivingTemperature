from __future__ import annotations

import pandas as pd


def generate_reason(row: pd.Series) -> str:
    strengths: list[str] = []

    transport = row.get("transport_component", row.get("transport_score", 0))
    medical = row.get("medical_component", row.get("medical_score", 0))
    vacancy = row.get("vacancy_component", 0)
    youth = row.get("youth_component", row.get("youth_score", 0))
    stability = row.get("stability_component", row.get("infrastructure_stability", 0))

    if transport >= 75:
        strengths.append("대중교통 접근성이 좋고")
    if medical >= 75:
        strengths.append("의료시설 접근성이 안정적이며")
    if vacancy >= 75:
        strengths.append("주거 공실 부담이 낮고")
    if youth >= 75:
        strengths.append("청년 인구 기반이 두텁고")
    if stability >= 75:
        strengths.append("인구 안정성 신호가 양호합니다")

    if not strengths:
        return "기본 점수 대비 균형 있는 생활권으로 보입니다."

    explanation = " ".join(strengths)
    if not explanation.endswith("."):
        explanation = explanation + "."
    return explanation

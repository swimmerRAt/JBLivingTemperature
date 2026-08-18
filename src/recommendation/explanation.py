from __future__ import annotations

import pandas as pd


def generate_reason(row: pd.Series) -> str:
    strengths: list[str] = []

    if row["transport_score"] >= 80:
        strengths.append("대중교통 접근성이 우수하고")
    if row["medical_score"] >= 80:
        strengths.append("의료시설 접근성이 높으며")
    if row["living_score"] >= 80:
        strengths.append("생활 인프라가 양호하고")
    if row["infrastructure_stability"] >= 80:
        strengths.append("향후 생활 인프라 유지 가능성이 높습니다")

    if not strengths:
        return "기본 점수 대비 균형 있는 생활권으로 보입니다."

    explanation = " ".join(strengths)
    if not explanation.endswith("."):
        explanation = explanation + "."
    return explanation

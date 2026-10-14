"""Train and backtest the youth and business forecasts, then write outputs and a report.

1. Cohort-residual youth net migration, checked against KOSIS city migration
2. Business decline risk by town and industry section (1 and 3 years)
3. Youth population forecast (3 years): Hamilton-Perry baseline vs LightGBM, SHAP
4. Rebuild the dashboard panel so it carries the forecasts

Outputs go to data/processed/ (CSV), models/model_report.json and docs/model_report.md.
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dashboard_model import (
    BUSINESS_WATCH_PROBABILITY,
    EXTINCTION_TIERS,
    SMALL_TOWN_ESTABLISHMENTS,
    EXTINCTION_TOP_TIER,
    business_stage,
    extinction_tier,
    worst_stage,
    youth_stage,
)
from src.data_pipeline import write_processed
from src.forecast import business, youth
from src.forecast.data import load_inputs

DATA_DIR = ROOT / "data"
PROCESSED = DATA_DIR / "processed"
REPORT_PATH = ROOT / "docs" / "model_report.md"
METRICS_PATH = ROOT / "models" / "model_report.json"
METHOD_LABELS = {
    "no_change": "변화 없음", "trend_3y": "최근 3년 추세 유지", "aging_only": "이동 없음(연령 이동만)",
    "hamilton_perry": "해밀턴-페리 코호트 추계", "lgbm_direct": "LightGBM", "lgbm_residual": "LightGBM(기준선 보정형)",
    "sector_rate": "업종 평균 감소율", "past_decline": "과거 감소 지속", "logistic": "로지스틱 회귀",
    "lgbm": "LightGBM", "ensemble": "LightGBM+로지스틱(보정 전)", "calibrated": "LightGBM+로지스틱(확률 보정)", "current_status": "현재 상태 유지",
}


def markdown_table(frame: pd.DataFrame, digits: int = 2) -> str:
    def cell(value: object) -> str:
        if isinstance(value, (float, np.floating)):
            return "" if np.isnan(value) else f"{value:,.{digits}f}"
        if isinstance(value, (int, np.integer)):
            return f"{value:,}"
        return str(value)
    header = "| " + " | ".join(map(str, frame.columns)) + " |"
    rule = "| " + " | ".join("---" for _ in frame.columns) + " |"
    body = ["| " + " | ".join(cell(value) for value in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join([header, rule, *body])


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def run_youth_migration(inputs) -> dict:
    check = youth.migration_check(inputs)
    latest = int(inputs.bands.index.get_level_values("year").max())
    estimate = youth.cohort_net_migration(inputs, latest - 5, latest).rename("net_migration")
    base = youth.no_migration_youth(youth.at_year(inputs.bands, latest - 5), 5)
    towns = inputs.units.set_index("region_code")[["city", "town"]].join(estimate)
    towns["net_migration_rate_pct"] = estimate / base * 100
    history = []
    years = sorted(inputs.bands.index.get_level_values("year").unique())
    for end in range(youth.FIRST_YEAR + 5, years[-1] + 1):
        values = youth.cohort_net_migration(inputs, end - 5, end)
        history.append(pd.DataFrame({"region_code": values.index, "start_year": end - 5, "end_year": end,
                                     "youth_net_migration": values.to_numpy()}))
    pd.concat(history).to_csv(PROCESSED / "youth_net_migration.csv", index=False)
    return {
        "check": check,
        "correlation": float(check["estimated"].corr(check["observed"])),
        "ratio": float(check["estimated"].sum() / check["observed"].sum()),
        "towns": towns.reset_index(),
        "window": (latest - 5, latest),
    }


def _business_models(history) -> dict:
    datasets, backtests, scores, calibrations = {}, {}, {}, {}
    for horizon in business.HORIZONS:
        datasets[horizon] = business.build_dataset(history, horizon)
        backtests[horizon] = business.add_calibrated(business.backtest(datasets[horizon], horizon))
        scores[horizon] = business.score(backtests[horizon])
        calibrations[horizon] = business.calibration(backtests[horizon])
        log(f"business {'total' if history.total else 'sector'} {horizon}y: {len(datasets[horizon]):,} rows")
    forecast, importance = business.forecast(history, datasets, backtests)
    return {"datasets": datasets, "scores": scores, "calibrations": calibrations, "forecast": forecast,
            "importance": importance}


def run_business(inputs) -> dict:
    names = inputs.units.set_index("region_code")[["city", "town"]]
    total = _business_models(business.SectorHistory(inputs, total=True))
    town = total["forecast"].rename(columns={
        "count": "establishments", "statute_drawdown": "business_drawdown", "statute_decline_run": "business_decline_run",
        "statute_by_peak": "business_statute_by_peak", "statute_by_run": "business_statute_by_run",
        "statute_meets": "business_statute_meets", "decline_prob_1y": "business_decline_prob_1y",
        "decline_prob_3y": "business_decline_prob_3y", "drivers": "business_drivers",
    }).drop(columns=["industry_code", "industry_name"])
    sector = _business_models(business.SectorHistory(inputs))
    town = town.merge(business.town_summary(sector["forecast"]), on="region_code", how="left")
    ranking = business.province_ranking(sector["forecast"])
    sector["forecast"].join(names, on="region_code").to_csv(PROCESSED / "forecast_business_sector.csv", index=False)
    town = town.join(names, on="region_code")
    town.to_csv(PROCESSED / "forecast_business_town.csv", index=False)
    ranking.to_csv(PROCESSED / "business_sector_ranking.csv", index=False)
    return {"total": total, "sector": sector, "town": town, "ranking": ranking}


def run_youth_forecast(inputs) -> dict:
    data = youth.build_dataset(inputs)
    results = youth.backtest(data)
    scores = youth.score(results)
    method = scores["weighted_mae_pp"].idxmin()
    sizes = pd.qcut(results["youth"], 3, labels=["소규모", "중규모", "대규모"])
    by_size = pd.DataFrame({
        name: results.groupby(sizes, observed=True).apply(
            lambda group, name=name: (np.expm1(group[name]) * 100 - np.expm1(group["target"]) * 100).abs().mean())
        for name in ("hamilton_perry", method)
    })
    edges, widths, coverage = youth.conformal_widths(results, method)
    result, importance = youth.forecast(inputs, data, method, widths, edges)
    names = inputs.units.set_index("region_code")[["city", "town"]]
    result = result.join(names, on="region_code")
    result["prediction_method"] = METHOD_LABELS[method]
    result.to_csv(PROCESSED / "forecast_youth.csv", index=False)
    log(f"youth: {len(data):,} rows, chosen {method}, interval coverage {coverage:.2f}")
    return {"rows": len(data), "scores": scores, "method": method, "by_size": by_size, "coverage": coverage,
            "result": result, "importance": importance, "origins": sorted(results["origin"].unique())}


def stage_table(youth_forecast: pd.DataFrame, business_town: pd.DataFrame) -> str:
    stages = youth_forecast[["region_code", "extinction_risk_index_forecast"]].merge(
        business_town[["region_code", "business_statute_meets", "business_decline_prob_1y", "establishments"]],
        on="region_code", how="outer")
    stages["청년"] = stages["extinction_risk_index_forecast"].map(youth_stage)
    stages["사업체"] = [business_stage(meets, probability, count) for meets, probability, count
                     in zip(stages["business_statute_meets"], stages["business_decline_prob_1y"], stages["establishments"])]
    stages["종합"] = [worst_stage(a, b) for a, b in zip(stages["청년"], stages["사업체"])]
    counts = pd.DataFrame({column: stages[column].value_counts() for column in ("청년", "사업체", "종합")})
    counts = counts.reindex(["안정", "주의", "위험"]).fillna(0).astype(int).reset_index().rename(columns={"index": "단계"})
    return markdown_table(counts)


def tier_table(youth_forecast: pd.DataFrame, origin: int, target: int) -> str:
    order = [label for _, label in EXTINCTION_TIERS] + [EXTINCTION_TOP_TIER]
    counts = pd.DataFrame({
        f"{origin}년 (실측)": youth_forecast["extinction_risk_index"].map(extinction_tier).value_counts(),
        f"{target}년 (예측)": youth_forecast["extinction_risk_index_forecast"].map(extinction_tier).value_counts(),
    }).reindex(order).fillna(0).astype(int).reset_index().rename(columns={"index": "단계"})
    return markdown_table(counts)


def elderly_check(inputs) -> tuple[float, float]:
    """Hamilton-Perry 65+ projection over the latest five years: province error and median town error (%)."""
    latest = int(inputs.bands.index.get_level_values("year").max())
    projected = youth.hamilton_perry_elderly(inputs, latest - 5, 5)
    actual = youth.at_year(inputs.bands, latest)[list(youth.ELDERLY_BANDS)].sum(axis=1)
    known = projected.notna() & actual.notna()
    return (float(projected[known].sum() / actual[known].sum() * 100 - 100),
            float(((projected[known] / actual[known] - 1).abs() * 100).median()))


def score_table(scores: pd.DataFrame) -> str:
    table = scores.copy()
    table["method"] = table["method"].map(METHOD_LABELS)
    return markdown_table(table.rename(columns={"frame": "구간", "method": "방법", "rows": "표본", "event_rate": "해당 비율",
                                                "roc_auc": "ROC-AUC", "pr_auc": "PR-AUC", "brier": "Brier"}), 3)


def write_report(migration: dict, business_result: dict, youth_result: dict, elapsed: float,
                 elderly_error: tuple[float, float]) -> None:
    yr = youth_result["result"]
    origin, target = int(yr["origin_year"].iloc[0]), int(yr["target_year"].iloc[0])
    total_now, total_next = yr["youth"].sum(), yr["youth_forecast"].sum()
    labels = {**youth.FEATURE_LABELS, "hp_growth_base": "코호트 추계 기준선"}
    blab = business.FEATURE_LABELS
    town = business_result["town"]
    origin_business = int(town["origin_year"].iloc[0])
    lines = [
        "# 예측 모델 결과 보고서",
        "",
        f"생성: {datetime.now():%Y-%m-%d %H:%M} · 실행 시간 {elapsed / 60:.1f}분 · 재실행: `.venv/bin/python scripts/train_models.py`",
        "",
        "## 요약",
        "",
        f"- **청년(20~39세) 인구 {target}년 예측**: 전북 {total_now:,.0f}명({origin}년) → **{total_next:,.0f}명** "
        f"({(total_next / total_now - 1) * 100:+.1f}%). 3년간 예상 청년 순이동 **{yr['net_migration_forecast'].sum():+,.0f}명**.",
        f"- 청년 예측 모델: **{METHOD_LABELS[youth_result['method']]}** (백테스트 인구가중 오차 "
        f"{youth_result['scores'].loc[youth_result['method'], 'weighted_mae_pp']:.1f}%p, 해밀턴-페리 기준선 "
        f"{youth_result['scores'].loc['hamilton_perry', 'weighted_mae_pp']:.1f}%p).",
        f"- 청년 순이동 추정(코호트 잔차법)은 KOSIS 시군 실제 순이동과 상관 {migration['correlation']:.3f}, 합계 비율 {migration['ratio']:.2f}로 일치합니다.",
        f"- **사업체 쇠퇴(도시재생법 기준)**: {origin_business}년 현재 {int(town['business_statute_meets'].sum())}곳이 해당하고,"
        f" 1년 안에 새로 해당할 확률이 50% 이상인 곳은 "
        f"{int((town['business_statute_meets'].eq(False) & (town['business_decline_prob_1y'] >= 0.5)).sum())}곳입니다.",
        "- 사업체 예측은 1년 내·3년 내(그 기간 중 한 번이라도 기준 해당) 모두 '현재 상태 유지' 기준선보다 낫습니다."
        f" 사업체 {SMALL_TOWN_ESTABLISHMENTS}개 미만 읍면동 {int((town['establishments'] < SMALL_TOWN_ESTABLISHMENTS).sum())}곳은"
        " 기준 해당 비율이 큰 곳의 약 2배라 **위험** 단계로 둡니다.",
        f"- **먼저 쇠퇴 기준에 들어갈 업종(전북 가중 평균, 1년)**: "
        + ", ".join(f"{row.industry_name}({row.decline_prob_1y:.0%})" for row in business_result["ranking"].head(3).itertuples()) + ".",
        "",
        "## 1. 청년 순이동 추정 (코호트 잔차법)",
        "",
        "읍면동 순이동은 직접 공표되지 않아, 5년 전 15~34세 인구와 올해 20~39세 인구의 차이로 추정했습니다"
        " (40세 미만 사망은 무시, 5년간 약 0.3% 과소 추정).",
        "",
        f"- 검증: 읍면동 추정치를 시군별로 합해 KOSIS 20~39세 순이동 5년 합계와 비교 ({len(migration['check'])}개 시군·기간)."
        f" 상관 **{migration['correlation']:.3f}**, 추정/실제 합계 **{migration['ratio']:.2f}**. 경계 조정으로 비교 단위가 끊긴 전주시는 제외됩니다.",
        "",
        f"### {migration['window'][0]}→{migration['window'][1]}년 청년 순유출이 큰 읍면동 (명)",
        "",
        markdown_table(migration["towns"].dropna(subset=["net_migration"]).nsmallest(10, "net_migration")
                       [["city", "town", "net_migration", "net_migration_rate_pct"]]
                       .rename(columns={"city": "시군", "town": "읍면동", "net_migration": "순이동(명)",
                                        "net_migration_rate_pct": "순이동률(%)"}), 1),
        "",
        "## 2. 사업체 쇠퇴 (도시재생법 기준)",
        "",
        "「도시재생 활성화 및 지원에 관한 특별법 시행령」 제17조의 산업·경제 쇠퇴 기준을 씁니다: ① 최근 사업체 수 최고치 대비"
        f" {business.STATUTE_DROP:.0%} 이상 감소, 또는 ② 최근 5년간 {business.STATUTE_CONSECUTIVE}년 이상 연속 감소."
        f" 법은 '최근 10년' 최고치를 보지만 2019→2020년 조사기준 변경 때문에 **최근 {business.STATUTE_WINDOW}년**으로 줄여 적용했습니다.",
        "",
        "### 2.1 전체 사업체 (읍면동 단위, 법령 적용 범위)",
        "",
        f"- {origin_business}년 현재 기준 해당: **{int(town['business_statute_meets'].sum())}곳** / 242곳"
        f" (최고치 대비 5%↓ {int(town['business_statute_by_peak'].sum())}곳, 3년 연속 감소 {int(town['business_statute_by_run'].sum())}곳)",
        "",
    ]
    for horizon in business.HORIZONS:
        lines += [f"{horizon}년 안에 한 번이라도 기준 해당 여부 예측 백테스트", "", score_table(business_result["total"]["scores"][horizon]), ""]
    lines += [
        "- 3년 목표는 'origin+1~+3년 중 한 번이라도 해당'입니다. 이전의 '3년 뒤 그 해 해당'은 5년 판정 창이 3년 사이 대부분 바뀌어"
        " (관측된 해 2년만 겹침) 현재 상태가 거의 정보를 주지 못했고 ROC-AUC 약 0.6으로 기준선보다 낫지 않았습니다.",
        "- 3년 내 예측은 2020년 조사기준 이후 검증 표본이 없습니다(판정 창 3개가 모두 2020년 이후에 들어오는 첫 기준연도는 2024년).",
        "",
        "### 법령 기준에 새로 해당할 확률(1년)이 높은 읍면동 (현재 미해당)",
        "",
        markdown_table(town[town["business_statute_meets"].eq(False)].nlargest(10, "business_decline_prob_1y")
                       [["city", "town", "establishments", "business_drawdown", "business_decline_prob_1y", "business_decline_prob_3y", "business_drivers"]]
                       .rename(columns={"city": "시군", "town": "읍면동", "establishments": "사업체 수", "business_drawdown": "최고치 대비 감소폭",
                                        "business_decline_prob_1y": "1년 내 해당 확률", "business_decline_prob_3y": "3년 내 확률",
                                        "business_drivers": "주요 요인(SHAP)"}), 3),
        "",
        "### 2.2 업종별 (먼저 쇠퇴 기준에 들어갈 업종)",
        "",
        f"읍면동×업종(KSIC 대분류, 사업체 {business.MIN_ESTABLISHMENTS}개 이상)에 같은 법령 기준을 적용했습니다."
        " 작은 업종은 한두 곳만 문을 닫아도 5% 기준에 걸리므로 확률은 **상대 순위**로 보세요.",
        "",
    ]
    for horizon in business.HORIZONS:
        lines += [f"{horizon}년 업종별 백테스트", "", score_table(business_result["sector"]["scores"][horizon]), ""]
    ranking = business_result["ranking"].head(10)[["industry_name", "towns", "establishments", "decline_prob_1y",
                                                   "decline_prob_3y", "high_risk_towns_3y"]]
    lines += [
        f"### 전북에서 먼저 쇠퇴 기준에 들어갈 업종 (사업체 수 가중 평균, {business.RANKING_MIN_TOWNS}개 이상 읍면동에 있는 업종)",
        "",
        markdown_table(ranking.rename(columns={"industry_name": "업종", "towns": "읍면동 수", "establishments": "사업체 수",
                                               "decline_prob_1y": "1년 내 확률", "decline_prob_3y": "3년 내 확률",
                                               "high_risk_towns_3y": f"3년 확률 {business.HIGH_RISK_3Y:.0%}↑ 읍면동"}), 3),
        "",
        "### 사업체 예측에 크게 작용한 요인 (SHAP 평균 절댓값, 전체 사업체 1년 모델)",
        "",
        markdown_table(business_result["total"]["importance"].head(10).rename(lambda name: blab.get(name, name))
                       .rename("영향력").reset_index().rename(columns={"index": "요인"}), 3),
        "",
    ]
    lines += [
        "## 3. 청년 인구 3년 예측",
        "",
        f"{len(youth_result['origins'])}개 기준연도({youth_result['origins'][0]}~{youth_result['origins'][-1]})에 대해, "
        "그 시점까지 결과가 알려진 자료로만 학습해 3년 뒤를 예측했습니다. 선택 기준은 인구가중 평균 절대오차(%p)입니다.",
        "",
        markdown_table(youth_result["scores"].rename(index=METHOD_LABELS).reset_index().rename(columns={
            "method": "방법", "mae_pp": "평균 오차(%p)", "median_ae_pp": "중앙 오차(%p)", "weighted_mae_pp": "인구가중 오차(%p)",
            "youth_mae_people": "평균 오차(명)", "province_error_pct": "전북 합계 오차(%)",
            "province_origin_abs_error_pct": "연도별 전북 합계 오차(절댓값 평균, %)"}), 2),
        "",
        "지역 규모별 평균 오차(%p)",
        "",
        markdown_table(youth_result["by_size"].rename(columns=METHOD_LABELS).reset_index().rename(columns={"youth": "청년 인구 규모"}), 1),
        "",
        f"- 80% 예측구간(규모별 컨포멀)의 기준연도 교차 검증 적중률: **{youth_result['coverage']:.0%}**.",
        "",
        f"### {target}년 청년 인구 감소율이 큰 읍면동",
        "",
        markdown_table(yr.nsmallest(10, "youth_change_pct")[["city", "town", "youth", "youth_forecast", "youth_change_pct",
                                                             "youth_change_low_pct", "youth_change_high_pct", "drivers"]]
                       .astype({"youth": int})
                       .rename(columns={"city": "시군", "town": "읍면동", "youth": f"{origin}년", "youth_forecast": f"{target}년 예측",
                                        "youth_change_pct": "변화율(%)", "youth_change_low_pct": "하한(%)",
                                        "youth_change_high_pct": "상한(%)", "drivers": "주요 요인(SHAP)"}), 1),
        "",
        f"### 시군별 {origin}→{target}년 청년 예측",
        "",
        markdown_table(yr.groupby("city").agg(youth=("youth", "sum"), forecast=("youth_forecast", "sum"),
                                              migration=("net_migration_forecast", "sum"))
                       .assign(change=lambda frame: (frame["forecast"] / frame["youth"] - 1) * 100)
                       .sort_values("change").reset_index()
                       .rename(columns={"city": "시군", "youth": f"{origin}년", "forecast": f"{target}년 예측",
                                        "migration": "예상 순이동(명)", "change": "변화율(%)"}), 1),
        "",
        "### 청년 예측에 크게 작용한 요인 (SHAP 평균 절댓값)",
        "",
        markdown_table(youth_result["importance"].head(10).rename(lambda name: labels.get(name, name))
                       .rename("영향력(로그 변화)").reset_index().rename(columns={"index": "요인"}), 3),
        "",
        "## 4. 대시보드의 3년 감소 위험 단계",
        "",
        "- 청년 인구: **지방소멸위험지수**(한국고용정보원, 20~39세 여성 ÷ 65세 이상)를 그대로 씁니다. 원 보고서의 5단계"
        " (0.2 미만 소멸고위험, 0.2~0.5 소멸위험진입, 0.5~1.0 소멸주의, 1.0~1.5 보통, 1.5 이상 소멸저위험) 중"
        " 0.5 미만(소멸위험지역)을 **위험**, 0.5~1.0을 **주의**, 1.0 이상을 **안정**으로 표시합니다.",
        f"- {target}년 지수는 20~39세 여성이 청년 예측 변화율만큼 변하고(가정), 65세 이상은 해밀턴-페리 코호트 추계로 계산했습니다."
        f" 65세 이상 추계를 최근 5년에 적용해 본 결과 전북 합계 오차 {elderly_error[0]:+.2f}%, 읍면동 중앙 오차 {elderly_error[1]:.1f}%였습니다.",
        "- 지수 기준값 자체(특히 0.5)는 학술적으로 근거가 부족하다는 비판이 있습니다(정성호 2019, 「지역사회학」 20(3)).",
        "",
        tier_table(yr, origin, target),
        "",
        "- 사업체: 전체 사업체가 도시재생법 산업 쇠퇴 기준에 이미 해당하거나 사업체가"
        f" {SMALL_TOWN_ESTABLISHMENTS}개 미만이면 **위험**, 아직 아니지만 1년 안에 해당할 확률이"
        f" {BUSINESS_WATCH_PROBABILITY:.0%} 이상이면 **주의**, 그 외 **안정**."
        f" {SMALL_TOWN_ESTABLISHMENTS}개 미만은 몇 곳만 문을 닫아도 5% 기준을 넘어, 기준 해당 비율이 큰 곳의 약 2배"
        "(1년 30% 대 17%, 3년 내 57% 대 29%)이고 모델도 '현재 상태 유지'보다 거의 낫지 않아(1년 ROC-AUC 0.79 대 0.78)"
        " 사업체 기반 자체가 취약한 곳으로 봅니다.",
        "- 종합 단계는 둘 중 더 나쁜 단계입니다.",
        "",
        stage_table(yr, business_result["town"]),
        "",
        "## 5. 해석 시 주의",
        "",
        "- SHAP 요인은 **예측에 대한 기여도**이지 인과 효과가 아닙니다. 정책 효과 판단에는 별도의 인과 분석이 필요합니다.",
        "- 청년 인구는 주민등록(외국인 제외) 기준입니다. "
        + (", ".join(yr.loc[yr["forecast_note"].ne(""), "town"]) or "해당 없음")
        + "은 경계 조정으로 과거 추세 변수가 비어 있어 예측을 신뢰하기 어렵습니다(화면에 표시).",
        "- 순창읍처럼 최근 청년이 늘어난 곳은 추세가 이어진다고 예측합니다. 15~19세가 많은 곳(학교 기숙사 등)은 실제로는 졸업 후 유출될 수 있습니다.",
        "- 소규모 읍면동은 LightGBM이 기준선보다 낫지 않을 수 있습니다(위 규모별 표 참고). 예측구간을 함께 봐야 합니다.",
        "- LightGBM은 최근 기준연도일수록 전북 합계를 낮게 예측했습니다(백테스트 마지막 기준연도 약 -3~-4%). 손실함수·하이퍼파라미터와"
        " 무관하게 나타나는 경향이라, 전북 합계 전망은 비관 쪽으로 치우쳤을 수 있습니다. 같은 기간 해밀턴-페리 전망은 "
        f"{((yr['youth'] * (1 + yr['hamilton_perry_change_pct'] / 100)).sum() / total_now - 1) * 100:+.1f}%입니다.",
        "- 사업체 3년 내 확률은 기간이 길어 기본 해당 비율 자체가 높습니다(전체 약 40%, 업종 약 65%). 절대값보다 순위로 보세요.",
        "- 버스 정류장·노선은 과거 이력이 없어 예측 입력에서 제외했고, 의료기관은 시군 단위 집계만 반영했습니다.",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    warnings.filterwarnings("ignore", category=UserWarning)
    started = time.time()
    log("loading inputs")
    inputs = load_inputs(DATA_DIR)
    log("step 1: youth net migration")
    migration = run_youth_migration(inputs)
    log(f"step 1 done: correlation {migration['correlation']:.3f}, ratio {migration['ratio']:.2f}")
    log("step 2: business decline risk")
    business_result = run_business(inputs)
    log("step 3: youth forecast")
    youth_result = run_youth_forecast(inputs)
    elapsed = time.time() - started
    METRICS_PATH.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "youth_migration_check": {"correlation": migration["correlation"], "ratio": migration["ratio"],
                                  "rows": len(migration["check"])},
        "youth_forecast": {"method": youth_result["method"], "rows": youth_result["rows"],
                           "interval_coverage_80": youth_result["coverage"],
                           "backtest": youth_result["scores"].round(4).to_dict(orient="index")},
        "business_total": {f"{horizon}y": business_result["total"]["scores"][horizon].round(4).to_dict(orient="records")
                           for horizon in business.HORIZONS},
        "business_sector": {f"{horizon}y": business_result["sector"]["scores"][horizon].round(4).to_dict(orient="records")
                            for horizon in business.HORIZONS},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(migration, business_result, youth_result, elapsed, elderly_check(inputs))
    log(f"report: {REPORT_PATH}")
    log("step 4: dashboard panel")
    region, _ = write_processed(DATA_DIR)
    log(f"done: {len(region)} towns, {elapsed / 60:.1f} min for models")


if __name__ == "__main__":
    main()

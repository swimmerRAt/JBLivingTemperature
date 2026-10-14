"""Business decline under the statutory urban-regeneration criterion.

도시재생 활성화 및 지원에 관한 특별법 시행령 제17조 calls a town's industry in
decline when its establishments fell 5% or more below the highest count of the
last ten years, or fell in three or more consecutive years within the last five.
The establishment survey frame changed between 2019 and 2020, so the peak window
is shortened to five years to stay inside one frame. The label for origin t and
horizon h is whether the criterion holds in any year t + 1 .. t + h. It is applied to each
town's total (the statutory scope) and to each town x industry section with at
least MIN_ESTABLISHMENTS establishments, whose 1- and 3-year probabilities give
the order in which sections are expected to fall into decline. Only KSIC 9+
years (2006 on) are used so that section letters mean the same industry.

The 3-year label used to be "meets the criterion in t + 3" alone. A five-year
window shares only two observed years with the window three years on, so the
peak behind today's status has dropped out by then and that label ranked barely
better than chance (ROC-AUC about 0.6, below persistence). "Any year within h"
keeps the nearest window, t - 3 .. t + 1, in play; the window_* features describe
its observed part. Probabilities
are the Platt-calibrated mean of LightGBM and logistic regression, which ranked
better than either alone in the backtest (the logistic model wins on the small
town-total dataset, LightGBM on the town x section one).
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.data_pipeline import BUSINESS_SERIES_START
from src.forecast.data import ForecastInputs, log_ratio, same_business_frame
from src.forecast.youth import FEATURE_LABELS as TOWN_LABELS
from src.forecast.youth import town_features

HORIZONS = (1, 3)
MIN_ESTABLISHMENTS = 10
STATUTE_DROP = 0.05        # 5% below the recent peak
STATUTE_CONSECUTIVE = 3    # three or more consecutive annual declines
STATUTE_WINDOW = 5         # law: 10-year peak; shortened to stay inside one survey frame
FIRST_YEAR = 2006
TOTAL_CODE = "ALL"
TOWN_FIRST_YEAR = 2008  # resident-registration bands start in 2008
# Sections residents use day to day; their weighted risk summarises a town.
LIVING_SERVICE_SECTIONS = ("G", "I", "P", "Q", "S")
TOWN_COLUMNS = ["log_population", "youth_ratio", "elderly_ratio", "population_growth_3y", "youth_growth_3y",
                "net_migration_rate_5y", "log_establishments", "establishment_growth_3y", "city_vacant_house_ratio",
                "city_youth_net_migration_rate", "log_density", "city", "town_type"]
CATEGORICAL = ["industry_code", "city", "town_type"]
# Shallow, heavily pooled trees: deeper settings ranked no better in the backtest
# and gave overconfident probabilities (top decile 53% predicted vs 30% observed).
LGBM_PARAMS = dict(n_estimators=200, learning_rate=0.03, num_leaves=7, min_child_samples=200, subsample=0.8,
                   subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=7, verbose=-1)
FEATURE_LABELS = {
    **TOWN_LABELS, "industry_code": "업종", "log_count": "업종 사업체 규모", "town_share": "읍면동 내 업종 비중",
    "growth_1y": "최근 1년 업종 증감", "growth_3y": "최근 3년 업종 증감", "employees_per_establishment": "사업체당 종사자",
    "province_sector_growth_1y": "전북 전체 업종 증감(1년)", "new_business_frame": "사업체 조사기준(2020~)",
    "drawdown_now": "최근 최고치 대비 감소폭", "decline_run_now": "연속 감소 연수", "meets_now": "현재 법령 쇠퇴 기준 해당",
    "window_drawdown": "판정 기간 내 최고치 대비 감소폭", "closures_to_criterion": "기준 도달까지 남은 감소 사업체 수",
    "window_decline_run": "판정 기간 내 최장 연속 감소", "trailing_decline_run": "현재 이어지는 연속 감소 연수",
    "city_sector_growth_1y": "시군 동일 업종 증감(1년)",
}


def decline_runs(window: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Longest run of year-on-year declines in each row, and the run still going in the last year."""
    falling = (window.diff(axis=1).iloc[:, 1:] < 0).to_numpy()
    longest = np.zeros(len(window), dtype=int)
    current = np.zeros(len(window), dtype=int)
    for column in range(falling.shape[1]):
        current = np.where(falling[:, column], current + 1, 0)
        longest = np.maximum(longest, current)
    return longest, current


def statutory_status(counts: pd.DataFrame, end: int) -> pd.DataFrame | None:
    """Drawdown from the window peak, longest run of declines, and whether the criterion holds in `end`."""
    start = end - STATUTE_WINDOW + 1
    if start < FIRST_YEAR or not same_business_frame(start, end) or end not in counts:
        return None
    window = counts[list(range(start, end + 1))]
    peak = window.max(axis=1)
    drawdown = 1 - window[end] / peak.where(peak > 0)
    status = pd.DataFrame({"drawdown": drawdown, "decline_run": decline_runs(window)[0]}, index=counts.index)
    status["by_peak"] = status["drawdown"] >= STATUTE_DROP
    status["by_run"] = status["decline_run"] >= STATUTE_CONSECUTIVE
    status["meets"] = status["by_peak"] | status["by_run"]
    return status


def sector_table(inputs: ForecastInputs) -> pd.DataFrame:
    """Establishments by (town, section) and year. SGIS omits a section with no
    establishment, so a missing year inside the series means zero."""
    wide = inputs.sectors.pivot_table(index=["region_code", "industry_code"], columns="year", values="establishments")
    return wide.fillna(0)


class SectorHistory:
    """Establishment series by (town, section), or by town total when `total` is set."""

    def __init__(self, inputs: ForecastInputs, total: bool = False):
        self.inputs = inputs
        self.total = total
        self.totals = inputs.panel.pivot_table(index="region_code", columns="year", values="establishments")
        if total:
            def by_town(column: str) -> pd.DataFrame:
                wide = inputs.panel.pivot_table(index="region_code", columns="year", values=column)
                wide.index = pd.MultiIndex.from_arrays([wide.index, [TOTAL_CODE] * len(wide)],
                                                       names=["region_code", "industry_code"])
                return wide
            self.counts = by_town("establishments").loc[:, FIRST_YEAR:]
            self.employees = by_town("employees_known")
            self.sections = [TOTAL_CODE]
        else:
            self.counts = sector_table(inputs)
            self.employees = inputs.sectors.pivot_table(index=["region_code", "industry_code"], columns="year",
                                                        values="employees_known")
            self.sections = sorted(inputs.sectors["industry_code"].unique())
        self.province = self.counts.groupby(level="industry_code").sum()
        self.last_year = int(self.counts.columns.max())
        self._towns: dict[int, pd.DataFrame] = {}

    def town_columns(self, origin: int) -> pd.DataFrame:
        if origin not in self._towns:
            if origin >= TOWN_FIRST_YEAR:
                self._towns[origin] = town_features(self.inputs, origin).reindex(columns=TOWN_COLUMNS)
            else:
                units = self.inputs.units
                towns = pd.DataFrame(index=units["region_code"], columns=TOWN_COLUMNS, dtype=float)
                towns["city"] = pd.Categorical(units["city"], categories=sorted(units["city"].unique()))
                towns["town_type"] = pd.Categorical(units["town_type"], categories=["동", "읍", "면"])
                self._towns[origin] = towns
        return self._towns[origin]

    def window_columns(self, origin: int) -> pd.DataFrame:
        """The observed part of the statutory window of `origin + 1`, the first year every label looks at."""
        counts, now = self.counts, self.counts[origin]

        def observed(first: int) -> pd.DataFrame:
            return counts[[year for year in range(max(first, FIRST_YEAR), origin + 1) if same_business_frame(year, origin)]]

        target = observed(origin + 2 - STATUTE_WINDOW)
        peak = target.max(axis=1)
        frame = pd.DataFrame(index=counts.index)
        frame["window_drawdown"] = 1 - now / peak.where(peak > 0)
        frame["closures_to_criterion"] = now - np.floor(peak * (1 - STATUTE_DROP))
        frame["window_decline_run"] = decline_runs(target)[0]
        frame["trailing_decline_run"] = decline_runs(observed(origin - STATUTE_WINDOW + 1))[1]
        if origin - 1 >= FIRST_YEAR and same_business_frame(origin - 1, origin):
            city = self.inputs.units.set_index("region_code")["city"].reindex(counts.index.get_level_values("region_code"))
            keys = [city.to_numpy(), counts.index.get_level_values("industry_code")]
            pooled = counts[[origin - 1, origin]].groupby(keys).sum()
            growth = log_ratio(pooled[origin], pooled[origin - 1])
            frame["city_sector_growth_1y"] = growth.reindex(pd.MultiIndex.from_arrays(keys)).to_numpy()
        return frame

    def origin_rows(self, origin: int) -> pd.DataFrame:
        """Features of every (town, section) with enough establishments in `origin`."""
        base = self.counts[origin]
        frame = pd.DataFrame({"count": base, "log_count": np.log(base.where(base > 0))}, index=self.counts.index)
        frame["town_share"] = base / self.totals[origin].reindex(base.index.get_level_values("region_code")).to_numpy()
        for years in (1, 3):
            start = origin - years
            if start >= FIRST_YEAR and same_business_frame(start, origin):
                frame[f"growth_{years}y"] = log_ratio(base, self.counts[start])
        frame["employees_per_establishment"] = self.employees[origin] / base
        status = statutory_status(self.counts, origin)
        if status is not None:
            frame["drawdown_now"] = status["drawdown"]
            frame["decline_run_now"] = status["decline_run"]
            frame["meets_now"] = status["meets"].astype(int)
        if origin - 1 >= FIRST_YEAR and same_business_frame(origin - 1, origin):
            growth = log_ratio(self.province[origin], self.province[origin - 1])
            frame["province_sector_growth_1y"] = growth.reindex(frame.index.get_level_values("industry_code")).to_numpy()
        frame = frame.join(self.window_columns(origin))
        frame = frame.join(self.town_columns(origin), on="region_code")
        frame["new_business_frame"] = int(origin >= BUSINESS_SERIES_START)
        frame["origin"] = origin
        frame = frame[frame["count"] >= MIN_ESTABLISHMENTS].reset_index()
        frame["industry_code"] = pd.Categorical(frame["industry_code"], categories=self.sections)
        return frame


def build_dataset(history: SectorHistory, horizon: int) -> pd.DataFrame:
    """Rows whose outcome years `origin + 1 .. origin + horizon` all have a statutory window inside one survey frame."""
    rows = []
    for origin in range(FIRST_YEAR, history.last_year - horizon + 1):
        statuses = [statutory_status(history.counts, origin + years) for years in range(1, horizon + 1)]
        if any(status is None for status in statuses) or not same_business_frame(origin, origin + horizon):
            continue
        frame = history.origin_rows(origin)
        keys = pd.MultiIndex.from_frame(frame[["region_code", "industry_code"]].astype(str))
        meets = np.column_stack([status["meets"].reindex(keys).eq(True).to_numpy() for status in statuses])
        frame["declined"] = meets.any(axis=1).astype(int)
        rows.append(frame)
    data = pd.concat(rows, ignore_index=True)
    data["industry_code"] = pd.Categorical(data["industry_code"], categories=history.sections)
    return data


def feature_columns(data: pd.DataFrame) -> list[str]:
    excluded = {"region_code", "origin", "count", "declined"}
    return [column for column in data.columns if column not in excluded]


def fit_lgbm(train: pd.DataFrame, columns: list[str]) -> lgb.LGBMClassifier:
    model = lgb.LGBMClassifier(**LGBM_PARAMS)
    model.fit(train[columns], train["declined"], categorical_feature=CATEGORICAL)
    return model


def fit_logistic(train: pd.DataFrame, columns: list[str]):
    numeric = [column for column in columns if column not in CATEGORICAL]
    transform = ColumnTransformer([
        ("numeric", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), numeric),
        ("categorical", OneHotEncoder(handle_unknown="ignore"), ["industry_code", "town_type"]),
    ])
    model = make_pipeline(transform, LogisticRegression(max_iter=2000, C=0.5))
    frame = train[columns].copy()
    for column in ("industry_code", "town_type"):
        frame[column] = frame[column].astype(str)
    model.fit(frame, train["declined"])
    return model


def predict_logistic(model, rows: pd.DataFrame, columns: list[str]) -> np.ndarray:
    frame = rows[columns].copy()
    for column in ("industry_code", "town_type"):
        frame[column] = frame[column].astype(str)
    return model.predict_proba(frame)[:, 1]


def backtest(data: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Each test origin is scored by models trained on windows that had ended by then."""
    columns = feature_columns(data)
    results = []
    for test_origin in sorted(data["origin"].unique()):
        train = data[data["origin"] + horizon <= test_origin]
        if train["origin"].nunique() < 3 or train["declined"].nunique() < 2:
            continue
        test = data[data["origin"].eq(test_origin)].copy()
        rates = train.groupby("industry_code", observed=True)["declined"].mean()
        test["sector_rate"] = test["industry_code"].map(rates).astype(float).fillna(train["declined"].mean())
        past = test[f"growth_{horizon}y"] if f"growth_{horizon}y" in test else test["growth_1y"]
        test["past_decline"] = (-past).fillna(0.0)
        test["current_status"] = test["meets_now"].fillna(0).astype(float) + test["drawdown_now"].fillna(0).clip(lower=0)
        test["logistic"] = predict_logistic(fit_logistic(train, columns), test, columns)
        test["lgbm"] = fit_lgbm(train, columns).predict_proba(test[columns])[:, 1]
        test["ensemble"] = (test["logistic"] + test["lgbm"]) / 2
        results.append(test)
    return pd.concat(results, ignore_index=True)


METHODS = ("sector_rate", "past_decline", "current_status", "logistic", "lgbm", "ensemble", "calibrated")


def _logit(probability: pd.Series | np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(probability, dtype=float), 1e-4, 1 - 1e-4)
    return np.log(clipped / (1 - clipped)).reshape(-1, 1)


def fit_calibration(results: pd.DataFrame) -> LogisticRegression:
    """Platt scaling of out-of-time ensemble scores against what happened."""
    return LogisticRegression().fit(_logit(results["ensemble"]), results["declined"])


def apply_calibration(calibrator: LogisticRegression, probability: pd.Series | np.ndarray) -> np.ndarray:
    return calibrator.predict_proba(_logit(probability))[:, 1]


def add_calibrated(results: pd.DataFrame) -> pd.DataFrame:
    """Leave-one-origin-out calibrated scores, so the reported calibration is itself out of sample."""
    results = results.copy()
    results["calibrated"] = np.nan
    for origin in results["origin"].unique():
        held = results["origin"].eq(origin)
        calibrator = fit_calibration(results[~held])
        results.loc[held, "calibrated"] = apply_calibration(calibrator, results.loc[held, "ensemble"])
    return results


def score(results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for frame_name, part in (("전체", results), ("2019년까지 조사기준", results[results["new_business_frame"].eq(0)]),
                             ("2020년 이후 조사기준", results[results["new_business_frame"].eq(1)])):
        if part.empty or part["declined"].nunique() < 2:
            continue
        for method in METHODS:
            probability = method not in {"past_decline", "current_status"}
            rows.append({
                "frame": frame_name, "method": method, "rows": len(part), "event_rate": part["declined"].mean(),
                "roc_auc": roc_auc_score(part["declined"], part[method]),
                "pr_auc": average_precision_score(part["declined"], part[method]),
                "brier": brier_score_loss(part["declined"], part[method]) if probability else np.nan,
            })
    return pd.DataFrame(rows)


def calibration(results: pd.DataFrame, method: str = "calibrated", bins: int = 10) -> pd.DataFrame:
    groups = pd.qcut(results[method], bins, duplicates="drop")
    return results.groupby(groups, observed=True).agg(predicted=(method, "mean"), observed=("declined", "mean"),
                                                      rows=("declined", "size")).reset_index(drop=True)


def latest_rows(history: SectorHistory) -> pd.DataFrame:
    """Rows for the most recent year, which have features but no outcome yet."""
    return history.origin_rows(history.last_year)


def shap_drivers(model: lgb.LGBMClassifier, rows: pd.DataFrame, columns: list[str], top: int = 2) -> tuple[pd.Series, pd.Series]:
    import shap

    values = shap.TreeExplainer(model).shap_values(rows[columns])
    values = values[1] if isinstance(values, list) else values
    values = pd.DataFrame(values, index=rows.index, columns=columns)
    text = []
    for _, row in values.iterrows():
        raising = row[row > 0].sort_values(ascending=False)[:top]
        text.append(", ".join(FEATURE_LABELS.get(name, name) for name in raising.index) or "두드러진 위험 요인 없음")
    return pd.Series(text, index=rows.index), values.abs().mean().sort_values(ascending=False)


def forecast(history: SectorHistory, datasets: dict[int, pd.DataFrame],
             backtests: dict[int, pd.DataFrame]) -> tuple[pd.DataFrame, pd.Series]:
    rows = latest_rows(history)
    names = history.inputs.sectors.drop_duplicates("industry_code").set_index("industry_code")["industry_name"]
    result = rows[["region_code", "industry_code", "count"]].copy()
    result["industry_code"] = result["industry_code"].astype(str)
    result["industry_name"] = result["industry_code"].map(names).fillna("전체 사업체")
    result["origin_year"] = int(rows["origin"].iloc[0])
    status = statutory_status(history.counts, history.last_year)
    if status is not None:
        keys = pd.MultiIndex.from_frame(result[["region_code", "industry_code"]].astype(str))
        for column in ("drawdown", "decline_run", "by_peak", "by_run", "meets"):
            result[f"statute_{column}"] = status[column].reindex(keys).to_numpy()
    importance = pd.Series(dtype=float)
    for horizon, data in datasets.items():
        columns = feature_columns(data)
        model = fit_lgbm(data, columns)
        raw = (model.predict_proba(rows[columns])[:, 1] + predict_logistic(fit_logistic(data, columns), rows, columns)) / 2
        result[f"decline_prob_{horizon}y"] = apply_calibration(fit_calibration(backtests[horizon]), raw)
        # Drivers come from the 1-year model, the one the dashboard's watch stage uses.
        if horizon == min(datasets):
            result["drivers"], importance = shap_drivers(model, rows, columns)
    return result.reset_index(drop=True), importance


def town_summary(sector_forecast: pd.DataFrame) -> pd.DataFrame:
    """Establishment-weighted living-service risk and the section expected to shrink first."""
    living = sector_forecast[sector_forecast["industry_code"].isin(LIVING_SERVICE_SECTIONS)]
    weighted = living.assign(weight=living["count"]).groupby("region_code").apply(
        lambda group: pd.Series({
            "living_service_risk_1y": np.average(group["decline_prob_1y"], weights=group["weight"]),
            "living_service_risk_3y": np.average(group["decline_prob_3y"], weights=group["weight"]),
            "living_service_establishments": int(group["count"].sum()),
        }), include_groups=False)
    ordered = sector_forecast.sort_values(["region_code", "decline_prob_1y", "decline_prob_3y"], ascending=[True, False, False])
    first = ordered.groupby("region_code").head(3).groupby("region_code").apply(
        lambda group: pd.Series({
            "first_risk_sector": group["industry_name"].iloc[0],
            "first_risk_prob_1y": group["decline_prob_1y"].iloc[0],
            "first_risk_prob_3y": group["decline_prob_3y"].iloc[0],
            "first_risk_drivers": group["drivers"].iloc[0],
            "top3_risk_sectors": ", ".join(f"{name}(1년 {p1:.0%}·3년 {p3:.0%})" for name, p1, p3
                                           in zip(group["industry_name"], group["decline_prob_1y"], group["decline_prob_3y"])),
        }), include_groups=False)
    return weighted.join(first, how="outer").reset_index()


RANKING_MIN_TOWNS = 5
# "Any year within three" is met by about 70% of sections historically (43% meet it today),
# so only near-certain cases count as high risk.
HIGH_RISK_3Y = 0.9


def province_ranking(sector_forecast: pd.DataFrame) -> pd.DataFrame:
    """Sections present in at least RANKING_MIN_TOWNS towns, ordered by establishment-weighted
    1-year then 3-year decline probability."""
    def summarise(group: pd.DataFrame) -> pd.Series:
        return pd.Series({
            "towns": len(group),
            "establishments": int(group["count"].sum()),
            "decline_prob_1y": np.average(group["decline_prob_1y"], weights=group["count"]),
            "decline_prob_3y": np.average(group["decline_prob_3y"], weights=group["count"]),
            "high_risk_towns_3y": int((group["decline_prob_3y"] >= HIGH_RISK_3Y).sum()),
        })
    ranking = sector_forecast.groupby(["industry_code", "industry_name"]).apply(summarise, include_groups=False)
    ranking = ranking[ranking["towns"] >= RANKING_MIN_TOWNS].astype({"towns": int, "establishments": int, "high_risk_towns_3y": int})
    return ranking.sort_values(["decline_prob_1y", "decline_prob_3y"], ascending=False).reset_index()

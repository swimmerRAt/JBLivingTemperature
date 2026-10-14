"""Hold-out check of the youth and business models: train on 2/3 of the rows, score the other 1/3.

Rows are town-years (or town x section-years), so three splits are compared:

- random: rows shuffled 2:1. Neighbouring years of the same town and overlapping
  statutory windows land on both sides, so this is optimistic and shown only for
  contrast.
- temporal: the earliest origins (about 2/3 of rows) train, the latest 1/3 test.
- temporal_embargo: as temporal, but training rows whose outcome year falls after
  the first test origin are dropped, because their outcome was not yet known
  when that test forecast would have been made. This is the honest number.

Writes models/holdout_report.json and prints the tables.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.forecast import business, youth
from src.forecast.data import load_inputs

TEST_SHARE = 1 / 3
SEED = 7
OUTPUT = ROOT / "models" / "holdout_report.json"


def temporal_cut(data: pd.DataFrame) -> int:
    """First test origin: the origin boundary whose test share of rows is closest to 1/3."""
    counts = data["origin"].value_counts().sort_index()
    tail = counts[::-1].cumsum()[::-1] / counts.sum()
    return int((tail - TEST_SHARE).abs().idxmin())


def splits(data: pd.DataFrame, horizon: int) -> dict[str, tuple[pd.DataFrame, pd.DataFrame, str]]:
    shuffled = np.random.default_rng(SEED).random(len(data)) < TEST_SHARE
    cut = temporal_cut(data)
    early, late = data[data["origin"] < cut], data[data["origin"] >= cut]
    known = early[early["origin"] + horizon <= cut]
    origins = sorted(late["origin"].unique())

    def years(frame: pd.DataFrame) -> str:
        values = sorted(frame["origin"].unique())
        return f"{values[0]}~{values[-1]}"

    return {
        "random": (data[~shuffled], data[shuffled], "무작위"),
        "temporal": (early, late, f"학습 {years(early)} / 검증 {', '.join(map(str, origins))}"),
        "temporal_embargo": (known, late, f"학습 {years(known)} / 검증 {', '.join(map(str, origins))}"),
    }


def youth_scores(test: pd.DataFrame, predictions: dict[str, np.ndarray]) -> list[dict]:
    actual = np.expm1(test["target"]) * 100
    rows = []
    for method, growth in predictions.items():
        error = (np.expm1(growth) * 100 - actual).abs()
        predicted = test["youth"] * np.exp(growth)
        by_origin = (predicted.groupby(test["origin"]).sum() / test["youth_future"].groupby(test["origin"]).sum() - 1).abs() * 100
        rows.append({
            "method": method,
            "weighted_mae_pp": float(np.average(error, weights=test["youth"])),
            "mae_pp": float(error.mean()),
            "median_ae_pp": float(error.median()),
            "province_origin_abs_error_pct": float(by_origin.mean()),
        })
    return rows


def run_youth(inputs) -> dict:
    data = youth.build_dataset(inputs)
    columns = youth.feature_columns(data)
    result = {}
    for name, (train, test, label) in splits(data, youth.HORIZON).items():
        model = youth.fit_model(train, columns, residual=False)
        predictions = {
            "lgbm": youth.predict(model, test, columns, residual=False),
            "hamilton_perry": youth.baseline_growth(test).to_numpy(),
            "trend_3y": test["youth_growth_3y"].fillna(0.0).to_numpy(),
        }
        result[name] = {"label": label, "train_rows": len(train), "test_rows": len(test),
                        "scores": youth_scores(test, predictions)}
    return result


def business_scores(test: pd.DataFrame, predictions: dict[str, np.ndarray]) -> list[dict]:
    rows = []
    for method, score in predictions.items():
        probability = method in {"lgbm", "logistic", "ensemble"}
        rows.append({
            "method": method,
            "roc_auc": float(roc_auc_score(test["declined"], score)),
            "pr_auc": float(average_precision_score(test["declined"], score)),
            "brier": float(brier_score_loss(test["declined"], score)) if probability else None,
        })
    return rows


def run_business(inputs) -> dict:
    result = {}
    for total in (True, False):
        history = business.SectorHistory(inputs, total=total)
        for horizon in business.HORIZONS:
            data = business.build_dataset(history, horizon)
            columns = business.feature_columns(data)
            key = f"{'total' if total else 'sector'}_{horizon}y"
            result[key] = {}
            for name, (train, test, label) in splits(data, horizon).items():
                lgbm = business.fit_lgbm(train, columns).predict_proba(test[columns])[:, 1]
                logistic = business.predict_logistic(business.fit_logistic(train, columns), test, columns)
                predictions = {
                    "ensemble": (lgbm + logistic) / 2, "lgbm": lgbm, "logistic": logistic,
                    "current_status": (test["meets_now"].fillna(0) + test["drawdown_now"].fillna(0).clip(lower=0)).to_numpy(),
                }
                result[key][name] = {"label": label, "train_rows": len(train), "test_rows": len(test),
                                     "event_rate": float(test["declined"].mean()),
                                     "scores": business_scores(test, predictions)}
                if total and horizon == 1 and name == "temporal_embargo":
                    small = test["count"] < 200
                    result[key][name]["small_town_roc_auc"] = {
                        "under_200": float(roc_auc_score(test.loc[small, "declined"], predictions["ensemble"][small.to_numpy()]))
                        if test.loc[small, "declined"].nunique() == 2 else None,
                        "200_or_more": float(roc_auc_score(test.loc[~small, "declined"], predictions["ensemble"][~small.to_numpy()])),
                    }
    return result


def show(title: str, block: dict) -> None:
    print(f"\n### {title}")
    for name, part in block.items():
        print(f"- {name}: {part['label']} (학습 {part['train_rows']:,}행, 검증 {part['test_rows']:,}행)")
        print(pd.DataFrame(part["scores"]).round(3).to_string(index=False))


def main() -> None:
    warnings.filterwarnings("ignore")
    inputs = load_inputs(ROOT / "data")
    report = {"youth": run_youth(inputs), "business": run_business(inputs)}
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    show("청년 3년 예측", report["youth"])
    for key, block in report["business"].items():
        show(f"사업체 {key}", block)
    print(f"\nwritten: {OUTPUT}")


if __name__ == "__main__":
    main()

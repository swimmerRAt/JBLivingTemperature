from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.data_pipeline import database_path
from src.forecast import business, youth
from src.forecast.data import ForecastInputs, same_business_frame

needs_database = pytest.mark.skipif(not database_path(Path("data")).exists(), reason="source database not built")


def synthetic_inputs() -> ForecastInputs:
    """Two towns with flat age bands; town b loses a fifth of each cohort every five years."""
    rows = []
    for year in (2015, 2020):
        for code, scale in (("a", 1.0), ("b", 0.8 if year == 2020 else 1.0)):
            for age in range(0, 101, 5):
                rows.append({"region_code": code, "year": year, "age_start": age, "population": 100 * scale})
    bands = pd.DataFrame(rows).pivot_table(index=["region_code", "year"], columns="age_start", values="population")
    units = pd.DataFrame({"region_code": ["a", "b"], "city": ["가시", "가시"], "town": ["가동", "나면"], "area_km2": [1.0, 2.0],
                          "town_type": ["동", "면"]})
    return ForecastInputs(units=units, bands=bands, panel=pd.DataFrame(), sectors=pd.DataFrame(), city_medical=pd.DataFrame())


def test_cohort_net_migration_is_zero_for_a_stationary_town():
    migration = youth.cohort_net_migration(synthetic_inputs(), 2015, 2020)
    assert migration["a"] == pytest.approx(0.0)
    assert migration["b"] == pytest.approx(-80.0)  # four youth bands of 100 shrink to 80 each


def test_hamilton_perry_carries_cohort_ratios_forward():
    projection = youth.hamilton_perry(synthetic_inputs(), 2020, horizon=5)
    assert projection["a"] == pytest.approx(400.0)
    assert projection["b"] == pytest.approx(4 * 80 * 0.8)


def test_business_windows_never_cross_the_survey_frame_change():
    assert same_business_frame(2016, 2019)
    assert same_business_frame(2020, 2023)
    assert not same_business_frame(2018, 2021)


def test_missing_sector_year_counts_as_zero_establishments():
    inputs = synthetic_inputs()
    inputs.sectors = pd.DataFrame({
        "region_code": ["a", "a", "a"], "year": [2017, 2018, 2018], "industry_code": ["G", "G", "I"],
        "industry_name": ["도소매", "도소매", "숙박음식"], "establishments": [12, 13, 11], "employees_known": [20, 21, 15],
    })
    table = business.sector_table(inputs)
    assert table.loc[("a", "I"), 2017] == 0


@needs_database
def test_real_business_dataset_respects_frames_and_minimum_size():
    from src.forecast.data import load_inputs

    history = business.SectorHistory(load_inputs(Path("data")))
    data = business.build_dataset(history, 3)
    assert (data["count"] >= business.MIN_ESTABLISHMENTS).all()
    ends = data["origin"] + 3
    assert all(same_business_frame(end - business.STATUTE_WINDOW + 1, end) for end in ends.unique())
    assert data["declined"].between(0, 1).all()
    assert 0.2 < data["declined"].mean() < 0.7


def test_statutory_status_flags_peak_drop_and_consecutive_declines():
    counts = pd.DataFrame(
        {2010: [100, 100, 100], 2011: [110, 99, 100], 2012: [108, 98, 101], 2013: [106, 97, 100], 2014: [104, 101, 99]},
        index=pd.MultiIndex.from_tuples([("a", "ALL"), ("b", "ALL"), ("c", "ALL")], names=["region_code", "industry_code"]),
    )
    status = business.statutory_status(counts, 2014)
    assert bool(status.loc[("a", "ALL"), "by_peak"])      # 104 is 5.5% below the 110 peak
    assert bool(status.loc[("a", "ALL"), "by_run"])       # fell 2012, 2013, 2014
    assert bool(status.loc[("b", "ALL"), "by_run"])       # fell 2011-2013, so it meets even after recovering
    assert not bool(status.loc[("b", "ALL"), "by_peak"])
    assert not bool(status.loc[("c", "ALL"), "meets"])
    assert business.statutory_status(counts, 2022) is None

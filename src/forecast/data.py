"""Inputs shared by the forecasting models, read from the source database.

Everything here is indexed by the 242 analysis units of src.data_pipeline and
uses only values observed in or before each year, so a feature built for an
origin year never looks at later data.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.data_pipeline import (
    BUSINESS_SERIES_START,
    build_town_year_panel,
    database_path,
    load_units,
    town_age_bands,
)

YOUTH_BANDS = (20, 25, 30, 35)
ELDERLY_BANDS = tuple(range(65, 101, 5))


@dataclass
class ForecastInputs:
    units: pd.DataFrame        # region_code, city, town, area_km2, town_type
    bands: pd.DataFrame        # index (region_code, year), columns = five-year band starts
    panel: pd.DataFrame        # town-year panel from src.data_pipeline
    sectors: pd.DataFrame      # region_code, year, industry_code, industry_name, establishments, employees_known
    city_medical: pd.DataFrame  # city, year, medical facilities (Q4 count)
    female_bands: pd.DataFrame | None = None  # women, same layout as bands


def town_type(town: str) -> str:
    return "면" if town.endswith("면") else "읍" if town.endswith("읍") else "동"


def same_business_frame(start: int, end: int) -> bool:
    """Establishment counts are comparable only within one survey frame."""
    return (start >= BUSINESS_SERIES_START) == (end >= BUSINESS_SERIES_START)


def load_inputs(data_dir: Path) -> ForecastInputs:
    with sqlite3.connect(database_path(data_dir)) as connection:
        units, _ = load_units(connection)
        panel, _ = build_town_year_panel(connection, units)
        bands = town_age_bands(connection, units)
        female = town_age_bands(connection, units, "population_age_female")
        sectors = pd.read_sql_query("""
            SELECT town_code AS region_code, year, industry_code, industry_name,
                   establishment_count AS establishments, employee_count AS employees_known
            FROM sgis_business_town WHERE industry_code <> 'ALL' AND ksic_basis = 'KSIC9+'
        """, connection)
        medical = pd.read_sql_query("SELECT period_code, city_name AS city, facility_count FROM medical_city_quarterly_totals",
                                    connection)
    units = pd.DataFrame(units.drop(columns="geometry"))
    units["town_type"] = units["town"].map(town_type)
    def widen(long: pd.DataFrame) -> pd.DataFrame:
        wide = long.pivot_table(index=["region_code", "year"], columns="age_start", values="population", dropna=False)
        wide.columns = [int(column) for column in wide.columns]
        return wide
    wide = widen(bands)
    medical = medical[medical["period_code"].str.endswith("04")].copy()
    medical["year"] = medical["period_code"].str[:4].astype(int)
    return ForecastInputs(units=units, bands=wide, panel=panel, sectors=sectors,
                          city_medical=medical[["city", "year", "facility_count"]], female_bands=widen(female))


def youth_total(bands: pd.DataFrame) -> pd.Series:
    return bands[list(YOUTH_BANDS)].sum(axis=1, min_count=len(YOUTH_BANDS))


def at_year(frame: pd.DataFrame | pd.Series, year: int) -> pd.DataFrame | pd.Series:
    """Rows of a (region_code, year)-indexed frame for one year, indexed by region_code."""
    return frame.xs(year, level="year")


def log_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = numerator / denominator
    return np.log(ratio.where((ratio > 0) & np.isfinite(ratio)))

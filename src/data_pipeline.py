"""Build the eup/myeon/dong region panel from the source SQLite database.

The analysis unit is the 242 eup/myeon/dong used by the SGIS business series
(2024 boundaries, with 금암1·2동 merged as 금암동). Resident-registration rows
are mapped onto those units by name. Vacancy and youth net migration exist only
per city, so every town in a city carries the same city value.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
# Five-year bands nearest the youth definition; matches city_age20_39_net_migration.
YOUTH_AGE_START, YOUTH_AGE_END = 20, 39
ELDERLY_AGE_START = 65
# MOIS renamed these towns; the units use the current name.
TOWN_RENAMES = {
    ("김제시", "교동월촌동"): "교월동",
    ("완주군", "용진면"): "용진읍",
    ("정읍시", "시기3동"): "초산동",
    ("전주시", "동산동"): "여의동",
}
# Boundary changes folded into the units: 금암1·2동 merged in 2025, and
# 송천3동 split from 송천1동 in 2025.
TOWN_MERGES = {
    ("전주시", "금암1동"): "금암동",
    ("전주시", "금암2동"): "금암동",
    ("전주시", "송천3동"): "송천1동",
}
# Boundary changes that make earlier resident-registration populations
# incomparable with the current units: (first comparable year, city, towns).
# 2018-07-01 (전주시 조례 제3453호): 혁신동 created from 중동 and parts of 만성동·장동,
# and 효자4동 split into 효자4·5동. 2023-06-30: 만성지구 and 두현·용흥마을 moved
# from 여의동 to 혁신동 (about 15,700 residents). The SGIS census columns are
# already on current boundaries and need no reset.
BOUNDARY_RESETS = (
    (2018, "전주시", ("효자4동", "효자5동", "혁신동")),
    (2023, "전주시", ("혁신동", "여의동")),
)
# A same-year gain and loss of at least this many residents that cancel within
# 15% are reported as a possible unlisted boundary transfer.
TRANSFER_MIN_PEOPLE = 2000
IGNORED_TOWNS = {("익산시", "함열출장소")}  # zero-population MOIS branch-office row
# The establishment survey frame changed between 2019 and 2020.
BUSINESS_SERIES_START = 2020


def database_path(data_dir: Path) -> Path:
    return Path(data_dir) / "processed" / "living_areas.sqlite"


def unit_name(city: str, town: str) -> str | None:
    if (city, town) in IGNORED_TOWNS:
        return None
    town = TOWN_RENAMES.get((city, town), town)
    return TOWN_MERGES.get((city, town), town)


def _read(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, connection, params=params)


def load_units(connection: sqlite3.Connection) -> tuple[gpd.GeoDataFrame, dict[str, str]]:
    """Return the unit polygons and a map from SGIS boundary code to unit code."""
    units = _read(connection, """
        SELECT town_code AS region_code, city_name AS city, town_name AS town
        FROM sgis_business_town
        WHERE industry_code = 'ALL' AND year = (SELECT MAX(year) FROM sgis_business_town)
    """)
    if units.empty:
        raise FileNotFoundError("sgis_business_town is empty; run scripts/download_sgis_api.py and rebuild")
    row = connection.execute(
        "SELECT path FROM source_file WHERE source_family = 'sgis_api_boundary' "
        "AND path LIKE '%boundary_town_%' ORDER BY path DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise FileNotFoundError("SGIS town boundary is not registered in the database")
    boundary = json.loads((ROOT / row[0]).read_text(encoding="utf-8"))
    shapes = gpd.GeoDataFrame(
        [
            {
                "city": feature["properties"]["adm_nm"].split()[1],
                "town": unit_name(feature["properties"]["adm_nm"].split()[1],
                                  feature["properties"]["adm_nm"].split()[-1]),
                "boundary_code": str(feature["properties"]["adm_cd"]),
                "geometry": shape(feature["geometry"]),
            }
            for feature in boundary["features"]
        ],
        crs=boundary["crs"],
    )
    polygons = shapes.dissolve(by=["city", "town"], as_index=False)[["city", "town", "geometry"]]
    merged = polygons.merge(units, on=["city", "town"], how="outer", indicator=True)
    unmatched = merged.loc[merged["_merge"] != "both", ["city", "town"]]
    if not unmatched.empty:
        raise ValueError(f"Boundary and business units differ: {unmatched.values.tolist()}")
    merged = gpd.GeoDataFrame(merged.drop(columns="_merge"), geometry="geometry", crs=boundary["crs"])
    merged["area_km2"] = merged.geometry.area / 1_000_000
    centers = merged.geometry.representative_point().to_crs("EPSG:4326")
    merged["lat"], merged["lon"] = centers.y, centers.x
    codes = shapes.merge(units, on=["city", "town"])
    return merged.sort_values("region_code").reset_index(drop=True), dict(zip(codes["boundary_code"], codes["region_code"]))


def _population(connection: sqlite3.Connection, units: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    towns = _read(connection, """
        SELECT a.year, a.city_name AS city, a.name AS source_town, a.population_total AS population,
               SUM(CASE WHEN p.age_start >= ? AND p.age_end <= ? THEN p.population END) AS youth_population,
               SUM(CASE WHEN p.age_start >= ? THEN p.population END) AS elderly_population
        FROM area_year a JOIN population_age p ON p.year = a.year AND p.admin_code = a.admin_code
        WHERE a.level = 'town'
        GROUP BY a.year, a.admin_code
    """, (YOUTH_AGE_START, YOUTH_AGE_END, ELDERLY_AGE_START))
    towns["town"] = [unit_name(city, town) for city, town in zip(towns["city"], towns["source_town"])]
    towns = towns.dropna(subset=["town"])
    keyed = towns.merge(units[["region_code", "city", "town"]], on=["city", "town"], how="left")
    unmatched = sorted({f"{row.year} {row.city} {row.source_town}" for row in keyed[keyed["region_code"].isna()].itertuples()})
    measures = ["population", "youth_population", "elderly_population"]
    population = keyed.dropna(subset=["region_code"]).groupby(["region_code", "year"], as_index=False)[measures].sum()
    for first_year, city, towns_changed in BOUNDARY_RESETS:
        codes = units.loc[units["city"].eq(city) & units["town"].isin(towns_changed), "region_code"]
        population.loc[population["region_code"].isin(codes) & (population["year"] < first_year), measures] = float("nan")
    return population, unmatched


def town_age_bands(connection: sqlite3.Connection, units: pd.DataFrame, table: str = "population_age") -> pd.DataFrame:
    """Resident registration by unit, year and five-year band (age_start; 100 = 100+).
    `table` is population_age (everyone) or population_age_female."""
    if table not in {"population_age", "population_age_female"}:
        raise ValueError(f"unknown age table {table}")
    bands = _read(connection, f"""
        SELECT a.year, a.city_name AS city, a.name AS source_town, p.age_start, p.population
        FROM area_year a JOIN {table} p ON p.year = a.year AND p.admin_code = a.admin_code
        WHERE a.level = 'town'
    """)
    bands["town"] = [unit_name(city, town) for city, town in zip(bands["city"], bands["source_town"])]
    keyed = bands.dropna(subset=["town"]).merge(units[["region_code", "city", "town"]], on=["city", "town"])
    bands = keyed.groupby(["region_code", "year", "age_start"], as_index=False)["population"].sum()
    for first_year, city, towns_changed in BOUNDARY_RESETS:
        codes = units.loc[units["city"].eq(city) & units["town"].isin(towns_changed), "region_code"]
        bands.loc[bands["region_code"].isin(codes) & (bands["year"] < first_year), "population"] = float("nan")
    return bands


def suspected_transfers(panel: pd.DataFrame) -> list[str]:
    """Same-city, same-year population jumps that offset each other."""
    frame = panel.sort_values(["region_code", "year"]).copy()
    frame["delta"] = frame.groupby("region_code")["population"].diff()
    frame = frame[frame["delta"].abs() >= TRANSFER_MIN_PEOPLE]
    found = []
    for (city, year), group in frame.groupby(["city", "year"]):
        gains, losses = group[group["delta"] > 0], group[group["delta"] < 0]
        for gain in gains.itertuples():
            for loss in losses.itertuples():
                if abs(gain.delta + loss.delta) <= 0.15 * gain.delta:
                    found.append(f"{year} {city} {loss.town}→{gain.town} 약 {int(gain.delta):,}명")
    return found


def _census_population(connection: sqlite3.Connection) -> pd.DataFrame:
    """SGIS register-based census on current boundaries (2015 onward)."""
    census = _read(connection, """
        SELECT town_code AS region_code, year,
               SUM(CASE WHEN age_group = 'total' THEN population END) AS census_population,
               SUM(CASE WHEN age_start >= ? AND age_end <= ? AND age_end - age_start = 4 THEN population END)
                   AS census_youth_population,
               SUM(CASE WHEN age_group = '65+' THEN population END) AS census_elderly_population
        FROM sgis_population_town GROUP BY town_code, year
    """, (YOUTH_AGE_START, YOUTH_AGE_END))
    return census


def build_town_year_panel(connection: sqlite3.Connection, units: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Region-year panel: town population and business, plus city-level context."""
    population, unmatched = _population(connection, units)
    women = town_age_bands(connection, units, "population_age_female")
    female_youth = women[women["age_start"].between(YOUTH_AGE_START, YOUTH_AGE_END - 4)].groupby(
        ["region_code", "year"])["population"].sum(min_count=1).rename("female_youth_population")
    population = population.join(female_youth, on=["region_code", "year"])
    business = _read(connection, """
        SELECT town_code AS region_code, year, establishment_count AS establishments,
               employee_count AS employees_known
        FROM sgis_business_town WHERE industry_code = 'ALL'
    """)
    panel = population.merge(business, on=["region_code", "year"], how="outer")
    panel = panel.merge(_census_population(connection), on=["region_code", "year"], how="outer")
    panel = panel.merge(units[["region_code", "city", "town"]], on="region_code", how="left")
    panel["youth_ratio"] = panel["youth_population"] / panel["population"] * 100
    panel["elderly_ratio"] = panel["elderly_population"] / panel["population"] * 100
    # 지방소멸위험지수 (한국고용정보원): women aged 20-39 divided by everyone aged 65+.
    panel["extinction_risk_index"] = panel["female_youth_population"] / panel["elderly_population"]

    vacancy = _read(connection, "SELECT year, city_name AS city, vacancy_pct AS city_vacant_house_ratio FROM vacant_housing")
    migration = _read(connection, "SELECT year, city_name AS city, net_migration AS city_youth_net_migration "
                                  "FROM city_age20_39_net_migration")
    city_youth = population.merge(units[["region_code", "city"]], on="region_code").groupby(
        ["city", "year"], as_index=False)["youth_population"].sum(min_count=1)
    migration = migration.merge(city_youth, on=["city", "year"], how="left")
    migration["city_youth_net_migration_rate"] = migration["city_youth_net_migration"] / migration["youth_population"] * 1000
    panel = panel.merge(vacancy, on=["city", "year"], how="left")
    panel = panel.merge(migration.drop(columns="youth_population"), on=["city", "year"], how="left")
    columns = ["region_code", "city", "town", "year", "population", "youth_population", "elderly_population",
               "youth_ratio", "elderly_ratio", "female_youth_population", "extinction_risk_index", "census_population", "census_youth_population",
               "census_elderly_population", "establishments", "employees_known", "city_vacant_house_ratio",
               "city_youth_net_migration", "city_youth_net_migration_rate"]
    return panel[columns].sort_values(["region_code", "year"]).reset_index(drop=True), unmatched


def _snapshot_key(period: str) -> tuple[int, int]:
    year, month = period.split(".")
    return int(year), int(month)


def _current_infrastructure(connection: sqlite3.Connection, codes: dict[str, str]) -> tuple[pd.DataFrame, dict[str, str]]:
    stops = _read(connection, "SELECT town_code, stop_id FROM tago_stop_admin WHERE town_code IS NOT NULL")
    routes = _read(connection, """
        SELECT DISTINCT a.town_code, r.city_name || ':' || r.route_id AS route_key
        FROM tago_route_stop r
        JOIN tago_stop_admin a ON a.feed_city_name = r.city_name AND a.stop_id = r.stop_id
        WHERE a.town_code IS NOT NULL
    """)
    periods = [row[0] for row in connection.execute("SELECT DISTINCT snapshot_period FROM medical_facility_admin")]
    medical_period = max(periods, key=_snapshot_key)
    medical = _read(connection, """
        SELECT town_code, facility_kind, encrypted_facility_id FROM medical_facility_admin
        WHERE snapshot_period = ? AND town_code IS NOT NULL
    """, (medical_period,))
    for frame in (stops, routes, medical):
        frame["region_code"] = frame["town_code"].map(codes)
    counts = pd.concat([
        stops.groupby("region_code")["stop_id"].nunique().rename("bus_stop_count"),
        routes.groupby("region_code")["route_key"].nunique().rename("bus_route_count"),
        medical[medical["facility_kind"].eq("hospital")].groupby("region_code").size().rename("hospital_count"),
        medical[medical["facility_kind"].eq("pharmacy")].groupby("region_code").size().rename("pharmacy_count"),
    ], axis=1)
    retrieved = connection.execute("SELECT MAX(retrieved_at_utc) FROM tago_stop").fetchone()[0]
    return counts.reset_index(), {"medical": medical_period, "bus": str(retrieved)[:10]}


def _change(panel: pd.DataFrame, column: str, end: int, start: int) -> pd.Series:
    values = panel.pivot(index="region_code", columns="year", values=column)
    if end not in values or start not in values:
        return pd.Series(float("nan"), index=values.index)
    return (values[end] / values[start] - 1) * 100


def _latest(panel: pd.DataFrame, column: str) -> tuple[pd.Series, int]:
    year = int(panel.loc[panel[column].notna(), "year"].max())
    return panel[panel["year"].eq(year)].set_index("region_code")[column], year


def build_region_panel(data_dir: Path) -> tuple[pd.DataFrame, dict[str, object], pd.DataFrame]:
    """Return the latest per-town indicators, data-quality notes, and the full town-year panel."""
    path = database_path(data_dir)
    if not path.exists():
        raise FileNotFoundError(f"{path} 가 없습니다. scripts/build_living_database.py를 먼저 실행하세요.")
    with sqlite3.connect(path) as connection:
        units, codes = load_units(connection)
        panel, unmatched = build_town_year_panel(connection, units)
        infrastructure, snapshots = _current_infrastructure(connection, codes)

    population, population_year = _latest(panel, "population")
    business, business_year = _latest(panel, "establishments")
    vacancy, vacancy_year = _latest(panel, "city_vacant_house_ratio")
    migration_rate, migration_year = _latest(panel, "city_youth_net_migration_rate")
    by_year = panel.set_index(["region_code", "year"])
    region = pd.DataFrame(units.drop(columns="geometry")).set_index("region_code")
    region["region_name"] = region["city"] + " " + region["town"]
    region["population"] = population
    region["youth_ratio"] = by_year.xs(population_year, level="year")["youth_ratio"]
    region["elderly_ratio"] = by_year.xs(population_year, level="year")["elderly_ratio"]
    region["extinction_risk_index"] = by_year.xs(population_year, level="year")["extinction_risk_index"]
    region["population_change"] = _change(panel, "population", population_year, population_year - 1)
    region["population_change_5y"] = _change(panel, "population", population_year, population_year - 5)
    region["youth_change_5y"] = _change(panel, "youth_population", population_year, population_year - 5)
    census, census_year = _latest(panel, "census_population")
    region["census_population"] = census
    region["census_population_change_5y"] = _change(panel, "census_population", census_year, census_year - 5)
    region["census_youth_change_5y"] = _change(panel, "census_youth_population", census_year, census_year - 5)
    # Towns whose registration series was reset by a boundary change get the
    # census change instead. The census counts foreign residents, so the
    # substitute stays limited to those towns and is labelled.
    substitute = region["population_change_5y"].isna() & region["census_population_change_5y"].notna()
    region["change_5y_source"] = f"주민등록 {population_year - 5}→{population_year}"
    region.loc[substitute, "change_5y_source"] = f"등록센서스 {census_year - 5}→{census_year} (경계 조정 지역, 외국인 포함)"
    region.loc[substitute, "population_change_5y"] = region.loc[substitute, "census_population_change_5y"]
    region.loc[substitute, "youth_change_5y"] = region.loc[substitute, "census_youth_change_5y"]
    region["business_count"] = business
    region["employee_count_known"] = by_year.xs(business_year, level="year")["employees_known"]
    region["business_change_since_2020"] = _change(panel, "establishments", business_year, BUSINESS_SERIES_START)
    region["city_vacant_house_ratio"] = vacancy
    region["city_youth_net_migration"] = by_year.xs(migration_year, level="year")["city_youth_net_migration"]
    region["city_youth_net_migration_rate"] = migration_rate
    region = region.join(infrastructure.set_index("region_code"))
    for column in ("bus_stop_count", "bus_route_count", "hospital_count", "pharmacy_count"):
        region[column] = region[column].fillna(0).astype(int)  # no matched point means zero observed
    region["bus_stop_density"] = region["bus_stop_count"] / region["area_km2"]
    region = region.reset_index()
    forecast_sources = _attach_forecasts(region, Path(data_dir) / "processed")

    warnings = {
        "city_level": "빈집 비율과 청년 순이동은 시군 단위 자료라 같은 시군의 읍면동에 같은 값이 들어갑니다.",
        "bus": "버스 정류장 수는 TAGO 현재 스냅샷의 방향별 승강장 ID 수이며, 완주군 자체 농어촌·수요응답 버스는 빠져 있을 수 있습니다.",
    }
    substituted = region.loc[region["change_5y_source"].str.startswith("등록센서스"), "region_name"].tolist()
    if substituted:
        warnings["change_substitute"] = (f"{', '.join(substituted)}은 경계 조정으로 주민등록 5년 변화를 계산할 수 없어 "
                                         f"SGIS 등록센서스({census_year - 5}→{census_year}년, 외국인 포함) 값으로 대신했습니다.")
    gaps = {column: int(region[column].isna().sum()) for column in
            ("population", "youth_change_5y", "population_change_5y", "business_count") if region[column].isna().any()}
    if gaps:
        warnings["missing"] = "값이 비어 있는 읍면동: " + ", ".join(f"{column} {count}곳" for column, count in gaps.items())
    transfers = suspected_transfers(panel)
    if transfers:
        warnings["boundary_transfer"] = ("BOUNDARY_RESETS에 없는 경계 조정 의심(같은 해 인구가 맞바뀜): "
                                         + ", ".join(transfers[:5]))
    if unmatched:
        warnings["unmatched_towns"] = f"분석 단위에 연결되지 않은 주민등록 읍면동 {len(unmatched)}건: " + ", ".join(unmatched[:5])
    quality = {
        "unit": "읍면동",
        "region_count": int(len(region)),
        "database": path.name,
        "sources": {
            "population": f"주민등록 인구 {population_year}년 (area_year, population_age)",
            "census": f"SGIS 등록센서스 인구 {census_year}년, 현재 경계 (sgis_population_town)",
            "business": f"SGIS 전국사업체조사 {business_year}년 (sgis_business_town)",
            "medical": f"HIRA 의료기관 {snapshots['medical']} (medical_facility_admin)",
            "bus": f"TAGO 정류장·노선 {snapshots['bus']} (tago_stop_admin, tago_route_stop)",
            "vacancy": f"빈집 비율 {vacancy_year}년, 시군 (vacant_housing)",
            "migration": f"20~39세 순이동 {migration_year}년, 시군 (city_age20_39_net_migration)",
        },
        "warnings": warnings,
    }
    quality["sources"].update(forecast_sources)
    if not forecast_sources:
        warnings["forecast"] = "예측 결과 파일이 없습니다. scripts/train_models.py를 실행하면 3년 예측이 연결됩니다."
    quality["source_count"] = len(quality["sources"])
    return region, quality, panel


FORECAST_COLUMNS = {
    "forecast_youth.csv": {
        "target_year": "youth_forecast_year", "youth_forecast": "youth_forecast",
        "youth_change_pct": "youth_forecast_change_3y", "youth_change_low_pct": "youth_forecast_change_low",
        "youth_change_high_pct": "youth_forecast_change_high", "net_migration_forecast": "youth_net_migration_forecast",
        "drivers": "youth_forecast_drivers", "prediction_method": "youth_forecast_method",
        "forecast_note": "youth_forecast_note",
        "extinction_risk_index_forecast": "extinction_risk_index_forecast",
    },
    "forecast_business_town.csv": {
        "establishments": "business_count_latest", "business_drawdown": "business_drawdown",
        "business_decline_run": "business_decline_run", "business_statute_by_peak": "business_statute_by_peak",
        "business_statute_by_run": "business_statute_by_run", "business_statute_meets": "business_statute_meets",
        "business_decline_prob_1y": "business_decline_prob_1y", "business_decline_prob_3y": "business_decline_prob_3y",
        "business_drivers": "business_drivers",
        "living_service_risk_1y": "living_service_risk_1y", "living_service_risk_3y": "living_service_risk_3y",
        "living_service_establishments": "living_service_establishments",
        "first_risk_sector": "first_risk_sector", "first_risk_prob_1y": "first_risk_prob_1y",
        "first_risk_prob_3y": "first_risk_prob_3y", "first_risk_drivers": "first_risk_drivers",
        "top3_risk_sectors": "top3_risk_sectors",
    },
}


def _attach_forecasts(region: pd.DataFrame, processed: Path) -> dict[str, str]:
    """Add model outputs written by scripts/train_models.py, when present."""
    sources = {}
    for name, columns in FORECAST_COLUMNS.items():
        path = processed / name
        if not path.exists():
            continue
        frame = pd.read_csv(path, dtype={"region_code": str}).set_index("region_code")
        frame = frame[[column for column in columns if column in frame]].rename(columns=columns)
        for column in frame:
            region[column] = region["region_code"].map(frame[column])
        sources[name.removesuffix(".csv")] = f"모델 예측 ({name})"
    return sources


def write_processed(data_dir: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    region, quality, panel = build_region_panel(data_dir)
    output = Path(data_dir) / "processed"
    output.mkdir(parents=True, exist_ok=True)
    region.to_csv(output / "region_panel.csv", index=False)
    panel.to_csv(output / "town_year_panel.csv", index=False)
    (output / "data_quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    return region, quality


def load_dashboard_data(data_dir: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    data_dir = Path(data_dir)
    processed = data_dir / "processed" / "region_panel.csv"
    quality_path = processed.parent / "data_quality.json"
    database = database_path(data_dir)
    stale = database.exists() and processed.exists() and database.stat().st_mtime > processed.stat().st_mtime
    if processed.exists() and quality_path.exists() and not stale:
        regions = pd.read_csv(processed, dtype={"region_code": str})
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
    else:
        regions, quality = write_processed(data_dir)
    if regions[["lat", "lon"]].isna().any().any():
        quality.setdefault("warnings", {})["coordinates"] = "지도 좌표가 없는 지역은 지도에 표시되지 않습니다."
    return regions, quality

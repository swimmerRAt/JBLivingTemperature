"""Load validated KOSIS and TAGO API snapshots into typed SQLite tables."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

CITY_NAMES = {
    "전주시", "군산시", "익산시", "정읍시", "남원시", "김제시", "완주군",
    "진안군", "무주군", "장수군", "임실군", "순창군", "고창군", "부안군",
}


def age_range(label: str) -> tuple[int | None, int | None]:
    if label == "계":
        return None, None
    match = re.fullmatch(r"(\d+)\s*-\s*(\d+)세", label)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.fullmatch(r"(\d+)(?:세)?\s*(?:이상|\+)", label)
    if match:
        return int(match.group(1)), None
    match = re.fullmatch(r"청년층\((\d+)-(\d+)세\)", label)
    if match:
        return int(match.group(1)), int(match.group(2))
    raise ValueError(f"Unknown KOSIS age band: {label}")


def area_level(code: str, name: str) -> str:
    if len(code) == 2:
        return "province"
    if name in CITY_NAMES:
        return "city"
    if len(code) == 5:
        return "district"
    return "town"


def city_for(code: str, city_codes: dict[str, str]) -> str | None:
    if code in city_codes:
        return city_codes[code]
    prefix = code[:5]
    if prefix in city_codes:
        return city_codes[prefix]
    # Jeonju's Wansan and Deokjin districts have separate five-digit codes.
    for city_code, city_name in city_codes.items():
        if city_name == "전주시" and code[:4] == city_code[:4]:
            return city_name
    return None


def value_or_null(value: object) -> int | None:
    if value in (None, "", "-", "X"):
        return None
    return int(str(value).replace(",", ""))


def read_records(path: Path) -> list[dict]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"Expected KOSIS list: {path}")
    return records


def load_historical_population(connection: sqlite3.Connection, files: dict) -> None:
    for path, source_id in files.get("kosis_api_registered_population", []):
        records = read_records(path)
        city_codes = {r["C1"]: r["C1_NM"] for r in records if r["C1_NM"] in CITY_NAMES}
        if len(city_codes) != 14:
            raise ValueError(f"Expected 14 historic cities in {path}, found {len(city_codes)}")
        batch = []
        for record in records:
            if record["TBL_ID"] != "DT_1B04005" or record["ITM_ID"] != "T2":
                raise ValueError(f"Unexpected historic population record in {path}")
            code, name = record["C1"], record["C1_NM"]
            start, end = age_range(record["C2_NM"])
            batch.append((int(record["PRD_DE"]), code, name, area_level(code, name),
                          city_for(code, city_codes), -1 if start is None else start, end,
                          int(record["DT"]), source_id))
        connection.executemany("INSERT INTO historical_population_age VALUES (?,?,?,?,?,?,?,?,?)", batch)


def load_migration(connection: sqlite3.Connection, files: dict) -> None:
    for family, table_id in (("kosis_api_migration_age", "DT_1B26001"),
                             ("kosis_api_migration_sex", "DT_1B26001_A02")):
        for path, source_id in files.get(family, []):
            batch = []
            for record in read_records(path):
                if record["TBL_ID"] != table_id or record["ITM_ID"] not in {"T10", "T20", "T25"}:
                    raise ValueError(f"Unexpected migration record in {path}")
                sex_code = record["C2"]
                age_code = record.get("C3", "000")
                if family == "kosis_api_migration_age" and sex_code != "0":
                    raise ValueError(f"Expected total sex in {path}")
                if family == "kosis_api_migration_sex" and sex_code == "0":
                    continue  # The age table already includes the all-sex total.
                start, end = age_range(record.get("C3_NM", "계"))
                people = value_or_null(record["DT"])
                if people is None:
                    raise ValueError(f"Missing annual migration count in {path}")
                batch.append((int(record["PRD_DE"]), record["C1"], record["C1_NM"],
                              sex_code, age_code, start, end, record["ITM_ID"], people, source_id))
            connection.executemany("INSERT INTO migration_annual_count VALUES (?,?,?,?,?,?,?,?,?,?)", batch)

    for path, source_id in files.get("kosis_api_migration_rate", []):
        batch = []
        for record in read_records(path):
            if record["TBL_ID"] != "DT_1B26006" or record["ITM_ID"] not in {"T10", "T20", "T30"}:
                raise ValueError(f"Unexpected migration-rate record in {path}")
            if record["C2"] != "0":
                raise ValueError(f"Expected total sex in {path}")
            start, end = age_range(record.get("C3_NM", "계"))
            raw = record["DT"]
            rate = None if raw in (None, "", "-", "X") else float(raw)
            batch.append((int(record["PRD_DE"]), record["C1"], record["C1_NM"],
                          record["C2"], record["C3"], start, end, record["ITM_ID"],
                          rate, source_id))
        connection.executemany("INSERT INTO migration_annual_rate VALUES (?,?,?,?,?,?,?,?,?,?)", batch)


def load_business(connection: sqlite3.Connection, files: dict) -> None:
    for path, source_id in files.get("kosis_api_business_town", []):
        records = read_records(path)
        city_codes = {r["C1"]: r["C1_NM"] for r in records if r["C1_NM"] in CITY_NAMES}
        if len(city_codes) != 14:
            raise ValueError(f"Expected 14 business cities in {path}, found {len(city_codes)}")
        grouped: dict[tuple[str, str], dict] = defaultdict(dict)
        for record in records:
            if record["TBL_ID"] != "DT_2KI2011" or record["PRD_DE"] != "2020":
                raise ValueError(f"Unexpected business record in {path}")
            key = (record["C1"], record["C2"])
            grouped[key][record["ITM_ID"]] = value_or_null(record["DT"])
            grouped[key]["name"] = record["C1_NM"]
            grouped[key]["industry"] = record["C2_NM"]
        batch = []
        for (code, industry_code), item in grouped.items():
            if "T10" not in item or "T20" not in item:
                raise ValueError(f"Missing business metric for {code}/{industry_code} in {path}")
            name = item["name"]
            batch.append((code, name, area_level(code, name), city_for(code, city_codes),
                          industry_code, item["industry"], item["T10"], item["T20"], source_id))
        connection.executemany("INSERT INTO business_2020 VALUES (?,?,?,?,?,?,?,?,?)", batch)


def load_housing(connection: sqlite3.Connection, files: dict) -> None:
    for path, source_id in files.get("kosis_api_vacant_housing", []):
        grouped: dict[tuple[int, str], dict] = defaultdict(dict)
        for record in read_records(path):
            if record["TBL_ID"] != "DT_1YL202005":
                raise ValueError(f"Unexpected vacancy record in {path}")
            key = (int(record["PRD_DE"]), record["C1_NM"])
            grouped[key][record["ITM_ID"]] = record["DT"]
        for (year, city), item in grouped.items():
            if city not in CITY_NAMES:
                continue
            if set(item) != {"T10", "T20", "T30"}:
                raise ValueError(f"Incomplete vacancy metrics in {path}: {city}")
            row = (year, city, value_or_null(item["T20"]), value_or_null(item["T30"]),
                   None if item["T10"] in ("-", "X") else float(item["T10"]), source_id)
            existing = connection.execute(
                "SELECT vacant_count, housing_count, vacancy_pct FROM vacant_housing WHERE year=? AND city_name=?",
                (year, city),
            ).fetchone()
            if existing is None:
                connection.execute("INSERT INTO vacant_housing VALUES (?,?,?,?,?,?)", row)
            elif existing != row[2:5]:
                raise ValueError(f"Vacancy CSV/API mismatch for {city} {year}")

    for path, source_id in files.get("kosis_api_housing_types", []):
        records = read_records(path)
        city_codes = {r["C1"]: r["C1_NM"] for r in records if r["C1_NM"] in CITY_NAMES}
        if len(city_codes) != 14:
            raise ValueError(f"Expected 14 housing cities in {path}, found {len(city_codes)}")
        batch = []
        for record in records:
            if record["TBL_ID"] != "DT_1JU1501":
                raise ValueError(f"Unexpected housing record in {path}")
            code, name = record["C1"], record["C1_NM"]
            batch.append((int(record["PRD_DE"]), code, name, area_level(code, name),
                          city_for(code, city_codes), record["ITM_ID"], record["ITM_NM"],
                          value_or_null(record["DT"]), source_id))
        connection.executemany("INSERT INTO housing_type_area_annual VALUES (?,?,?,?,?,?,?,?,?)", batch)


def load_medical_quarterly(connection: sqlite3.Connection, files: dict) -> None:
    for path, source_id in files.get("kosis_api_medical_city", []):
        batch = []
        for record in read_records(path):
            if record["TBL_ID"] != "DT_HIRA4B" or record["PRD_SE"] != "Q":
                raise ValueError(f"Unexpected HIRA quarterly record in {path}")
            batch.append((record["PRD_DE"], record["C1"], record["C1_NM"],
                          record["C2"], record["C2_NM"], value_or_null(record["DT"]),
                          source_id))
        connection.executemany("INSERT INTO medical_city_quarterly VALUES (?,?,?,?,?,?,?)", batch)


def load_transport_api(connection: sqlite3.Connection, files: dict) -> None:
    groups = {
        "kosis_api_transport_access_old": "access_mode",
        "kosis_api_transport_access_new": "access_mode",
        "kosis_api_transport_time": "access_time",
        "kosis_api_transport_trips_old": "trip_frequency",
        "kosis_api_transport_trips_new": "trip_frequency",
    }
    for family, metric_group in groups.items():
        for path, source_id in files.get(family, []):
            batch = []
            for record in read_records(path):
                if record["ORG_ID"] != "116" or record["C1_NM"] != "전북":
                    raise ValueError(f"Unexpected transport area in {path}")
                raw = record["DT"]
                value = None if raw in (None, "", "-", "X") else float(raw)
                batch.append((int(record["PRD_DE"]), metric_group, record["TBL_ID"],
                              record["ITM_ID"], record["ITM_NM"], value,
                              record.get("UNIT_NM"), source_id))
            connection.executemany("INSERT INTO province_transport_api VALUES (?,?,?,?,?,?,?,?)", batch)


def load_tago(connection: sqlite3.Connection, files: dict) -> None:
    for family, table in (("tago_api_stops", "tago_stop"), ("tago_api_routes", "tago_route"),
                          ("tago_api_route_stops", "tago_route_stop")):
        for path, source_id in files.get(family, []):
            data = json.loads(path.read_text(encoding="utf-8"))
            city = data.get("city_name")
            if city not in CITY_NAMES or len(data["items"]) != data["total_count"]:
                raise ValueError(f"Invalid TAGO snapshot coverage in {path}")
            retrieved = data["retrieved_at_utc"]
            if table == "tago_stop":
                rows = [(city, str(r["nodeid"]), str(r["nodenm"]), r.get("gpslati"),
                         r.get("gpslong"), retrieved, source_id) for r in data["items"]]
                connection.executemany("INSERT INTO tago_stop VALUES (?,?,?,?,?,?,?)", rows)
            elif table == "tago_route":
                rows = [(city, str(r["routeid"]), str(r.get("routeno", "")), r.get("routetp"),
                         r.get("startnodenm"), r.get("endnodenm"), retrieved, source_id)
                        for r in data["items"]]
                connection.executemany("INSERT INTO tago_route VALUES (?,?,?,?,?,?,?,?)", rows)
            else:
                route_id = data["route_id"]
                if any(str(r.get("routeid")) != route_id for r in data["items"]):
                    raise ValueError(f"Route ID mismatch in {path}")
                rows = [(source_id, index, city, route_id, r.get("nodeid"), r.get("nodenm"),
                         r.get("nodeord"), str(r.get("updowncd", "")), r.get("gpslati"), r.get("gpslong"))
                        for index, r in enumerate(data["items"])]
                connection.executemany("INSERT INTO tago_route_stop VALUES (?,?,?,?,?,?,?,?,?,?)", rows)


def load_all(connection: sqlite3.Connection, files: dict) -> None:
    load_historical_population(connection, files)
    load_migration(connection, files)
    load_business(connection, files)
    load_housing(connection, files)
    load_medical_quarterly(connection, files)
    load_transport_api(connection, files)
    load_tago(connection, files)

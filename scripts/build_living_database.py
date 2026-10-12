"""Build a source-traceable SQLite database for the Jeonbuk living-area project.

This command reads files already present under data/. It does not fetch data.
Each build writes a temporary database and replaces the final one only after
the integrity checks pass.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile

import openpyxl
import pandas as pd
from normalize_api import load_all as load_normalized_api

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATABASE_PATH = DATA_DIR / "processed" / "living_areas.sqlite"
SCHEMA_PATH = ROOT / "db" / "schema.sql"
WORKBOOK_PATH = next(
    path for path in (ROOT / "docs").glob("*.xlsx")
    if unicodedata.normalize("NFC", path.name).endswith("_데이터_다운로드_출처.xlsx")
)
CITY_NAMES = {
    "전주시", "군산시", "익산시", "정읍시", "남원시", "김제시", "완주군",
    "진안군", "무주군", "장수군", "임실군", "순창군", "고창군", "부안군",
}
MOIS_DOWNLOAD_URL = "https://jumin.mois.go.kr/ageStatMonth.do"
BUS_STOP_URL = "https://www.data.go.kr/data/15067528/fileData.do"
HIRA_URL = "https://opendata.hira.or.kr/op/opc/selectOpenData.do?sno=11925"


def nfc(value: object) -> str:
    return unicodedata.normalize("NFC", str(value)).strip()


def integer(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    cleaned = str(value).replace(",", "").strip()
    if cleaned in {"", "-", "nan"}:
        return None
    return int(float(cleaned))


def real(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    cleaned = str(value).replace(",", "").strip()
    if cleaned in {"", "-", "nan"}:
        return None
    return float(cleaned)


def source_family(path: Path) -> str:
    name = nfc(path.name)
    if path.parent.name == "kosis_api" and path.suffix == ".json":
        return "kosis_api_" + name.rsplit("_", 1)[0]
    if path.parent.name == "tago" and path.suffix == ".json":
        if name.startswith("route_stops_"):
            return "tago_api_route_stops"
        return "tago_api_" + name.split("_", 1)[0]
    if name.startswith("mois_resident_age_"):
        return "mois_resident_age"
    if name.startswith("전국_버스정류장_"):
        return "bus_stop_national"
    if "전국 병의원 및 약국 현황" in name:
        return "hira_medical_zip"
    tokens = {
        "성및연령별_추계인구": "kosis_projected_population_age",
        "주요_연령계층별": "kosis_projected_population_structure",
        "장래_인구변동요인": "kosis_projected_population_components",
        "시군구 성별이동자수": "kosis_migration_sex",
        "연령별 이동자수": "kosis_migration_age",
        "산업별취업자": "kosis_employment_industry",
        "직업별취업자": "kosis_employment_occupation",
        "교육정도별_취업자": "kosis_employment_education",
        "미거주주택": "kosis_vacant_housing",
        "주택의_종류별": "kosis_housing_types",
        "시군구별_종별_요양기관": "kosis_medical_summary",
        "시도별_대중교통_접근수단": "kosis_transport_access_mode",
        "시도별_대중교통_접근시간": "kosis_transport_access_time",
        "시도별_대중교통이용횟수": "kosis_transport_trip_frequency",
    }
    return next((family for token, family in tokens.items() if token in name), "other")


def file_provenance(family: str) -> tuple[str | None, str | None]:
    if family.startswith("kosis_api_"):
        return "https://kosis.kr/openapi/Param/statisticsParameterData.do", "국가통계포털"
    if family.startswith("tago_api_"):
        page = "15098529" if family in {"tago_api_routes", "tago_api_route_stops"} else "15098534"
        return f"https://www.data.go.kr/data/{page}/openapi.do", "국토교통부"
    if family == "mois_resident_age":
        return MOIS_DOWNLOAD_URL, "행정안전부"
    if family == "bus_stop_national":
        return BUS_STOP_URL, "국토교통부"
    if family == "hira_medical_zip":
        return HIRA_URL, "건강보험심사평가원"
    return None, None


def register_files(connection: sqlite3.Connection) -> dict[str, list[tuple[Path, int]]]:
    files = sorted(
        (p for p in DATA_DIR.rglob("*") if p.is_file() and "processed" not in p.relative_to(DATA_DIR).parts
         and p.suffix.lower() in {".csv", ".zip", ".json"}),
        key=lambda p: str(p),
    )
    result: dict[str, list[tuple[Path, int]]] = defaultdict(list)
    for path in files:
        family = source_family(path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        url, provider = file_provenance(family)
        period = (re.search(r"(20\d{2}(?:\.\d{1,2})?)", nfc(path.name)) or [None])[0]
        cursor = connection.execute(
            "INSERT INTO source_file(path, source_family, original_url, provider, period_label, byte_count, sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(path.relative_to(ROOT)), family, url, provider, period, path.stat().st_size, digest.hexdigest()),
        )
        result[family].append((path, cursor.lastrowid))
    return result


CATALOG_STATUS = {
    **{row: ("partial", "행정안전부 대안 출처 2008~2025년 읍면동 자료 정규화; 2001~2007년은 정규화되지 않음") for row in range(5, 10)},
    **{row: ("partial", "기존 이동 CSV는 전북 시도 행만 포함; 시군구별 이동 지표는 정규화되지 않음") for row in range(10, 14)},
    **{row: ("missing", "계획한 읍면동별 사업체수·종사자수 연간 자료가 없음; 보유 취업자 CSV는 대체 불가") for row in range(14, 17)},
    17: ("ready", "2021~2025년 시군구별 빈집 수를 보유 CSV에서 적재"),
    18: ("ready", "2021~2025년 시군구별 빈집 비율을 보유 CSV에서 적재"),
    19: ("partial", "주택 종류 CSV는 전북 시도 합계만 제공; 읍면동 패널 자료가 아님"),
    20: ("partial", "HIRA가 공개한 시설 ZIP 스냅샷 확보; 계획의 월별 자료가 아닌 분기말·연말 공개본"),
    21: ("partial", "HIRA 개별 시설에서 시군별 수를 산출; KOSIS 공표 집계 CSV는 전북 시도 합계로 별도 적재"),
    22: ("partial", "HIRA 시설 종류는 공개 ZIP 스냅샷 기준; 계획의 월별 시계열은 제공되지 않음"),
    23: ("partial", "국토교통부 전국 정류장 CSV 확보; 익산·완주의 수집 범위가 매우 작음"),
    24: ("requires_key", "전주시 승강장 API 인증키 필요; 전국 정류장 파일만 부분 확보"),
    25: ("requires_key", "TAGO 버스 노선 API 활용신청 및 인증키 필요"),
    26: ("requires_key", "TAGO 버스 도착·위치 API 활용신청 및 인증키 필요; 이력은 별도 수집 필요"),
    27: ("ready", "전북 시도 단위 2020~2024년 접근수단 통계 보유"),
    28: ("ready", "전북 시도 단위 2020~2024년 접근시간 통계 보유"),
    29: ("ready", "전북 시도 단위 2020~2024년 이용횟수 통계 보유"),
}

CATALOG_FILE_FAMILIES = {
    **{row: ("mois_resident_age", "kosis_api_registered_population") for row in range(5, 10)},
    **{row: ("kosis_migration_sex", "kosis_api_migration_sex") for row in range(10, 13)},
    13: ("kosis_migration_age", "kosis_api_migration_age", "kosis_api_migration_rate"),
    14: ("kosis_api_business_town",),
    15: ("kosis_api_business_town",),
    16: ("kosis_api_business_town",),
    17: ("kosis_vacant_housing", "kosis_api_vacant_housing"),
    18: ("kosis_vacant_housing", "kosis_api_vacant_housing"),
    19: ("kosis_housing_types", "kosis_api_housing_types"),
    20: ("hira_medical_zip",),
    21: ("hira_medical_zip", "kosis_medical_summary", "kosis_api_medical_city"),
    22: ("hira_medical_zip",),
    23: ("bus_stop_national", "tago_api_stops"),
    24: ("bus_stop_national", "tago_api_stops"),
    25: ("tago_api_routes", "tago_api_route_stops"),
    27: ("kosis_transport_access_mode", "kosis_api_transport_access_old", "kosis_api_transport_access_new"),
    28: ("kosis_transport_access_time", "kosis_api_transport_time"),
    29: ("kosis_transport_trip_frequency", "kosis_api_transport_trips_old", "kosis_api_transport_trips_new"),
}


def register_catalog(connection: sqlite3.Connection, files: dict[str, list[tuple[Path, int]]]) -> None:
    workbook = openpyxl.load_workbook(WORKBOOK_PATH, read_only=True, data_only=True)
    sheet = workbook["데이터_다운로드_목록"]
    for row_number, cells in enumerate(sheet.iter_rows(min_row=5, max_row=29, values_only=True), start=5):
        status, status_note = CATALOG_STATUS[row_number]
        api_families = [family for family in CATALOG_FILE_FAMILIES.get(row_number, ()) if family.startswith(("kosis_api_", "tago_api_"))]
        if any(files.get(family) for family in api_families):
            status = "partial"
            status_note += "; API 원본 응답을 별도 보관·적재했으나 지표별 분류 및 범위 검증은 아직 필요"
        connection.execute(
            "INSERT INTO catalog_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row_number, *cells[:11], status, status_note),
        )
        for family in CATALOG_FILE_FAMILIES.get(row_number, ()):
            for _, source_id in files.get(family, []):
                connection.execute(
                    "INSERT INTO catalog_source_file(workbook_row, source_file_id) VALUES (?, ?)",
                    (row_number, source_id),
                )


def load_population(connection: sqlite3.Connection, files: dict[str, list[tuple[Path, int]]]) -> None:
    for path, source_id in files.get("mois_resident_age", []):
        year = int(re.search(r"(20\d{2})", path.stem).group(1))
        with path.open(encoding="cp949", newline="") as stream:
            reader = csv.DictReader(stream)
            total_column = f"{year}년_계_총인구수"
            age_columns = []
            for column in reader.fieldnames or []:
                match = re.fullmatch(rf"{year}년_계_(\d+)~(\d+)세", column)
                if match:
                    age_columns.append((column, int(match.group(1)), int(match.group(2))))
                elif column == f"{year}년_계_100세 이상":
                    age_columns.append((column, 100, None))
            if len(age_columns) != 21:
                raise ValueError(f"{path}: expected 21 age bands, found {len(age_columns)}")
            for row in reader:
                full_name = nfc(row["행정구역"])
                if not full_name.startswith(("전라북도 ", "전북특별자치도 ")):
                    continue
                match = re.fullmatch(r"(.+?)\s*\((\d{10})\)", full_name)
                if not match:
                    raise ValueError(f"{path}: unrecognized administrative code in {full_name}")
                label, code = match.groups()
                label = label.strip()
                parts = label.split()[1:]
                if not parts:
                    level, city_name, name = "province", None, label
                else:
                    city_name = parts[0]
                    # MOIS retained a zero-population Iksan branch-office row in
                    # 2020-2021. Keep its source code without treating it as a city.
                    if city_name == "익산시함열출장소":
                        city_name = "익산시"
                        parts = [city_name, "함열출장소"]
                    if city_name not in CITY_NAMES:
                        raise ValueError(f"{path}: unknown Jeonbuk city {city_name}")
                    name = parts[-1]
                    level = "city" if len(parts) == 1 else "district" if len(parts) == 2 and name.endswith("구") else "town"
                total = integer(row[total_column])
                ages = [(start, end, integer(row[column])) for column, start, end in age_columns]
                if total is None or any(count is None for _, _, count in ages):
                    raise ValueError(f"{path}: missing age population at {full_name}")
                if sum(count for _, _, count in ages) != total:
                    raise ValueError(f"{path}: age bands do not sum to total at {full_name}")
                connection.execute(
                    "INSERT INTO area_year VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (year, code, name, label, level, city_name, total, source_id),
                )
                connection.executemany(
                    "INSERT INTO population_age VALUES (?, ?, ?, ?, ?, ?)",
                    [(year, code, start, end, count, source_id) for start, end, count in ages],
                )
        print(f"population {year}: imported", flush=True)


def load_vacancy(connection: sqlite3.Connection, files: dict[str, list[tuple[Path, int]]]) -> None:
    for path, source_id in files.get("kosis_vacant_housing", []):
        frame = pd.read_csv(path, header=None, encoding="utf-8-sig")
        columns: dict[int, dict[str, int]] = defaultdict(dict)
        for index in range(3, frame.shape[1]):
            year_text = nfc(frame.iat[0, index])
            if not year_text.isdigit() or nfc(frame.iat[2, index]) != "원데이터":
                continue
            heading = nfc(frame.iat[1, index])
            metric = "rate" if "빈집" in heading and "비율" in heading else "vacant" if "빈집" in heading and "수" in heading else "housing" if "전체주택" in heading else None
            if metric:
                columns[int(year_text)][metric] = index
        for _, row in frame.iloc[3:].iterrows():
            if nfc(row.iloc[0]) != "전북특별자치도" or nfc(row.iloc[2]) != "소계":
                continue
            city = nfc(row.iloc[1])
            if city not in CITY_NAMES:
                continue
            for year, mapping in columns.items():
                if set(mapping) != {"rate", "vacant", "housing"}:
                    raise ValueError(f"{path}: incomplete vacancy measures for {year}")
                connection.execute(
                    "INSERT INTO vacant_housing VALUES (?, ?, ?, ?, ?, ?)",
                    (year, city, integer(row.iloc[mapping["vacant"]]), integer(row.iloc[mapping["housing"]]), real(row.iloc[mapping["rate"]]), source_id),
                )


def load_province_housing(connection: sqlite3.Connection, files: dict[str, list[tuple[Path, int]]]) -> None:
    for path, source_id in files.get("kosis_housing_types", []):
        frame = pd.read_csv(path, header=None, encoding="utf-8-sig", dtype=str)
        matches = frame.index[frame.iloc[:, 0].eq("전북특별자치도")].tolist()
        if len(matches) != 1:
            raise ValueError(f"{path}: expected one Jeonbuk row")
        row = frame.iloc[matches[0]]
        for index in range(1, frame.shape[1]):
            year = nfc(frame.iat[0, index])
            if year.isdigit() and nfc(frame.iat[2, index]) == "원데이터":
                count = integer(row.iloc[index])
                if count is not None:
                    connection.execute(
                        "INSERT INTO province_housing_observation VALUES (?, ?, ?, ?)",
                        (int(year), nfc(frame.iat[1, index]), count, source_id),
                    )


def load_province_medical_summary(connection: sqlite3.Connection, files: dict[str, list[tuple[Path, int]]]) -> None:
    for path, source_id in files.get("kosis_medical_summary", []):
        frame = pd.read_csv(path, header=None, encoding="utf-8-sig", dtype=str)
        matches = frame.index[frame.iloc[:, 0].eq("전북")].tolist()
        if len(matches) != 1:
            raise ValueError(f"{path}: expected one Jeonbuk row")
        row = frame.iloc[matches[0]]
        for index in range(1, frame.shape[1]):
            if nfc(frame.iat[3, index]) != "원데이터":
                continue
            count = integer(row.iloc[index])
            if count is not None:
                connection.execute(
                    "INSERT INTO province_medical_summary VALUES (?, ?, ?, ?, ?)",
                    (nfc(frame.iat[0, index]), nfc(frame.iat[1, index]),
                     nfc(frame.iat[2, index]), count, source_id),
                )


def load_migration(connection: sqlite3.Connection, files: dict[str, list[tuple[Path, int]]]) -> None:
    for family in ("kosis_migration_sex", "kosis_migration_age"):
        for path, source_id in files.get(family, []):
            frame = pd.read_csv(path, encoding="cp949", dtype=str)
            for _, row in frame.iterrows():
                area = nfc(row.iloc[0])
                if area != "전북특별자치도":
                    continue
                group = nfc(row.iloc[1])
                movement = nfc(row.iloc[2])
                for period in frame.columns[4:]:
                    count = integer(row[period])
                    if count is None:
                        continue
                    kind = "month" if period.endswith(" 월") else "quarter"
                    connection.execute(
                        "INSERT INTO migration_observation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (family, period, kind, area, group if family.endswith("age") else "", group if family.endswith("sex") else "", movement, count, source_id),
                    )


def load_transport(connection: sqlite3.Connection, files: dict[str, list[tuple[Path, int]]]) -> None:
    for family in ("kosis_transport_access_mode", "kosis_transport_access_time", "kosis_transport_trip_frequency"):
        for path, source_id in files.get(family, []):
            frame = pd.read_csv(path, header=None, encoding="utf-8-sig")
            matches = frame.index[frame.iloc[:, 0].eq("전북")].tolist()
            if len(matches) != 1:
                raise ValueError(f"{path}: expected one Jeonbuk row")
            row = frame.iloc[matches[0]]
            for index in range(1, frame.shape[1]):
                year = nfc(frame.iat[0, index])
                if not year.isdigit() or nfc(frame.iat[2, index]) != "원데이터":
                    continue
                value = real(row.iloc[index])
                if value is not None:
                    connection.execute(
                        "INSERT INTO province_transport_observation VALUES (?, ?, ?, ?, ?)",
                        (family, int(year), nfc(frame.iat[1, index]), value, source_id),
                    )


def medical_city(source_city: object) -> str | None:
    name = nfc(source_city)
    if name.startswith("전주"):
        return "전주시"
    return name if name in CITY_NAMES else None


def load_medical(connection: sqlite3.Connection, files: dict[str, list[tuple[Path, int]]]) -> None:
    wanted = {
        "암호화요양기호", "요양기관명", "종별코드명", "시도코드명", "시군구코드명",
        "읍면동", "주소", "좌표(X)", "좌표(Y)",
    }
    seen_snapshots = set()
    for path, source_id in files.get("hira_medical_zip", []):
        name = nfc(path.name)
        if " (1)" in name:
            continue
        snapshot = re.search(r"(20\d{2}\.\d{1,2})", name).group(1)
        if snapshot in seen_snapshots:
            raise ValueError(f"duplicate medical snapshot {snapshot}")
        seen_snapshots.add(snapshot)
        with ZipFile(path) as archive:
            for number, kind in ((1, "hospital"), (2, "pharmacy")):
                matches = [member for member in archive.namelist() if member.endswith(".xlsx") and re.search(rf"(?:^|/){number}\.", member)]
                if len(matches) != 1:
                    raise ValueError(f"{path}: expected one facility workbook numbered {number}")
                with archive.open(matches[0]) as stream:
                    frame = pd.read_excel(stream, usecols=lambda column: column in wanted, dtype={"암호화요양기호": str})
                missing = {"암호화요양기호", "요양기관명", "시도코드명", "시군구코드명"} - set(frame.columns)
                if missing:
                    raise ValueError(f"{path}: missing columns {missing}")
                frame = frame[frame["시도코드명"].astype(str).str.contains("전북|전라북", na=False)]
                records = []
                for _, row in frame.iterrows():
                    city = medical_city(row["시군구코드명"])
                    if city is None:
                        continue
                    encrypted_id = nfc(row["암호화요양기호"])
                    if not encrypted_id or encrypted_id == "nan":
                        raise ValueError(f"{path}: missing encrypted facility ID")
                    records.append((
                        snapshot, kind, encrypted_id, nfc(row["요양기관명"]),
                        None if pd.isna(row.get("종별코드명")) else nfc(row.get("종별코드명")),
                        nfc(row["시군구코드명"]), city,
                        None if pd.isna(row.get("읍면동")) else nfc(row.get("읍면동")),
                        None if pd.isna(row.get("주소")) else nfc(row.get("주소")),
                        real(row.get("좌표(X)")), real(row.get("좌표(Y)")), source_id,
                    ))
                connection.executemany(
                    "INSERT INTO medical_facility_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", records,
                )
        print(f"medical {snapshot}: imported", flush=True)
    if "2024.12" not in seen_snapshots:
        connection.execute(
            "INSERT INTO data_issue VALUES (?, ?, ?)",
            ("medical_2024_12_gap", "warning", "공식 HIRA 목록에는 2024.12 ZIP이 있지만 로컬 원본에 없어 시설 시계열에 공백이 있음"),
        )


def load_bus_stops(connection: sqlite3.Connection, files: dict[str, list[tuple[Path, int]]]) -> None:
    for path, source_id in files.get("bus_stop_national", []):
        with path.open(encoding="cp949", newline="") as stream:
            reader = csv.DictReader(stream)
            batch = []
            for row in reader:
                raw_city = nfc(row["도시명"])
                if not raw_city.startswith(("전라북도 ", "전북특별자치도 ")):
                    continue
                city = raw_city.split()[-1]
                if city not in CITY_NAMES:
                    continue
                lat, lon = real(row["위도"]), real(row["경도"])
                if lat is None or lon is None:
                    continue
                batch.append((nfc(row["정류장번호"]), nfc(row["정류장명"]), city, raw_city,
                              nfc(row["도시코드"]), lat, lon, nfc(row["정보수집일"]), source_id))
                if len(batch) >= 1000:
                    connection.executemany("INSERT INTO bus_stop VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
                    batch.clear()
            connection.executemany("INSERT INTO bus_stop VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
    scarce = connection.execute(
        "SELECT city_name, COUNT(*) FROM bus_stop GROUP BY city_name HAVING COUNT(*) < 100 ORDER BY city_name"
    ).fetchall()
    if scarce:
        text = ", ".join(f"{city} {count}건" for city, count in scarce)
        connection.execute(
            "INSERT INTO data_issue VALUES (?, ?, ?)",
            ("bus_stop_coverage", "blocker", f"TAGO 연계 전국 파일의 전북 정류장 수집 범위가 불완전함: {text}; 지역 간 접근성 비교에 바로 사용 금지"),
        )


def load_api_records(connection: sqlite3.Connection, files: dict[str, list[tuple[Path, int]]]) -> None:
    for family, entries in files.items():
        if not family.startswith(("kosis_api_", "tago_api_")):
            continue
        provider = "KOSIS" if family.startswith("kosis_api_") else "TAGO"
        dataset = family.split("_api_", 1)[1]
        for path, source_id in entries:
            data = json.loads(path.read_text(encoding="utf-8"))
            records = data if provider == "KOSIS" else data.get("items")
            if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
                raise ValueError(f"{path}: invalid API source records")
            for index, record in enumerate(records):
                period = record.get("PRD_DE") if provider == "KOSIS" else data.get("retrieved_at_utc")
                area = data.get("city_name") if provider == "TAGO" else record.get("C1_NM")
                connection.execute(
                    "INSERT INTO api_record VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (source_id, index, provider, dataset, period, area,
                     json.dumps(record, ensure_ascii=False, separators=(",", ":"))),
                )
            print(f"API raw {path.name}: {len(records)} records", flush=True)


def build_database(destination: Path = DATABASE_PATH) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        files = register_files(connection)
        register_catalog(connection, files)
        load_population(connection, files)
        load_vacancy(connection, files)
        load_province_housing(connection, files)
        load_province_medical_summary(connection, files)
        load_migration(connection, files)
        load_transport(connection, files)
        load_medical(connection, files)
        load_bus_stops(connection, files)
        load_api_records(connection, files)
        load_normalized_api(connection, files)
        if connection.execute("SELECT COUNT(DISTINCT year) FROM historical_population_age").fetchone()[0] == 7:
            connection.execute("UPDATE catalog_item SET acquisition_status='ready', status_note=? "
                               "WHERE workbook_row BETWEEN 5 AND 9",
                               ("KOSIS 2001~2007년과 행안부 2008~2025년 전북 시군·읍면동 연령별 인구 적재; "
                                "행정구역 코드의 연도별 변경에 유의",))
        if connection.execute("SELECT COUNT(DISTINCT year) FROM migration_annual_count").fetchone()[0] == 25:
            connection.execute("UPDATE catalog_item SET acquisition_status='partial', status_note=? "
                               "WHERE workbook_row BETWEEN 10 AND 12",
                               ("KOSIS 시군별 연간 전입·전출·순이동 2001~2025년 적재; "
                                "월별·분기별 시군구 시계열은 미적재",))
            connection.execute("UPDATE catalog_item SET acquisition_status='partial', status_note=? "
                               "WHERE workbook_row=13",
                               ("KOSIS 시군별 연간 5세 연령대 이동자 수와 별도 이동률 2001~2025년 적재; "
                                "연령별 이동률은 합산 불가, 월별 수치 미적재",))
        if connection.execute("SELECT COUNT(*) FROM business_2020").fetchone()[0]:
            connection.execute("UPDATE catalog_item SET acquisition_status='partial', status_note=? "
                               "WHERE workbook_row IN (14,15)",
                               ("2020년 경제총조사 전북 읍면동 산업별 사업체·종사자 수 적재; "
                                "X·-는 결측 처리, 연속연도 자료는 미확보",))
            connection.execute("UPDATE catalog_item SET acquisition_status='missing', status_note=? "
                               "WHERE workbook_row=16",
                               ("2020년 단년도 경제총조사만 있어 연간 사업체 증감률 계산 불가; "
                                "전국사업체조사 온라인간행물의 연속연도 원본 필요",))
        if connection.execute("SELECT COUNT(DISTINCT year) FROM vacant_housing").fetchone()[0] == 11:
            connection.execute("UPDATE catalog_item SET acquisition_status='ready', status_note=? "
                               "WHERE workbook_row IN (17,18)",
                               ("KOSIS 전북 14개 시군 2015~2025년 빈집 수·전체 주택·비율 적재",))
        if connection.execute("SELECT COUNT(*) FROM housing_type_area_annual").fetchone()[0]:
            connection.execute("UPDATE catalog_item SET acquisition_status='partial', status_note=? "
                               "WHERE workbook_row=19",
                               ("KOSIS 주택 유형 2015~2025년 적재; 읍면동은 2015·2020·2025년만 공표",))
        medical_periods = connection.execute("SELECT COUNT(DISTINCT period_code) "
                                             "FROM medical_city_quarterly").fetchone()[0]
        if medical_periods:
            connection.execute("UPDATE catalog_item SET acquisition_status='partial', status_note=? "
                               "WHERE workbook_row=21",
                               (f"KOSIS 전북 시군구·기관종별 분기 집계 {medical_periods}개 분기 적재; "
                                "월별 시군 시계열과 HIRA 시설 스냅샷은 기준이 다름",))
        if connection.execute("SELECT COUNT(*) FROM historical_population_age").fetchone()[0] == 0:
            connection.execute(
                "INSERT INTO data_issue VALUES (?, ?, ?)",
                ("population_2001_2007_gap", "warning", "2001~2007년 주민등록 인구 행은 아직 정규화되지 않음"),
            )
        connection.execute(
            "INSERT INTO data_issue VALUES (?, ?, ?)",
            ("business_data_gap", "blocker", "읍면동별 연속연도 사업체수·종사자수 지표는 없음; 단년도 경제총조사와 취업자 통계로 연간 증감률을 만들 수 없음"),
        )
        if connection.execute("SELECT COUNT(*) FROM province_transport_api").fetchone()[0]:
            connection.execute("INSERT INTO data_issue VALUES (?, ?, ?)",
                               ("transport_category_break", "warning",
                                "2018년 전후 대중교통 접근수단·이용횟수 통계표의 일부 항목이 다름; "
                                "동일 범주를 확인하기 전에는 직접 시계열 비교하지 말 것"))
        if connection.execute("SELECT COUNT(*) FROM migration_annual_count").fetchone()[0] == 0:
            connection.execute(
                "INSERT INTO data_issue VALUES (?, ?, ?)",
                ("migration_spatial_gap", "blocker", "시군구별 연령별 순이동 자료가 없음"),
            )
        tago_cities = {row[0] for row in connection.execute("SELECT DISTINCT city_name FROM tago_stop")}
        missing_tago = sorted(CITY_NAMES - tago_cities)
        if tago_cities:
            connection.execute("UPDATE catalog_item SET acquisition_status='partial', status_note=? "
                               "WHERE workbook_row=23",
                               (f"TAGO 현재 정류장 {len(tago_cities)}개 시군 적재; 도시코드 미수록: "
                                f"{', '.join(missing_tago) or '없음'}",))
            connection.execute("UPDATE data_issue SET severity='warning', description=? "
                               "WHERE issue_key='bus_stop_coverage'",
                               ("기존 전국 정류장 CSV는 일부 시군이 불완전함; 정류장 분석은 TAGO API "
                                "스냅샷을 사용하고 미수록 지역은 제외해야 함",))
            route_count = connection.execute("SELECT COUNT(*) FROM tago_route").fetchone()[0]
            route_file_count = len(files.get("tago_api_route_stops", []))
            route_stop_count = connection.execute("SELECT COUNT(DISTINCT city_name || ':' || route_id) "
                                                  "FROM tago_route_stop").fetchone()[0]
            connection.execute("UPDATE catalog_item SET acquisition_status='partial', status_note=? "
                               "WHERE workbook_row=24",
                               ("TAGO 전주시 정류장 좌표 적재; 전주시 전용 승강장 API는 별도 미수집",))
            connection.execute("UPDATE catalog_item SET acquisition_status='partial', status_note=? "
                               "WHERE workbook_row=25",
                               (f"TAGO 노선 {route_count}개, 경유 정류소 확보 노선 {route_stop_count}개; "
                                "완주군은 도시코드 목록에 없음",))
            if route_file_count < route_count:
                connection.execute("INSERT INTO data_issue VALUES (?, ?, ?)",
                                   ("route_stop_gap", "warning",
                                    f"TAGO 경유정류소 조회 파일 {route_file_count}/{route_count}개; "
                                    "누락 노선은 노선별 호출을 재개해야 함"))
        connection.execute("UPDATE catalog_item SET acquisition_status='missing', status_note=? "
                           "WHERE workbook_row=26",
                           ("도착·차량 위치는 실시간 API이며 현재 이력 적재 없음; "
                            "호출 대상과 주기를 정한 뒤 수집해야 함",))
        for workbook_row, metric_group in ((27, "access_mode"), (28, "access_time"),
                                           (29, "trip_frequency")):
            years = connection.execute("SELECT COUNT(DISTINCT year) FROM province_transport_api "
                                       "WHERE metric_group=?", (metric_group,)).fetchone()[0]
            if years == 11:
                note = "KOSIS 전북 시도 2014~2024년 적재; 원천 통계표의 시기별 항목 차이를 확인하고 비교해야 함"
                state = "ready"
            else:
                note = f"KOSIS 전북 시도 2014~2024년 중 {years}개 연도 API 적재; 누락 기간 확인 필요"
                state = "partial"
            connection.execute("UPDATE catalog_item SET acquisition_status=?, status_note=? "
                               "WHERE workbook_row=?", (state, note, workbook_row))
        if missing_tago:
            connection.execute("INSERT INTO data_issue VALUES (?, ?, ?)",
                               ("tago_city_gap", "warning", "TAGO 정류장 API 도시코드 미수록: " + ", ".join(missing_tago)))
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise ValueError(f"SQLite integrity failed: {integrity}, foreign keys: {foreign_key_errors[:3]}")
        city_years = connection.execute("SELECT year, COUNT(*) FROM city_year_population GROUP BY year").fetchall()
        if not city_years or any(count != 14 for _, count in city_years):
            raise ValueError(f"Expected 14 cities in each population year, found {city_years}")
        connection.commit()
    except BaseException:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    connection.close()
    temporary.replace(destination)
    print(f"database: {destination}", flush=True)


if __name__ == "__main__":
    try:
        build_database()
    except Exception as error:
        print(f"database build failed: {error}", file=sys.stderr)
        raise

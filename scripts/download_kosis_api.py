"""Save KOSIS API responses for the workbooks named in the source catalog.

The KOSIS API requires a real key. This downloader preserves the returned
records as source data; interpreting each table's dimensions is a separate
step after the actual response has been inspected.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
META_ENDPOINT = "https://kosis.kr/openapi/statisticsData.do"
TABLES = {
    "registered_population": ("101", "DT_1B04005"),
    "migration_sex": ("101", "DT_1B26001_A02"),
    "migration_age": ("101", "DT_1B26001"),
    "migration_rate": ("101", "DT_1B26006"),
    "business_town": ("101", "DT_2KI2011"),
    "vacant_housing": ("101", "DT_1YL202005"),
    "housing_types": ("101", "DT_1JU1501"),
}
QUERY_DIMENSIONS = {
    "registered_population": {"objL1": "45*", "objL2": "all", "itmId": "T2"},
    "migration_sex": {"objL1": "52*", "objL2": "all", "itmId": "T10+T20+T25"},
    "migration_age": {"objL1": "52*", "objL2": "0", "objL3": "all", "itmId": "T10+T20+T25"},
    "migration_rate": {"objL1": "52*", "objL2": "0", "objL3": "all", "itmId": "T10+T20+T30"},
    "business_town": {"objL1": "35*", "objL2": "all", "itmId": "T10+T20"},
    "vacant_housing": {"objL1": "35*", "itmId": "T10+T20+T30"},
    "housing_types": {"objL1": "35*", "itmId": "all"},
}


def read_key() -> str:
    value = os.environ.get("KOSIS_API_KEY", "").strip()
    if not value:
        env_path = ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("KOSIS_API_KEY="):
                    value = line.partition("=")[2].strip().strip('"\'')
                    break
    if not value or value.lower() in {"your_key_here", "placeholder"}:
        raise SystemExit("KOSIS_API_KEY is empty. Add the issued key to .env first.")
    return value


def request_json(endpoint: str, params: dict[str, str], context: str) -> list[dict]:
    request = Request(f"{endpoint}?{urlencode(params)}", headers={"User-Agent": "JBLivingTemperature/1.0"})
    # The request URL contains the key. Never print it or include it in an exception.
    try:
        with urlopen(request, timeout=120) as response:
            payload = response.read()
    except Exception as exc:
        raise RuntimeError(f"KOSIS {context}: request failed ({type(exc).__name__})") from None
    try:
        records = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError(f"KOSIS {context}: response is not JSON") from None
    if not isinstance(records, list) or not records or not all(isinstance(record, dict) for record in records):
        raise RuntimeError(f"KOSIS {context}: empty or error response")
    return records


def request_metadata(table: str, key: str, kind: str) -> list[dict]:
    org_id, table_id = TABLES[table]
    params = {"method": "getMeta", "type": kind, "apiKey": key,
              "orgId": org_id, "tblId": table_id, "format": "json", "jsonVD": "Y"}
    if kind == "PRD":
        params["detail"] = "Y"
    return request_json(META_ENDPOINT, params, f"{table} metadata {kind}")


def choose_period(metadata: list[dict], year: int) -> tuple[str, str] | None:
    section = ""
    for row in metadata:
        if "PRD_SE" in row:
            section = str(row["PRD_SE"])
        elif str(row.get("PRD_DE", "")) == str(year):
            if section == "년":
                return "Y", str(year)
            if section == "5년":
                return "F", str(year)
    return None


def request_table(table: str, period: tuple[str, str], key: str) -> list[dict]:
    org_id, table_id = TABLES[table]
    frequency, period_label = period
    params = {
        "method": "getList",
        "apiKey": key,
        "orgId": org_id,
        "tblId": table_id,
        **QUERY_DIMENSIONS[table],
        "prdSe": frequency,
        "startPrdDe": period_label,
        "endPrdDe": period_label,
        "format": "json",
        "jsonVD": "Y",
    }
    records = request_json(ENDPOINT, params, f"{table} {period_label}")
    if not all(record.get("TBL_ID") == table_id and str(record.get("PRD_DE", "")) == period_label for record in records):
        raise RuntimeError(f"KOSIS {table} {period_label}: unexpected table ID or period")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", choices=[*TABLES, "all"], default="all")
    parser.add_argument("--start", type=int, default=2020)
    parser.add_argument("--end", type=int, default=2025)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must not exceed --end")
    key = read_key()
    tables = TABLES if args.table == "all" else {args.table: TABLES[args.table]}
    output_dir = ROOT / "data" / "raw" / "kosis_api"
    output_dir.mkdir(parents=True, exist_ok=True)
    failed = []
    for table in tables:
        try:
            meta_periods = request_metadata(table, key, "PRD")
            meta_items = request_metadata(table, key, "ITM")
        except RuntimeError as exc:
            failed.append(f"{table} metadata")
            print(exc, flush=True)
            continue
        for kind, records in (("prd", meta_periods), ("itm", meta_items)):
            path = output_dir / f"{table}_meta_{kind}.json"
            temporary = path.with_suffix(".json.part")
            temporary.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        for year in range(args.start, args.end + 1):
            if table == "business_town" and year != 2020:
                continue  # DT_2KI2011 is the 2020 economic census table.
            period = choose_period(meta_periods, year)
            if period is None:
                print(f"{table} {year}: not listed in KOSIS period metadata", flush=True)
                continue
            target = output_dir / f"{table}_{year}.json"
            if target.exists() and not args.force:
                print(f"{table} {year}: already present", flush=True)
                continue
            try:
                records = request_table(table, period, key)
                temporary = target.with_suffix(".json.part")
                temporary.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
                temporary.replace(target)
                print(f"{table} {year}: {len(records)} records", flush=True)
            except RuntimeError as exc:
                failed.append(f"{table} {year}")
                print(exc, flush=True)
            time.sleep(0.5)
    if failed:
        raise SystemExit(f"Downloads requiring attention: {', '.join(failed)}")


if __name__ == "__main__":
    main()

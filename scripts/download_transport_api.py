"""Download KOSIS Jeonbuk public-transport access and use for 2014-2024."""

from __future__ import annotations

import json
import time

from download_kosis_api import ENDPOINT, ROOT, read_key, request_json

TABLES = {
    "transport_access_old": ("DT_MLTM_5720", "13102114750A.0014", 2014, 2017),
    "transport_access_new": ("DT_MLTM_6196", "13102131806A.0014", 2018, 2024),
    "transport_time": ("DT_MLTM_5721", "13102114757A.0014", 2014, 2024),
    "transport_trips_old": ("DT_MLTM_5719", "13102114755A.0014", 2014, 2017),
    "transport_trips_new": ("DT_MLTM_7149", "13102136796A.0014", 2018, 2024),
}


def main() -> None:
    key = read_key()
    directory = ROOT / "data" / "raw" / "kosis_api"
    directory.mkdir(parents=True, exist_ok=True)
    for name, (table_id, area_code, first, last) in TABLES.items():
        for year in range(first, last + 1):
            path = directory / f"{name}_{year}.json"
            if path.exists():
                continue
            rows = request_json(ENDPOINT, {
                "method": "getList", "apiKey": key, "orgId": "116", "tblId": table_id,
                "objL1": area_code, "itmId": "all", "prdSe": "Y",
                "startPrdDe": str(year), "endPrdDe": str(year),
                "format": "json", "jsonVD": "Y",
            }, f"{name} {year}")
            if any(r.get("TBL_ID") != table_id or r.get("PRD_DE") != str(year)
                   or r.get("C1_NM") != "전북" for r in rows):
                raise RuntimeError(f"Unexpected transport record in {name} {year}")
            temporary = path.with_suffix(".json.part")
            temporary.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
            print(f"{name} {year}: {len(rows)} records", flush=True)
            time.sleep(0.2)


if __name__ == "__main__":
    main()

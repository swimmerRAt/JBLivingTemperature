"""Download the KOSIS HIRA Jeonbuk city/district facility-type quarterly series."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from download_kosis_api import ENDPOINT, ROOT, read_key, request_json, request_metadata, TABLES

TABLES["medical_city"] = ("354", "DT_HIRA4B")
OUT = ROOT / "data" / "raw" / "kosis_api"


def main() -> None:
    key = read_key()
    periods = request_metadata("medical_city", key, "PRD")
    quarter = False
    available = []
    for row in periods:
        if "PRD_SE" in row:
            quarter = row["PRD_SE"] == "분기"
        elif quarter:
            match = re.fullmatch(r"(\d{4}) ([1-4])/4", row.get("PRD_DE", ""))
            if match:
                available.append((int(match.group(1)), int(match.group(2))))
    OUT.mkdir(parents=True, exist_ok=True)
    for year, q in available:
        label = f"{year}Q{q}"
        path = OUT / f"medical_city_{label}.json"
        if path.exists():
            continue
        period = f"{year}0{q}"
        rows = request_json(ENDPOINT, {
            "method": "getList", "apiKey": key, "orgId": "354", "tblId": "DT_HIRA4B",
            "objL1": "1535413102140064HC3.35*", "objL2": "all",
            "itmId": "1635413103140064SC0", "prdSe": "Q",
            "startPrdDe": period, "endPrdDe": period, "format": "json", "jsonVD": "Y",
        }, f"medical_city {label}")
        if any(r.get("TBL_ID") != "DT_HIRA4B" or r.get("PRD_DE") != period for r in rows):
            raise RuntimeError(f"HIRA quarterly: unexpected table or period {label}")
        temporary = path.with_suffix(".json.part")
        temporary.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        print(f"medical_city {label}: {len(rows)} records", flush=True)
        time.sleep(0.25)


if __name__ == "__main__":
    main()

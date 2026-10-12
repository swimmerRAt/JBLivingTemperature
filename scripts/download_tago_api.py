"""Download a current TAGO city/stop/route snapshot after API approval.

This is a snapshot collector. Arrival and bus-location feeds need a separate
scheduled collection policy; they cannot reconstruct historical service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://apis.data.go.kr/1613000"
CITY_NAMES = {
    "전주시", "군산시", "익산시", "정읍시", "남원시", "김제시", "완주군",
    "진안군", "무주군", "장수군", "임실군", "순창군", "고창군", "부안군",
}
OPERATIONS = {
    "city_codes": "BusSttnInfoInqireService/getCtyCodeList",
    "stops": "BusSttnInfoInqireService/getSttnNoList",
    "routes": "BusRouteInfoInqireService/getRouteNoList",
    "route_stops": "BusRouteInfoInqireService/getRouteAcctoThrghSttnList",
}


def read_key() -> str:
    value = os.environ.get("DATA_GO_KR_API_KEY", "").strip()
    if not value:
        env_path = ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("DATA_GO_KR_API_KEY="):
                    value = line.partition("=")[2].strip().strip('"\'')
                    break
    if not value or value.lower() in {"your_key_here", "placeholder"}:
        raise SystemExit("DATA_GO_KR_API_KEY is empty. Add the issued key to .env first.")
    return unquote(value)  # Accept either portal's encoded or decoded key.


def fetch(operation: str, key: str, **params: object) -> dict:
    query = urlencode({"serviceKey": key, "_type": "json", **params})
    request = Request(f"{BASE}/{OPERATIONS[operation]}?{query}", headers={"User-Agent": "JBLivingTemperature/1.0"})
    for attempt in range(7):
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except Exception as exc:
            # urllib exceptions can include the complete URL, including the key.
            raise RuntimeError(f"TAGO {operation}: request failed ({type(exc).__name__})") from None
        result = payload.get("response", {}) if isinstance(payload, dict) else {}
        header, body = result.get("header", {}), result.get("body", {})
        if str(header.get("resultCode")) in {"00", "0"} and isinstance(body, dict):
            return body
        transient_session_error = (str(header.get("resultCode")) == "99"
                                   and "가용한 세션" in str(header.get("resultMsg", "")))
        incomplete_response = not header
        if (transient_session_error or incomplete_response) and attempt < 6:
            time.sleep(min(5 * (attempt + 1), 30))
            continue
        raise RuntimeError(f"TAGO {operation}: response error {header.get('resultCode', 'unknown')}: "
                           f"{header.get('resultMsg', 'no message')}")
    raise RuntimeError(f"TAGO {operation}: session retry exhausted")


def items(body: dict) -> list[dict]:
    value = body.get("items", {})
    value = value.get("item", []) if isinstance(value, dict) else []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError("TAGO: unexpected items structure")
    return value


def fetch_pages(operation: str, key: str, city_code: str, **filters: object) -> tuple[list[dict], int]:
    records: list[dict] = []
    page = 1
    while True:
        body = fetch(operation, key, cityCode=city_code, numOfRows=1000, pageNo=page, **filters)
        batch = items(body)
        total = int(body.get("totalCount", 0))
        records.extend(batch)
        if len(records) >= total:
            if len(records) != total:
                raise RuntimeError(f"TAGO {operation} {city_code}: page count mismatch")
            return records, total
        if not batch:
            raise RuntimeError(f"TAGO {operation} {city_code}: incomplete pagination")
        page += 1
        time.sleep(0.2)


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def canonical_city(value: str) -> str | None:
    for city in CITY_NAMES:
        if value == city or value == city[:-1] or value.endswith(" " + city):
            return city
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["all", "city_codes", "stops", "routes", "route_stops"], default="all")
    parser.add_argument("--city", choices=sorted(CITY_NAMES), help="Limit collection to one city")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    key = read_key()
    directory = ROOT / "data" / "raw" / "tago"
    city_path = directory / "city_codes.json"
    if not city_path.exists() or args.force:
        body = fetch("city_codes", key)
        codes = items(body)
        if not codes:
            raise SystemExit("TAGO city-code list is empty")
        save(city_path, {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "items": codes})
    else:
        codes = json.loads(city_path.read_text(encoding="utf-8"))["items"]
    print(f"city codes: {len(codes)}", flush=True)
    if args.dataset == "city_codes":
        return
    chosen = {}
    for entry in codes:
        city = canonical_city(str(entry.get("cityname", "")))
        if city and (args.city is None or city == args.city):
            if city in chosen:
                raise SystemExit(f"TAGO lists duplicate codes for {city}; inspect city_codes.json")
            chosen[city] = str(entry["citycode"])
    if not chosen:
        raise SystemExit("No selected Jeonbuk cities appear in TAGO city-code list")
    missing = sorted((CITY_NAMES if args.city is None else {args.city}) - chosen.keys())
    if missing:
        print(f"TAGO city-code coverage gap: {', '.join(missing)}", flush=True)
    datasets = ("stops", "routes") if args.dataset == "all" else (() if args.dataset == "route_stops" else (args.dataset,))
    for dataset in datasets:
        for city, code in sorted(chosen.items()):
            target = directory / f"{dataset}_{city}.json"
            if target.exists() and not args.force:
                print(f"{dataset} {city}: already present", flush=True)
                continue
            records, total = fetch_pages(dataset, key, code)
            save(target, {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                          "city_name": city, "city_code": code, "total_count": total, "items": records})
            print(f"{dataset} {city}: {total} records", flush=True)
            time.sleep(0.3)
    if args.dataset in {"all", "route_stops"}:
        for city, code in sorted(chosen.items()):
            routes_path = directory / f"routes_{city}.json"
            if not routes_path.exists():
                raise SystemExit(f"Collect route list first: {routes_path}")
            routes = json.loads(routes_path.read_text(encoding="utf-8"))["items"]
            for route in routes:
                route_id = str(route.get("routeid", ""))
                if not route_id:
                    raise SystemExit(f"Unexpected route ID for {city}; inspect {routes_path}")
                route_hash = hashlib.sha256(route_id.encode("utf-8")).hexdigest()[:16]
                target = directory / f"route_stops_{city}_{route_hash}.json"
                if target.exists() and not args.force:
                    continue
                records, total = fetch_pages("route_stops", key, code, routeId=route_id)
                save(target, {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                              "city_name": city, "city_code": code, "route_id": route_id,
                              "total_count": total, "items": records})
                if sum(1 for _ in directory.glob(f"route_stops_{city}_*.json")) % 50 == 0:
                    print(f"route stops {city}: 50 more routes saved", flush=True)
                time.sleep(0.05)


if __name__ == "__main__":
    main()

"""Download SGIS Jeonbuk town business counts, population and boundaries.

The SGIS business API serves the national establishment survey by eup/myeon/dong
for 2000 onward. Each year is classified with that year's KSIC revision, so a
section letter does not name the same industry in every year. The population
API serves the register-based census (2015 onward at town level) re-aggregated
on current town boundaries. Boundaries are UTM-K (EPSG:5179) polygons used to
place TAGO stops in their actual city.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://sgisapi.kostat.go.kr/OpenAPI3"
PROVINCE_CODE = "35"
SECTIONS = "ABCDEFGHIJKLMNOPQRSTU"
OUT = ROOT / "data" / "raw" / "sgis"
# searchpopulation age_type codes, read off the responses: "01".."20" are the
# five-year bands 0-4 .. 95-99, "21" is 100+, and "22"/"23"/"24" are 0-14, 15-64
# and 65+. The documentation shows the code table only as an image.
POPULATION_AGE_GROUPS = {
    **{f"{index:02d}": f"{(index - 1) * 5}-{(index - 1) * 5 + 4}" for index in range(1, 21)},
    "21": "100+", "22": "0-14", "23": "15-64", "24": "65+",
}


def read_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        env_path = ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith(f"{name}="):
                    value = line.partition("=")[2].strip().strip('"\'')
                    break
    if not value:
        raise SystemExit(f"{name} is empty. Add the SGIS service ID and secret to .env first.")
    return value


def request(path: str, params: dict[str, str], context: str) -> dict:
    url = f"{BASE}/{path}?{urlencode(params)}"
    # The URL carries the key or access token. Never print it or include it in an exception.
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={"User-Agent": "JBLivingTemperature/1.0"}), timeout=120) as response:
                payload = json.load(response)
        except Exception as exc:
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"SGIS {context}: request failed ({type(exc).__name__})") from None
        if payload.get("errCd") == 0:
            return payload
        raise RuntimeError(f"SGIS {context}: error {payload.get('errCd')}: {payload.get('errMsg')}")
    raise RuntimeError(f"SGIS {context}: retry exhausted")


def authenticate() -> str:
    payload = request("auth/authentication.json", {
        "consumer_key": read_env("SGIS_CONSUMER_KEY"),
        "consumer_secret": read_env("SGIS_CONSUMER_SECRET"),
    }, "authentication")
    return payload["result"]["accessToken"]


def save(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def download_company(token: str, year: int, force: bool) -> None:
    target = OUT / f"company_{year}.json"
    if target.exists() and not force:
        print(f"company {year}: already present", flush=True)
        return
    queries = []
    for class_code in (None, *SECTIONS):
        params = {"accessToken": token, "year": str(year), "adm_cd": PROVINCE_CODE, "low_search": "2"}
        if class_code:
            params["class_code"] = class_code
        rows = request("stats/company.json", params, f"company {year} {class_code or 'all'}").get("result") or []
        if class_code is None and not rows:
            raise RuntimeError(f"SGIS company {year}: no town rows")
        if any(not str(row.get("adm_cd", "")).startswith(PROVINCE_CODE) or len(str(row["adm_cd"])) != 8
               for row in rows):
            raise RuntimeError(f"SGIS company {year} {class_code}: unexpected area code")
        queries.append({"class_code": class_code, "records": rows})
        time.sleep(0.1)
    total = sum(int(row["corp_cnt"]) for row in queries[0]["records"])
    by_section = sum(int(row["corp_cnt"]) for query in queries[1:] for row in query["records"])
    if total != by_section:
        raise RuntimeError(f"SGIS company {year}: sections sum to {by_section}, total is {total}")
    save(target, {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "year": year, "queries": queries})
    print(f"company {year}: {len(queries[0]['records'])} towns, {total} establishments", flush=True)


def download_population(token: str, year: int, force: bool) -> None:
    target = OUT / f"population_{year}.json"
    if target.exists() and not force:
        print(f"population {year}: already present", flush=True)
        return
    queries = []
    for age_type, group in (("", "total"), *POPULATION_AGE_GROUPS.items()):
        params = {"accessToken": token, "year": str(year), "adm_cd": PROVINCE_CODE, "low_search": "2"}
        if age_type:
            params["age_type"] = age_type
        rows = request("stats/searchpopulation.json", params, f"population {year} {group}").get("result") or []
        if any(not str(row.get("adm_cd", "")).startswith(PROVINCE_CODE) or len(str(row["adm_cd"])) != 8
               for row in rows):
            raise RuntimeError(f"SGIS population {year} {group}: unexpected area code")
        queries.append({"age_type": age_type or None, "age_group": group, "records": rows})
        time.sleep(0.1)
    totals = [row["population"] for row in queries[0]["records"]]
    if not totals or all(value in ("N/A", "", None) for value in totals):
        raise RuntimeError(f"SGIS population {year}: no town totals")
    save(target, {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "year": year, "queries": queries})
    print(f"population {year}: {len(totals)} towns", flush=True)


def download_industry_codes(token: str, force: bool) -> None:
    for revision in ("8", "9", "10", "11"):
        target = OUT / f"industry_codes_ksic{revision}.json"
        if target.exists() and not force:
            continue
        rows = request("stats/industrycode.json", {"accessToken": token, "class_deg": revision},
                       f"industry codes {revision}").get("result") or []
        save(target, {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                      "ksic_revision": int(revision), "items": rows})


def download_boundaries(token: str, year: int, force: bool) -> None:
    for level, low_search in (("city", "1"), ("town", "2")):
        target = OUT / f"boundary_{level}_{year}.json"
        if target.exists() and not force:
            print(f"boundary {level} {year}: already present", flush=True)
            continue
        payload = request("boundary/hadmarea.geojson", {
            "accessToken": token, "year": str(year), "adm_cd": PROVINCE_CODE, "low_search": low_search,
        }, f"boundary {level} {year}")
        features = payload.get("features") or []
        if not features:
            raise RuntimeError(f"SGIS boundary {level} {year}: no features")
        save(target, {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "year": year,
                      "level": level, "crs": "EPSG:5179", "features": features})
        print(f"boundary {level} {year}: {len(features)} areas", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=2000)
    parser.add_argument("--end", type=int, default=2024)
    parser.add_argument("--boundary-year", type=int, default=2024)
    parser.add_argument("--population-start", type=int, default=2015)
    parser.add_argument("--population-end", type=int, default=2024)
    parser.add_argument("--dataset", choices=["all", "company", "population"], default="all")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must not exceed --end")
    token = authenticate()
    download_industry_codes(token, args.force)
    download_boundaries(token, args.boundary_year, args.force)
    failed = []
    jobs = []
    if args.dataset in {"all", "company"}:
        jobs += [(download_company, year) for year in range(args.start, args.end + 1)]
    if args.dataset in {"all", "population"}:
        jobs += [(download_population, year) for year in range(args.population_start, args.population_end + 1)]
    for job, year in jobs:
        try:
            job(token, year, args.force)
        except RuntimeError as exc:
            failed.append(f"{job.__name__.removeprefix('download_')} {year}")
            print(exc, flush=True)
    if failed:
        raise SystemExit(f"Downloads requiring attention: {', '.join(failed)}")


if __name__ == "__main__":
    main()

"""Download Jeonbuk everyday-facility locations from public APIs for the 30-minute analysis.

Raw responses are kept under data/raw/facilities/ (re-used unless --force) and the
normalised point table is written to data/processed/living_facilities.csv.
Senior-center records carry addresses only; they are geocoded with the SGIS
(Statistics Korea) geocoder and unmatched or coarse matches are dropped, never guessed.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from pyproj import Transformer

RAW = ROOT / "data/raw/facilities"
OUTPUT = ROOT / "data/processed/living_facilities.csv"
JEONBUK = ("전북특별자치도", "전라북도")
STORE_API = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInDong"
RETAIL_API = "https://apis.data.go.kr/1741000/large_scale_retail_stores/info"
CINEMA_API = "https://apis.data.go.kr/1741000/movie_theaters/info"
LIBRARY_API = "https://api.data.go.kr/openapi/tn_pubr_public_lbrry_api"
SCHOOL_API = "https://api.data.go.kr/openapi/tn_pubr_public_elesch_mskul_lc_api"
SENIOR_API = "https://apis.data.go.kr/6450000/rfcapiElrbs/getElrbsList"
SGIS = "https://sgisapi.kostat.go.kr/OpenAPI3"
# Small-category codes of the 2023 store classification; I2 is the whole food-service class.
STORE_FILTERS = {"supermarket": {"indsSclsCd": "G20404"}, "convenience": {"indsSclsCd": "G20405"},
                 "food": {"indsLclsCd": "I2"}}
EXCLUDED_FOOD = ("I211", "I20701")  # bars/pubs and staff canteens are not open everyday food places
LARGE_STORE_TYPES = {"대형마트", "백화점", "쇼핑센터", "복합쇼핑몰"}
LOCALDATA_CRS = Transformer.from_crs("EPSG:5174", "EPSG:4326", always_xy=True)


def read_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    env = ROOT / ".env"
    if not value and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                value = line.split("=", 1)[1].strip()
    if not value:
        raise SystemExit(f"{name}가 .env에 없습니다.")
    return value


def fetch(url: str, params: dict[str, str], context: str) -> str:
    query = urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(f"{url}?{query}", timeout=60) as response:
                return response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 3:
                raise RuntimeError(f"{context} 요청 실패: {exc}") from exc
            time.sleep(2 * (attempt + 1))
    raise AssertionError


def save_raw(name: str, endpoint: str, params: dict, items: list, basis: str | None) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    payload = {"endpoint": endpoint, "params": params, "fetched_at": datetime.now().isoformat(timespec="seconds"),
               "basis": basis, "count": len(items), "items": items}
    (RAW / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"{name}: {len(items)} records", flush=True)


def load_raw(name: str) -> dict:
    return json.loads((RAW / f"{name}.json").read_text(encoding="utf-8"))


def download_stores(key: str) -> None:
    for name, extra in STORE_FILTERS.items():
        items, page, basis = [], 1, None
        while True:
            params = {"divId": "ctprvnCd", "key": "52", "numOfRows": "1000", "pageNo": str(page), "type": "json", **extra}
            body = json.loads(fetch(STORE_API, {"ServiceKey": key, **params}, f"상가정보 {name}"))
            if body.get("header", {}).get("resultCode") not in ("00", None):
                if body["header"].get("resultCode") == "03":  # no more data
                    break
                raise RuntimeError(f"상가정보 오류: {body['header']}")
            basis = body["header"].get("stdrYm", basis)
            batch = body.get("body", {}).get("items") or []
            items += batch
            if len(items) >= int(body["body"].get("totalCount", 0)) or not batch:
                break
            page += 1
        save_raw(f"store_{name}", STORE_API, {"divId": "ctprvnCd", "key": "52", **extra}, items, basis)


def download_localdata(name: str, endpoint: str, address_field: str, key: str) -> None:
    items: dict[str, dict] = {}
    for province in JEONBUK:
        page = 1
        while True:
            params = {"pageNo": str(page), "numOfRows": "100", "returnType": "json",
                      "cond[SALS_STTS_CD::EQ]": "01", f"cond[{address_field}::LIKE]": province}
            body = json.loads(fetch(endpoint, {"serviceKey": key, **params}, name))["response"]["body"]
            batch = (body.get("items") or {}).get("item") or []
            items.update({item["MNG_NO"]: item for item in batch})
            if page * 100 >= int(body.get("totalCount") or 0) or not batch:
                break
            page += 1
    basis = max((item.get("DAT_UPDT_PNT") or "" for item in items.values()), default=None)
    save_raw(name, endpoint, {"cond[SALS_STTS_CD::EQ]": "01", f"cond[{address_field}::LIKE]": list(JEONBUK)},
             list(items.values()), basis)


def download_standard(name: str, endpoint: str, key: str, filters: dict[str, str] | None = None) -> None:
    items, page = [], 1
    while True:
        params = {"pageNo": str(page), "numOfRows": "1000", "type": "json", **(filters or {})}
        body = json.loads(fetch(endpoint, {"serviceKey": key, **params}, name))
        if body["header"]["resultCode"] == "03":
            break
        batch = body["body"]["items"]
        batch = batch.get("item", []) if isinstance(batch, dict) else batch
        items += batch
        if len(items) >= int(body["body"]["totalCount"]) or not batch:
            break
        page += 1
    basis = max((item.get("referenceDate") or "" for item in items), default=None)
    save_raw(name, endpoint, filters or {}, items, basis)


def download_senior_centers(key: str) -> None:
    items, page = [], 1
    while True:
        root = ET.fromstring(fetch(SENIOR_API, {"serviceKey": key, "pageNo": str(page), "numOfRows": "1000"}, "경로당"))
        batch = [{child.tag: child.text for child in node} for node in root.iter("list")]
        items += batch
        if len(items) >= int(root.findtext(".//totalCount") or 0) or not batch:
            break
        page += 1
    basis = max((item.get("baseDate") or "" for item in items), default=None)
    save_raw("senior_center", SENIOR_API, {}, items, basis)


def geocode_senior_centers(force: bool) -> None:
    """Geocode each address once; the cache makes reruns cheap and auditable."""
    cache_path = RAW / "senior_center_geocode.json"
    cache = {} if force or not cache_path.exists() else json.loads(cache_path.read_text(encoding="utf-8"))
    addresses = sorted({item["addr"].strip() for item in load_raw("senior_center")["items"] if item.get("addr")})
    pending = [address for address in addresses if address not in cache]
    token, issued = None, 0.0
    for number, address in enumerate(pending, 1):
        if token is None or time.time() - issued > 3000:
            auth = json.loads(fetch(f"{SGIS}/auth/authentication.json", {
                "consumer_key": read_env("SGIS_CONSUMER_KEY"), "consumer_secret": read_env("SGIS_CONSUMER_SECRET")}, "SGIS 인증"))
            token, issued = auth["result"]["accessToken"], time.time()
        result = json.loads(fetch(f"{SGIS}/addr/geocodewgs84.json",
                                  {"accessToken": token, "address": address, "resultcount": "1"}, "SGIS 지오코딩"))
        rows = (result.get("result") or {}).get("resultdata") or []
        cache[address] = rows[0] if result.get("errCd") == 0 and rows else {"error": result.get("errMsg")}
        if number % 200 == 0 or number == len(pending):
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"geocoded {number}/{len(pending)}", flush=True)
        time.sleep(0.05)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _in_jeonbuk(address: object) -> bool:
    return isinstance(address, str) and address.strip().startswith(JEONBUK)


def normalise() -> pd.DataFrame:
    rows: list[dict] = []

    def add(category, identifier, name, lon, lat, address, source, basis):
        rows.append({"facility_id": f"{source}:{identifier}", "category": category, "name": name,
                     "lon": lon, "lat": lat, "address": address, "source": source, "basis": basis})

    for name in ("supermarket", "convenience", "food"):
        raw = load_raw(f"store_{name}")
        for item in raw["items"]:
            code = item.get("indsSclsCd", "")
            if name == "food":
                if code.startswith(EXCLUDED_FOOD[0]) or code == EXCLUDED_FOOD[1]:
                    continue
                category = "cafe" if code == "I21201" else "restaurant"
            else:
                category = name
            add(category, item["bizesId"], item.get("bizesNm"), float(item["lon"]), float(item["lat"]),
                item.get("rdnmAdr") or item.get("lnoAdr"), "sbiz_store", raw["basis"])

    # LOCALDATA licenses each screen (cinema) or re-registered operator (store) separately;
    # one site = one address (or one coordinate).
    for category, name, source, keep in [("large_store", "large_store", "localdata_retail",
                                          lambda item: item.get("BZSTAT_SE_NM") in LARGE_STORE_TYPES),
                                         ("cinema", "cinema", "localdata_cinema", lambda item: True)]:
        raw = load_raw(name)
        sites: dict[str, dict] = {}
        for item in raw["items"]:
            if keep(item) and item.get("CRD_INFO_X"):
                address = item.get("ROAD_NM_ADDR") or item.get("LOTNO_ADDR") or f'{item["CRD_INFO_X"]},{item["CRD_INFO_Y"]}'
                sites.setdefault(address.strip(), item)
        for address, item in sites.items():
            lon, lat = LOCALDATA_CRS.transform(float(item["CRD_INFO_X"]), float(item["CRD_INFO_Y"]))
            add(category, item["MNG_NO"], item.get("BPLC_NM"), lon, lat, address, source, raw["basis"])

    raw = load_raw("library")
    for number, item in enumerate(raw["items"]):
        if item.get("latitude") and item.get("longitude"):
            add("library", f'{item.get("lbrryNm")}:{number}', item.get("lbrryNm"), float(item["longitude"]),
                float(item["latitude"]), item.get("rdnmadr"), "std_library", raw["basis"])

    raw = load_raw("school")
    for item in raw["items"]:
        address = item.get("rdnmadr") or item.get("lnmadr")
        if (_in_jeonbuk(address) and item.get("operSttus") == "운영"
                and item.get("schoolSe") in {"초등학교", "중학교", "고등학교"} and item.get("latitude")):
            add("school", item["schoolId"], item.get("schoolNm"), float(item["longitude"]), float(item["latitude"]),
                address, "std_school", raw["basis"])

    raw = load_raw("senior_center")
    geocodes = json.loads((RAW / "senior_center_geocode.json").read_text(encoding="utf-8"))
    for item in raw["items"]:
        match = geocodes.get((item.get("addr") or "").strip(), {})
        # Accept building-number matches only; a town-level centroid is not a location.
        if match.get("x") and (match.get("road_nm_main_no") not in (None, "null", "")
                               or match.get("jibun_main_no") not in (None, "null", "")):
            add("senior_center", item["dataNo"], item.get("facilityNm"), float(match["x"]), float(match["y"]),
                item.get("addr"), "jeonbuk_senior_center", raw["basis"])

    frame = pd.DataFrame(rows)
    frame = frame[frame.lon.between(124, 132) & frame.lat.between(33, 39)].drop_duplicates("facility_id")
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Download again even if raw files exist")
    args = parser.parse_args()
    key = read_env("DATA_GO_KR_API_KEY")
    jobs = {
        "store_food": lambda: download_stores(key),
        "large_store": lambda: download_localdata("large_store", RETAIL_API, "ROAD_NM_ADDR", key),
        "cinema": lambda: download_localdata("cinema", CINEMA_API, "LOTNO_ADDR", key),
        "library": lambda: download_standard("library", LIBRARY_API, key, {"ctprvnNm": "전북특별자치도"}),
        "school": lambda: download_standard("school", SCHOOL_API, key),
        "senior_center": lambda: download_senior_centers(key),
    }
    for name, job in jobs.items():
        if args.force or not (RAW / f"{name}.json").exists():
            job()
    geocode_senior_centers(args.force)
    frame = normalise()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUTPUT, index=False)
    print(frame.category.value_counts().to_string())
    print(f"Saved {len(frame)} facilities: {OUTPUT}")


if __name__ == "__main__":
    main()

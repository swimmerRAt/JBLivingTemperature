from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

JEONBUK_CENTERS = {
    "전주시": (35.8242, 127.1480),
    "군산시": (35.9677, 126.7366),
    "익산시": (35.9483, 126.9577),
    "정읍시": (35.5699, 126.8560),
    "남원시": (35.4164, 127.3904),
    "김제시": (35.8036, 126.8808),
    "완주군": (35.9047, 127.1622),
    "진안군": (35.7917, 127.4248),
    "무주군": (36.0068, 127.6608),
    "장수군": (35.6473, 127.5216),
    "임실군": (35.6178, 127.2890),
    "순창군": (35.3745, 127.1375),
    "고창군": (35.4358, 126.7019),
    "부안군": (35.7318, 126.7330),
}


def _nfc(value: object) -> str:
    return unicodedata.normalize("NFC", str(value)).strip()


def _number(value: object) -> float:
    if value is None or pd.isna(value):
        return float("nan")
    text = str(value).replace(",", "").strip()
    if text in {"", "-", "nan", "None"}:
        return float("nan")
    return float(text)


def _latest_year(columns: pd.Series) -> str:
    years = [int(float(str(value).split(".")[0])) for value in columns if str(value).strip().isdigit()]
    return str(max(years))


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, header=None, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path, header=None, encoding="cp949")


def _find_source(data_dir: Path, *tokens: str, suffixes: tuple[str, ...] = (".csv",)) -> Path | None:
    candidates = []
    for path in data_dir.iterdir():
        normalized = _nfc(path.name)
        if path.suffix.lower() in suffixes and all(token in normalized for token in tokens):
            candidates.append(path)
    if not candidates:
        return None
    if candidates[0].suffix.lower() == ".zip":
        candidates.sort(key=lambda item: tuple(int(x) for x in re.search(r"(\d{4})\.(\d+)", item.name).groups()) if re.search(r"(\d{4})\.(\d+)", item.name) else (0, 0))
    return candidates[-1]


def discover_sources(data_dir: Path) -> dict[str, Path]:
    data_dir = Path(data_dir)
    sources: dict[str, Path] = {}
    lookups = {
        "population": ("장래_인구변동요인", (".csv",)),
        "population_structure": ("주요_연령계층별", (".csv",)),
        "population_age": ("성및연령별", (".csv",)),
        "vacancy": ("미거주주택", (".csv",)),
        "healthcare_summary": ("시군구별_종별_요양기관", (".csv",)),
        "healthcare": ("전국 병의원 및 약국 현황", (".zip",)),
        "transport_time": ("시도별_대중교통_접근시간", (".csv",)),
    }
    for key, (token, suffixes) in lookups.items():
        found = _find_source(data_dir, token, suffixes=suffixes)
        if found is not None:
            sources[key] = found
    return sources


def _wide_value_column(frame: pd.DataFrame, year: str, start: int = 2) -> int | None:
    for index in range(start, frame.shape[1]):
        if _nfc(frame.iloc[0, index]) == year and _nfc(frame.iloc[1, index]) == "원데이터":
            return index
    return None


def _extract_population(path: Path) -> pd.DataFrame:
    frame = _read_csv(path)
    year = _latest_year(frame.iloc[0, 2:])
    value_col = _wide_value_column(frame, year)
    change_col = next(
        (
            index
            for index in range(2, frame.shape[1])
            if _nfc(frame.iloc[0, index]) == year and _nfc(frame.iloc[1, index]) == "증감률"
        ),
        None,
    )
    rows = []
    for _, row in frame.iloc[2:].iterrows():
        city = _nfc(row.iloc[0])
        if city == "전북" or city not in JEONBUK_CENTERS or _nfc(row.iloc[1]) != "인구(명)":
            continue
        rows.append(
            {
                "city": city,
                "population": _number(row.iloc[value_col]) if value_col is not None else float("nan"),
                "population_change": _number(row.iloc[change_col]) if change_col is not None else float("nan"),
            }
        )
    return pd.DataFrame(rows).drop_duplicates("city")


def _extract_population_structure(path: Path) -> pd.DataFrame:
    frame = _read_csv(path)
    year = _latest_year(frame.iloc[0, 2:])
    value_col = _wide_value_column(frame, year)
    records: dict[str, dict[str, float]] = {}
    for _, row in frame.iloc[2:].iterrows():
        city = _nfc(row.iloc[0])
        if city not in JEONBUK_CENTERS:
            continue
        kind = _nfc(row.iloc[1])
        records.setdefault(city, {})
        if value_col is None:
            continue
        if "유소년인구" in kind:
            records[city]["youth_population"] = _number(row.iloc[value_col])
        elif "고령인구" in kind:
            records[city]["elderly_population"] = _number(row.iloc[value_col])
    return pd.DataFrame([{"city": city, **values} for city, values in records.items()])


def _extract_vacancy(path: Path) -> pd.DataFrame:
    frame = _read_csv(path)
    year = _latest_year(frame.iloc[0, 3:])
    value_col = None
    for index in range(3, frame.shape[1]):
        if _nfc(frame.iloc[0, index]) == year and "빈집" in _nfc(frame.iloc[1, index]) and _nfc(frame.iloc[2, index]) == "원데이터":
            value_col = index
            break
    rows = []
    for _, row in frame.iloc[2:].iterrows():
        if _nfc(row.iloc[0]) != "전북특별자치도":
            continue
        city = _nfc(row.iloc[1])
        if city not in JEONBUK_CENTERS or value_col is None:
            continue
        rows.append({"city": city, "vacant_house_ratio": _number(row.iloc[value_col])})
    return pd.DataFrame(rows).drop_duplicates("city")


def _canonical_healthcare_city(value: object) -> str | None:
    text = _nfc(value)
    if text.startswith("전주"):
        return "전주시"
    return text if text in JEONBUK_CENTERS else None


def _extract_healthcare(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".xlsx") and "/1." in name)
        with archive.open(member) as handle:
            hospitals = pd.read_excel(handle, usecols=["시도코드명", "시군구코드명", "종별코드명"])
    hospitals = hospitals[hospitals["시도코드명"].astype(str).str.contains("전북", na=False)].copy()
    hospitals["city"] = hospitals["시군구코드명"].map(_canonical_healthcare_city)
    hospitals = hospitals.dropna(subset=["city"])
    return hospitals.groupby("city").size().rename("hospital_count").reset_index()


def _extract_transport_baseline(path: Path) -> float:
    frame = _read_csv(path)
    year = _latest_year(frame.iloc[0, 1:])
    value_col = None
    for index in range(1, frame.shape[1]):
        if _nfc(frame.iloc[0, index]) == year and _nfc(frame.iloc[1, index]) == "30분 이상" and _nfc(frame.iloc[2, index]) == "원데이터":
            value_col = index
            break
    if value_col is None:
        return float("nan")
    value = _number(frame.iloc[3, value_col])
    return max(0.0, min(100.0, 100.0 - value * 1.5))


def build_region_panel(data_dir: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    data_dir = Path(data_dir)
    sources = discover_sources(data_dir)
    if "population" not in sources or "population_structure" not in sources or "vacancy" not in sources:
        raise FileNotFoundError("population, population_structure, and vacancy sources are required")

    panel = _extract_population(sources["population"])
    panel = panel.merge(_extract_population_structure(sources["population_structure"]), on="city", how="left")
    panel = panel.merge(_extract_vacancy(sources["vacancy"]), on="city", how="left")
    if "healthcare" in sources:
        panel = panel.merge(_extract_healthcare(sources["healthcare"]), on="city", how="left")
    else:
        panel["hospital_count"] = float("nan")

    panel["youth_ratio"] = panel["youth_population"] / panel["population"] * 100
    panel["elderly_ratio"] = panel["elderly_population"] / panel["population"] * 100
    panel["net_migration"] = float("nan")
    panel["business_count"] = float("nan")
    panel["bus_stop_count"] = float("nan")
    panel["transport_score"] = (
        _extract_transport_baseline(sources["transport_time"])
        if "transport_time" in sources
        else float("nan")
    )
    panel["medical_score"] = panel["hospital_count"] / panel["population"] * 100000
    panel["lat"] = panel["city"].map(lambda city: JEONBUK_CENTERS[city][0])
    panel["lon"] = panel["city"].map(lambda city: JEONBUK_CENTERS[city][1])
    panel = panel.sort_values("city").reset_index(drop=True)
    panel["region_code"] = panel["city"].map(lambda city: f"jeonbuk-{city}")

    warnings: dict[str, str] = {}
    if panel[["lat", "lon"]].isna().any().any():
        warnings["coordinates"] = "지도 좌표가 없는 지역은 지도에 표시되지 않습니다."
    if panel["net_migration"].isna().all():
        warnings["migration"] = "시군구 순이동 원천 데이터는 아직 연결되지 않았습니다."
    quality = {
        "region_count": int(len(panel)),
        "source_count": len(sources),
        "sources": {key: path.name for key, path in sources.items()},
        "warnings": warnings,
    }
    return panel, quality


def load_dashboard_data(data_dir: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    data_dir = Path(data_dir)
    processed = data_dir / "processed" / "region_panel.csv"
    if not processed.exists() and (data_dir / "region_panel.csv").exists():
        processed = data_dir / "region_panel.csv"
    quality_path = processed.parent / "data_quality.json"
    if processed.exists():
        regions = pd.read_csv(processed)
        quality = json.loads(quality_path.read_text()) if quality_path.exists() else {"warnings": {}}
    else:
        regions, quality = build_region_panel(data_dir)
    for column in ("lat", "lon"):
        if column not in regions:
            regions[column] = float("nan")
    if regions[["lat", "lon"]].isna().any().any():
        quality.setdefault("warnings", {})["coordinates"] = "지도 좌표가 없는 지역은 지도에 표시되지 않습니다."
    return regions, quality

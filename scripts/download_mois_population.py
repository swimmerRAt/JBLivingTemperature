"""Download the official annual resident-registration age tables.

The Ministry of the Interior and Safety link is the alternative population
source listed in the project's download-source workbook. The downloaded CSVs
are retained unchanged; the database loader filters Jeonbuk and normalizes
their administrative codes.
"""

from __future__ import annotations

import argparse
import csv
import io
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://jumin.mois.go.kr/downloadCsvAge.do"


def download_year(year: int, output_dir: Path, force: bool = False) -> tuple[Path, bool]:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"mois_resident_age_{year}.csv"
    if target.exists() and not force:
        return target, False

    # xlsStats=3 is the site's "all eup/myeon/dong" CSV selection.
    # Five-year bands preserve the age detail needed for later youth definitions.
    query = urlencode({"searchYearMonth": "year", "xlsStats": "3"})
    body = urlencode(
        {
            "sltOrgType": "1",
            "sltOrgLvl1": "5200000000",
            "sltOrgLvl2": "A",
            "sum": "sum",
            "gender": "gender",  # adds 남/여 columns; 지방소멸위험지수 needs women aged 20-39
            "sltUndefType": "",
            "searchYearStart": str(year),
            "searchMonthStart": "12",
            "searchYearEnd": str(year),
            "searchMonthEnd": "12",
            "sltOrderType": "1",
            "sltOrderValue": "ASC",
            "sltArgTypes": "5",
            "sltArgTypeA": "0",
            "sltArgTypeB": "100",
            "category": "year",
        }
    ).encode("ascii")
    request = Request(
        f"{ENDPOINT}?{query}",
        data=body,
        headers={"User-Agent": "JBLivingTemperature/1.0", "Referer": "https://jumin.mois.go.kr/ageStatMonth.do"},
        method="POST",
    )
    with urlopen(request, timeout=90) as response:
        data = response.read()
        disposition = response.headers.get("Content-Disposition", "")
    if "attachment" not in disposition.lower():
        raise ValueError(f"{year}: server did not return a CSV attachment")
    try:
        first_row = next(csv.reader(io.StringIO(data.decode("cp949"))))
    except (UnicodeDecodeError, StopIteration) as exc:
        raise ValueError(f"{year}: response is not a CP949 CSV") from exc
    if not first_row or first_row[0] != "행정구역" or not any(str(year) in cell for cell in first_row[1:]):
        raise ValueError(f"{year}: unexpected CSV header")
    temporary = target.with_suffix(".csv.part")
    temporary.write_bytes(data)
    temporary.replace(target)
    return target, True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=2008)
    parser.add_argument("--end", type=int, default=2025)
    parser.add_argument("--force", action="store_true", help="re-download files that already exist")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "raw" / "mois")
    arguments = parser.parse_args()
    if arguments.start > arguments.end:
        parser.error("--start must not exceed --end")
    failures = []
    for year in range(arguments.start, arguments.end + 1):
        try:
            path, downloaded = download_year(year, arguments.output_dir, arguments.force)
            print(f"{year}: {'downloaded' if downloaded else 'already present'} {path}", flush=True)
        except Exception as exc:
            failures.append(year)
            print(f"{year}: FAILED {exc}", flush=True)
        if year < arguments.end:
            time.sleep(0.4)
    if failures:
        raise SystemExit(f"Years not downloaded: {failures}")


if __name__ == "__main__":
    main()

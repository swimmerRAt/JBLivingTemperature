"""Collect complete timestamped TAGO snapshots once, or repeatedly at a chosen interval."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval-hours', type=float, help='Repeat until interrupted; at least 1 hour')
    parser.add_argument('--city', help='Optional Jeonbuk city name')
    args = parser.parse_args()
    if args.interval_hours is not None and (not 1 <= args.interval_hours <= 24 * 365):
        parser.error('--interval-hours must be between 1 and 8760')
    while True:
        command = [sys.executable, str(ROOT / 'scripts/download_tago_api.py'), '--history']
        if args.city:
            command += ['--city', args.city]
        completed = subprocess.run(command, cwd=ROOT, check=False)
        status = {'finished_at_utc': datetime.now(timezone.utc).isoformat(),
                  'exit_code': completed.returncode, 'city': args.city}
        log = ROOT / 'data/raw/tago/collection_runs.jsonl'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(status, ensure_ascii=False) + '\n')
        if args.interval_hours is None:
            raise SystemExit(completed.returncode)
        print(f'Collection exit={completed.returncode}; next attempt in {args.interval_hours} hours', flush=True)
        time.sleep(args.interval_hours * 3600)


if __name__ == '__main__':
    main()

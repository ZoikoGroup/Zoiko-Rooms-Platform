#!/usr/bin/env python
"""Download / refresh the offline IP -> country database used by the Find a Room currency detection.

DB-IP Lite Country (CC BY 4.0, free, no account) is published monthly. Run this once, then monthly
(cron / Task Scheduler):    python update_geoip.py

It tries this month's file, then the two before, checks the download opens as a real database, and only then
swaps it in -- a failed download never leaves you without the old file. The page must show the attribution
link "IP geolocation by DB-IP" (the Find a Room page does)."""

from __future__ import annotations

import gzip
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

import httpx
import maxminddb

URL = "https://download.db-ip.com/free/dbip-country-lite-{ym}.mmdb.gz"


def months_to_try(today: date, count: int = 3):
    year, month = today.year, today.month
    for _ in range(count):
        yield f"{year:04d}-{month:02d}"
        month -= 1
        if month == 0:
            year, month = year - 1, 12


def main() -> int:
    target = Path(os.environ.get("GEOIP_DATABASE_PATH") or "data/geoip/dbip-country-lite.mmdb")
    if not target.is_absolute():
        target = Path(__file__).resolve().parent / target
    target.parent.mkdir(parents=True, exist_ok=True)

    for ym in months_to_try(date.today()):
        url = URL.format(ym=ym)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                gz = Path(tmp) / "db.mmdb.gz"
                with httpx.stream("GET", url, timeout=120, follow_redirects=True) as response:
                    if response.status_code != 200:
                        print(f"{ym}: not available ({response.status_code})")
                        continue
                    with gz.open("wb") as out:
                        for chunk in response.iter_bytes():
                            out.write(chunk)
                raw = Path(tmp) / "db.mmdb"
                with gzip.open(gz) as src, raw.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                with maxminddb.open_database(str(raw)) as reader:  # must be a real, readable database
                    if (reader.get("8.8.8.8") or {}).get("country", {}).get("iso_code") != "US":
                        print(f"{ym}: downloaded file failed the sanity check")
                        continue
                staged = target.with_name(target.name + ".new")
                shutil.copyfile(raw, staged)
                try:
                    os.replace(staged, target)  # atomic on Linux, even while the app has the old file open
                except OSError:
                    staged.unlink(missing_ok=True)
                    print(f"{ym}: {target.name} is in use (Windows locks a database the running app has open). "
                          "Stop the backend, run this again, then start it. Linux servers aren't affected.")
                    return 1
            print(f"Updated {target} from DB-IP Lite {ym}. The running backend picks it up automatically.")
            return 0
        except Exception as exc:
            print(f"{ym}: failed ({exc.__class__.__name__}: {exc})")
    print("Could not download the GeoIP database; the existing file (if any) is unchanged.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

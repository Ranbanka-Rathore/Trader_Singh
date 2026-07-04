"""One-shot bulk downloader for the validation window, with retry pass.
Run: PYTHONUTF8=1 python -m backtest.bulk_download
"""
import datetime
import sys

from backtest import bhavcopy


def main():
    start = datetime.date(2024, 1, 1)
    end = datetime.date(2026, 7, 3)
    print(f"PASS 1: {start} -> {end}")
    have = bhavcopy.download_range(start, end, pause_sec=0.8)
    print(f"pass 1 complete: {len(have)} days with data")

    # Retry pass: transient timeouts leave neither zip nor holiday marker
    import os
    missing = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            if not os.path.exists(bhavcopy.zip_path(d)) and \
               not os.path.exists(bhavcopy._holiday_marker(d)):
                missing.append(d)
        d += datetime.timedelta(days=1)
    print(f"PASS 2: retrying {len(missing)} gaps")
    for d in missing:
        try:
            p = bhavcopy.download(d, timeout=60)
            print(f"  {d}: {'OK' if p else 'holiday'}")
        except Exception as e:
            print(f"  {d}: FAILED {str(e)[:70]}")

    # Final census
    n_zip = sum(1 for f in os.listdir(bhavcopy.DATA_DIR) if f.endswith(".zip"))
    n_hol = sum(1 for f in os.listdir(bhavcopy.DATA_DIR) if f.endswith(".holiday"))
    print(f"DONE: {n_zip} session files, {n_hol} holiday markers")


if __name__ == "__main__":
    sys.exit(main())

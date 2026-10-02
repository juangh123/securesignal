"""Read every supported FTSO feed on Coston2 and emit an evidence report.

This is a read-only operational check: it resolves the canonical FtsoV2
contract through FlareContractRegistry, reads each supported feed with
``getFeedById``, and fails if any feed is missing, non-positive, or stale.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import price_provider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        help="write the JSON report to this path instead of stdout",
    )
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=24 * 3600,
        help="fail feeds older than this (default 86400)",
    )
    args = parser.parse_args()

    started_at = time.time()
    entries = []
    failures = []
    for symbol in price_provider.SUPPORTED_SYMBOLS:
        try:
            price, feed_timestamp = price_provider._read_online(symbol)
            # Measure against the read time, not the start of the sweep: fees
            # update every ~90 s, so later reads legitimately have newer
            # timestamps than the first one.
            read_at = time.time()
            age_seconds = int(read_at - feed_timestamp)
            ok = (
                price > 0
                # Tolerate modest future skew from the local clock.
                and age_seconds >= -300
                and age_seconds <= args.max_age_seconds
            )
            entry = {
                "symbol": symbol,
                "ok": ok,
                "price_usd": price,
                "feed_timestamp": feed_timestamp,
                "feed_time_utc": datetime.fromtimestamp(
                    feed_timestamp, tz=timezone.utc
                ).isoformat(),
                "read_time_utc": datetime.fromtimestamp(
                    read_at, tz=timezone.utc
                ).isoformat(),
                "age_seconds": age_seconds,
            }
        except Exception as exc:  # noqa: BLE001 - report every feed failure
            entry = {
                "symbol": symbol,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        entries.append(entry)
        if not entry["ok"]:
            failures.append(entry)

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.time() - started_at, 2),
        "price_source": price_provider.get_price_source(),
        "max_age_seconds": args.max_age_seconds,
        "supported_count": len(price_provider.SUPPORTED_SYMBOLS),
        "ok_count": len(entries) - len(failures),
        "failure_count": len(failures),
        "feeds": entries,
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}: {report['ok_count']}/{report['supported_count']} feeds ok")
    else:
        print(text, end="")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

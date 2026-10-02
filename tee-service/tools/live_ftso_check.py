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

    symbols = list(price_provider.SUPPORTED_SYMBOLS)
    started_at = time.time()
    batch: dict[str, tuple[float, int]] = {}
    batch_error = None
    try:
        batch = price_provider._read_online_many(symbols)
    except Exception as exc:  # noqa: BLE001 - surface and fall back per symbol
        batch_error = f"{type(exc).__name__}: {exc}"

    entries = []
    failures = []
    for symbol in symbols:
        read_at = time.time()
        price = None
        feed_timestamp = None
        error = None
        if symbol in batch:
            price, feed_timestamp = batch[symbol]
        else:
            # If the batch failed, attribute the error per symbol. Batch
            # success always contains every requested symbol.
            try:
                price, feed_timestamp = price_provider._read_online(symbol)
            except Exception as exc:  # noqa: BLE001 - report every feed failure
                error = f"{type(exc).__name__}: {exc}"

        if price is None:
            entry = {
                "symbol": symbol,
                "ok": False,
                "error": error or batch_error or "feed missing from batch response",
            }
        else:
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
        entries.append(entry)
        if not entry["ok"]:
            failures.append(entry)

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.time() - started_at, 2),
        "price_source": price_provider.get_price_source(),
        "batch_error": batch_error,
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

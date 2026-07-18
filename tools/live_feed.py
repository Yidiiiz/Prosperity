"""Live quote feed: polls Yahoo Finance and appends Date,Close rows to a
journal CSV. This daemon is the network side of live mode; the simulation
core only ever reads the journal (colony/arenas/live.py tails it).

The journal is append-only and doubles as the permanent record of the
session's tape — replaying it through the replay arena reproduces the live
run exactly (tools/verify_live_run.py).

Usage:
    python tools/live_feed.py BTC-USD -o data/live_btc.csv --interval 5
    (Ctrl-C to stop; --max-rows for a bounded session)
"""

import argparse
import csv
import datetime
import json
import os
import sys
import time
import urllib.request

QUOTE_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/"
             "{symbol}?range=1d&interval=1m")
MAX_CONSECUTIVE_FAILURES = 10


def quote(symbol):
    url = QUOTE_URL.format(symbol=symbol.upper())
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    return float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="append live quotes to a journal CSV")
    parser.add_argument("symbol", help="ticker, e.g. BTC-USD, SPY")
    parser.add_argument("-o", "--out", required=True, help="journal CSV path (append-only)")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    parser.add_argument("--max-rows", type=int, default=None, help="stop after N rows")
    args = parser.parse_args(argv)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    new_file = not os.path.exists(args.out)
    rows = 0
    failures = 0
    with open(args.out, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["Date", "Close"])
            f.flush()
        try:
            while args.max_rows is None or rows < args.max_rows:
                try:
                    price = quote(args.symbol)
                    failures = 0
                except Exception as exc:  # transient network hiccups: retry
                    failures += 1
                    print(f"poll failed ({failures}/{MAX_CONSECUTIVE_FAILURES}): {exc}",
                          file=sys.stderr)
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        return 1
                    time.sleep(args.interval)
                    continue
                stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(
                    timespec="seconds")
                writer.writerow([stamp, round(price, 4)])
                f.flush()
                rows += 1
                if rows % 12 == 0 or rows == 1:
                    print(f"{stamp}  {args.symbol} {price:,.2f}  ({rows} rows)")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
    print(f"feed stopped after {rows} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

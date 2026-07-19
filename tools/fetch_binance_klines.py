"""Fetch historical Binance klines (candles) into the standard Date,Close CSV.

Public market data, no API key (spec v2 5.1). Default host is Binance's
public data mirror (data-api.binance.vision), which serves market data
without the geo restrictions of the main api.binance.com host. Network code
lives in tools/ only (#25); the simulation replays the file offline.

Usage:
    python tools/fetch_binance_klines.py BTCUSDT 1m --start 2025-07-01 \
        --end 2026-07-01 -o data/btcusdt_1m.csv

Prints the price-series digest at the end so experiments can pin it.
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import sys
import time
import urllib.request

DEFAULT_BASE = "https://data-api.binance.vision"
LIMIT = 1000  # rows per request, the API maximum


def utc_ms(stamp):
    dt = datetime.datetime.fromisoformat(stamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_page(base, symbol, interval, start_ms, end_ms):
    url = (f"{base}/api/v3/klines?symbol={symbol.upper()}&interval={interval}"
           f"&startTime={start_ms}&endTime={end_ms}&limit={LIMIT}")
    req = urllib.request.Request(url, headers={"User-Agent": "darwin-wallet-fetch"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch(base, symbol, interval, start_ms, end_ms, out_path):
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        page = fetch_page(base, symbol, interval, cursor, end_ms)
        if not page:
            break
        for k in page:
            open_time, close = k[0], float(k[4])
            stamp = datetime.datetime.fromtimestamp(
                open_time / 1000, tz=datetime.timezone.utc
            ).isoformat(timespec="seconds").replace("+00:00", "")
            rows.append((stamp, close))
        cursor = page[-1][0] + 1
        print(f"  {len(rows)} rows through {rows[-1][0]}", file=sys.stderr)
        time.sleep(0.15)  # stay far under the public rate limit
    if len(rows) < 2:
        raise SystemExit(f"only {len(rows)} rows for {symbol!r} — nothing to write")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Close"])
        writer.writerows(rows)
    digest = hashlib.sha256(
        ",".join(str(close) for _, close in rows).encode()
    ).hexdigest()[:16]
    print(f"{symbol.upper()} {interval}: {len(rows)} rows"
          f" ({rows[0][0]} .. {rows[-1][0]}) -> {out_path}")
    print(f"close-series digest: {digest}")
    return digest


def main(argv=None):
    parser = argparse.ArgumentParser(description="fetch Binance klines to Date,Close CSV")
    parser.add_argument("symbol", help="e.g. BTCUSDT")
    parser.add_argument("interval", help="e.g. 1m, 5m, 1h, 1d")
    parser.add_argument("--start", required=True, help="UTC ISO date/datetime, inclusive")
    parser.add_argument("--end", required=True, help="UTC ISO date/datetime, exclusive")
    parser.add_argument("-o", "--out", required=True, help="output CSV path")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API base URL")
    args = parser.parse_args(argv)
    fetch(args.base, args.symbol, args.interval, utc_ms(args.start), utc_ms(args.end),
          args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

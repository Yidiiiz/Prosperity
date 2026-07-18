"""Fetch daily historical closes from Yahoo Finance into a local CSV.

This is the ONLY network code in the project; the simulation core replays
the resulting file offline (colony/arenas/replay.py).

Usage:
    python tools/fetch_market_data.py SPY -o data/spy_d.csv
"""

import argparse
import csv
import datetime
import json
import os
import sys
import urllib.request

# period1=0 forces full daily history ("range=max" silently degrades to
# monthly bars for long-lived tickers).
CHART_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/"
             "{symbol}?period1=0&period2=9999999999&interval=1d")


def fetch(symbol, out_path):
    url = CHART_URL.format(symbol=symbol.upper())
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    result = data["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    rows = [
        (datetime.date.fromtimestamp(ts).isoformat(), round(close, 4))
        for ts, close in zip(stamps, closes)
        if close is not None
    ]
    if len(rows) < 100:
        raise SystemExit(f"only {len(rows)} usable rows for {symbol!r}; refusing tiny dataset")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Close"])
        writer.writerows(rows)
    print(f"{symbol.upper()}: {len(rows)} daily closes ({rows[0][0]} .. {rows[-1][0]})"
          f" -> {out_path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="fetch daily closes from Yahoo Finance")
    parser.add_argument("symbol", help="ticker, e.g. SPY, AAPL, BTC-USD")
    parser.add_argument("-o", "--out", required=True, help="output CSV path")
    args = parser.parse_args(argv)
    fetch(args.symbol, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

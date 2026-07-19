"""Replay arena: real historical prices from a local CSV, one row per tick.

The CSV is fetched ONCE by the tools/ fetchers; the simulation core never
touches the network. Replay is exogenous and deterministic by construction —
the past is already written, so `step` ignores the RNG and the same CSV
always yields the same price path.

`lot_denominator` scales the asset down so one lot is an affordable slice
(lot price = close * 1,000,000 / denominator micro-dollars, floored at 1 u).
History ends: the arena reports `exhausted()` and the run loop stops cleanly.
"""

import csv
import datetime
import hashlib


class ArenaExhausted(Exception):
    """Raised if step() is called past the end of the price history."""


def parse_utc(stamp):
    """Date,Close stamps to unix seconds: bare dates are UTC midnight, naive
    datetimes are taken as UTC (the fetchers and feed write UTC ISO)."""
    dt = datetime.datetime.fromisoformat(stamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def to_price_u(close, denominator):
    return max(1, round(close * 1_000_000 / denominator))


class Replay:
    def __init__(self, arena_cfg):
        self.name = arena_cfg["name"]
        self.csv_path = arena_cfg["csv"]
        self.denominator = arena_cfg.get("lot_denominator", 1)
        times, closes = self._read_rows(self.csv_path)
        if len(closes) < 2:
            raise ValueError(f"{self.csv_path}: need at least 2 price rows")
        self._times = times
        self._prices = [to_price_u(c, self.denominator) for c in closes]
        self._digest = hashlib.sha256(
            ",".join(map(str, self._prices)).encode()
        ).hexdigest()[:16]
        self._idx = 0

    @staticmethod
    def _read_rows(path):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            close = next((c for c in fields if c.strip().lower() == "close"), None)
            date = next((c for c in fields if c.strip().lower() == "date"), None)
            if close is None:
                raise ValueError(f"{path}: no 'Close' column in header {fields}")
            times, closes = [], []
            for row in reader:
                if not row.get(close):
                    continue
                times.append(parse_utc(row[date]) if date and row.get(date) else 0)
                closes.append(float(row[close]))
            return times, closes

    def step(self, rng):
        if self.exhausted():
            raise ArenaExhausted(f"{self.csv_path}: price history exhausted")
        self._idx += 1

    def price(self):
        return self._prices[self._idx]

    def utc(self):
        return self._times[self._idx]

    def history(self, n):
        return self._prices[max(0, self._idx - n + 1): self._idx + 1]

    def regime_kind(self):
        return "replay"

    def exhausted(self):
        return self._idx + 1 >= len(self._prices)

    def ticks_total(self):
        """Ticks of history available from the start (rows - 1)."""
        return len(self._prices) - 1

    def get_state(self):
        return {"idx": self._idx, "digest": self._digest}

    def set_state(self, state):
        if state["digest"] != self._digest:
            raise RuntimeError(
                f"{self.csv_path} does not match the data this run started with"
                " (resume requires the identical CSV)"
            )
        self._idx = state["idx"]

"""Replay arena: real historical prices from a local CSV, one row per tick.

The CSV is fetched ONCE by tools/fetch_market_data.py; the simulation core
never touches the network. Replay is exogenous and deterministic by
construction — the past is already written, so `step` ignores the RNG and
the same CSV always yields the same price path.

`lot_denominator` scales the asset down so one lot is an affordable slice
(lot price = close * 100 / denominator, floored at 1 cent). History ends:
the arena reports `exhausted()` and the run loop stops cleanly.
"""

import csv
import hashlib


class ArenaExhausted(Exception):
    """Raised if step() is called past the end of the price history."""


class Replay:
    def __init__(self, arena_cfg):
        self.name = arena_cfg["name"]
        self.csv_path = arena_cfg["csv"]
        self.denominator = arena_cfg.get("lot_denominator", 1)
        closes = self._read_closes(self.csv_path)
        if len(closes) < 2:
            raise ValueError(f"{self.csv_path}: need at least 2 price rows")
        self._prices = [max(1, round(c * 100 / self.denominator)) for c in closes]
        self._digest = hashlib.sha256(
            ",".join(map(str, self._prices)).encode()
        ).hexdigest()[:16]
        self._idx = 0

    @staticmethod
    def _read_closes(path):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            field = next((c for c in reader.fieldnames or [] if c.strip().lower() == "close"), None)
            if field is None:
                raise ValueError(f"{path}: no 'Close' column in header {reader.fieldnames}")
            return [float(row[field]) for row in reader if row.get(field)]

    def step(self, rng):
        if self.exhausted():
            raise ArenaExhausted(f"{self.csv_path}: price history exhausted")
        self._idx += 1

    def price(self):
        return self._prices[self._idx]

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

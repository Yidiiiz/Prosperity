"""Live arena: real-time prices tailed from a journal CSV that
tools/live_feed.py appends to. Paper trading only — the colony reads prices;
it never sends an order anywhere.

The core stays offline: the feed daemon (the network side) writes the
journal, this arena only reads the file. One appended row = one tick, so a
live session is wall-clock paced by the feed and — crucially — REPRODUCIBLE
after the fact: replaying the same journal through the Replay arena with the
same config and seed yields a byte-identical ledger
(tools/verify_live_run.py proves it).
"""

import csv
import hashlib
import io
import time

from .replay import parse_utc, to_price_u


def _journal_rows(path):
    """Read Date,Close rows, tolerating a partial final line (the feed may
    be mid-append). Returns ([], []) if the file does not exist yet."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return [], []
    if text and not text.endswith("\n"):
        text = text[: text.rfind("\n") + 1]  # drop the torn tail line
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    close = next((c for c in fields if c.strip().lower() == "close"), None)
    date = next((c for c in fields if c.strip().lower() == "date"), None)
    if close is None:
        return [], []
    times, closes = [], []
    for row in reader:
        if not row.get(close):
            continue
        times.append(parse_utc(row[date]) if date and row.get(date) else 0)
        closes.append(float(row[close]))
    return times, closes


def _digest(prices):
    return hashlib.sha256(",".join(map(str, prices)).encode()).hexdigest()[:16]


class Live:
    def __init__(self, arena_cfg):
        self.name = arena_cfg["name"]
        self.csv_path = arena_cfg["csv"]
        self.denominator = arena_cfg.get("lot_denominator", 1)
        self.timeout = arena_cfg.get("poll_timeout_seconds", 120)
        self._prices = []
        self._times = []
        self._idx = 0
        self._load()
        if not self._prices:
            raise ValueError(
                f"{self.csv_path}: journal is empty — start the feed first"
                " (python tools/live_feed.py <SYMBOL> -o <journal>)"
            )

    def _load(self):
        times, closes = _journal_rows(self.csv_path)
        if len(closes) > len(self._prices):
            self._prices = [to_price_u(c, self.denominator) for c in closes]
            self._times = times

    def wait_for_data(self):
        """Block until an unconsumed row exists. False = the feed went stale
        (no new row within poll_timeout_seconds); the run loop stops cleanly."""
        deadline = time.monotonic() + self.timeout
        while self._idx + 1 >= len(self._prices):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)
            self._load()
        return True

    def step(self, rng):  # rng unused: the tape is written by the market
        self._load()
        if self._idx + 1 >= len(self._prices):
            raise RuntimeError("no unconsumed journal row; wait_for_data first")
        self._idx += 1

    def price(self):
        return self._prices[self._idx]

    def utc(self):
        return self._times[self._idx]

    def history(self, n):
        return self._prices[max(0, self._idx - n + 1): self._idx + 1]

    def regime_kind(self):
        return "live"

    def exhausted(self):
        return False  # a live feed may always produce more

    def get_state(self):
        return {"idx": self._idx, "digest": _digest(self._prices[: self._idx + 1])}

    def set_state(self, state):
        self._load()
        idx = state["idx"]
        if idx >= len(self._prices) or _digest(self._prices[: idx + 1]) != state["digest"]:
            raise RuntimeError(
                f"{self.csv_path} does not contain the prefix this run consumed"
                " (resume requires the same, append-only journal)"
            )
        self._idx = idx

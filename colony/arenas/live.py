"""Live arena: real-time prices tailed from the journal that
tools/live_feed.py appends to. Paper trading only — the colony reads prices;
it never sends an order anywhere.

The core stays offline: the feed daemon (the network side) writes the
journal, this arena only reads files. One appended row = one tick, so a
live session is wall-clock paced by the feed and — crucially — REPRODUCIBLE
after the fact: replaying the same journal through the Replay arena with the
same config and seed yields a byte-identical ledger (#31).

v2 (spec v2 5.3): the journal may be a DIRECTORY of daily segments
(YYYY-MM-DD.csv, UTC boundaries) chained in date order and consumed across
boundaries transparently. Completed segments are read once and cached; only
the growing tail segment is re-read. The resume digest becomes the list of
fully-consumed segment digests plus the prefix digest of the segment the
cursor is in. Single-file mode (`csv`) keeps the exact v1 semantics.
"""

import csv
import hashlib
import io
import time
from pathlib import Path

from .replay import parse_utc, to_price_u


def _read_rows(text):
    """Parse Date,Close rows from journal text, tolerating a torn tail line
    (the feed may be mid-append). Returns (times, closes)."""
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


def _read_file(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return _read_rows(f.read())
    except FileNotFoundError:
        return [], []


def _digest(prices):
    return hashlib.sha256(",".join(map(str, prices)).encode()).hexdigest()[:16]


class Live:
    def __init__(self, arena_cfg):
        self.name = arena_cfg["name"]
        self.csv_path = arena_cfg.get("csv")
        self.journal_dir = arena_cfg.get("journal")
        if bool(self.csv_path) == bool(self.journal_dir):
            raise ValueError("live arena needs exactly one of 'csv' (single file)"
                             " or 'journal' (directory of daily segments)")
        self.denominator = arena_cfg.get("lot_denominator", 1)
        self.timeout = arena_cfg.get("poll_timeout_seconds", 120)
        # chained view: parallel price/time arrays plus per-segment row counts
        self._prices = []
        self._times = []
        self._segments = []  # [name, rows] per segment, in date order
        self._done = 0  # segments fully read and cached (all but the tail)
        self._idx = 0
        self._load()
        if not self._prices:
            where = self.csv_path or self.journal_dir
            raise ValueError(
                f"{where}: journal is empty — start the feed first"
                " (python tools/live_feed.py <SYMBOL> --journal <dir>)"
            )

    # ------------------------------------------------------------- loading

    def _segment_names(self):
        return sorted(p.name for p in Path(self.journal_dir).glob("*.csv"))

    def _load(self):
        if self.csv_path:
            times, closes = _read_file(self.csv_path)
            if len(closes) > len(self._prices):
                self._prices = [to_price_u(c, self.denominator) for c in closes]
                self._times = times
                self._segments = [[Path(self.csv_path).name, len(closes)]]
            return
        names = self._segment_names()
        cached = [s[0] for s in self._segments[: self._done]]
        if names[: len(cached)] != cached:
            raise RuntimeError(f"{self.journal_dir}: consumed segments changed on disk")
        # every segment before the newest is complete: read once, cache
        prices = self._prices[: sum(s[1] for s in self._segments[: self._done])]
        times = self._times[: len(prices)]
        segments = self._segments[: self._done]
        for name in names[self._done:]:
            seg_times, seg_closes = _read_file(Path(self.journal_dir) / name)
            seg_prices = [to_price_u(c, self.denominator) for c in seg_closes]
            prices.extend(seg_prices)
            times.extend(seg_times)
            segments.append([name, len(seg_prices)])
        if len(prices) < len(self._prices):
            raise RuntimeError(f"{self.journal_dir}: journal shrank — it must be append-only")
        self._prices, self._times, self._segments = prices, times, segments
        self._done = max(0, len(segments) - 1)

    # ------------------------------------------------------------ protocol

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
        if self._idx + 1 >= len(self._prices):
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

    # -------------------------------------------------------------- resume

    def _cursor(self):
        """(segments fully behind the cursor, rows consumed in the next one)."""
        consumed = self._idx + 1
        full = 0
        for name, rows in self._segments:
            if consumed < rows:
                break
            consumed -= rows
            full += 1
        return full, consumed

    def get_state(self):
        if self.csv_path:
            return {"idx": self._idx, "digest": _digest(self._prices[: self._idx + 1])}
        full, offset = self._cursor()
        start = 0
        digests = []
        for name, rows in self._segments[:full]:
            digests.append([name, _digest(self._prices[start: start + rows])])
            start += rows
        state = {"idx": self._idx, "segments": digests}
        if offset:
            state["tail"] = [self._segments[full][0],
                             _digest(self._prices[start: start + offset]), offset]
        return state

    def set_state(self, state):
        self._load()
        idx = state["idx"]
        if self.csv_path:
            if idx >= len(self._prices) or _digest(self._prices[: idx + 1]) != state["digest"]:
                raise RuntimeError(
                    f"{self.csv_path} does not contain the prefix this run consumed"
                    " (resume requires the same, append-only journal)"
                )
            self._idx = idx
            return
        if idx >= len(self._prices):
            raise RuntimeError(f"{self.journal_dir}: journal is shorter than the consumed prefix")
        names = {name: i for i, (name, _) in enumerate(self._segments)}
        start = 0
        starts = {}
        for name, rows in self._segments:
            starts[name] = start
            start += rows
        for name, digest in state["segments"]:
            if name not in names:
                raise RuntimeError(f"{self.journal_dir}: consumed segment {name} is missing")
            rows = self._segments[names[name]][1]
            if _digest(self._prices[starts[name]: starts[name] + rows]) != digest:
                raise RuntimeError(f"{self.journal_dir}: segment {name} does not match"
                                   " the data this run consumed")
        if "tail" in state:
            name, digest, offset = state["tail"]
            if name not in names:
                raise RuntimeError(f"{self.journal_dir}: consumed segment {name} is missing")
            begin = starts[name]
            if _digest(self._prices[begin: begin + offset]) != digest:
                raise RuntimeError(f"{self.journal_dir}: segment {name} does not contain"
                                   " the prefix this run consumed")
        self._idx = idx

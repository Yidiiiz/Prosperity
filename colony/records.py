"""Append-only plain-text records (spec 9.2): the permanent lab notebook.

The database is the source of truth; records are the greppable audit trail
that survives db resets and gets committed to git. Creating a record fails
loudly if the path already exists — never overwrite.
"""

import datetime
import json
import subprocess
from pathlib import Path

KINDS = ("runs", "experiments", "tests")


def _utc_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _git_describe():
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


class Record:
    def __init__(self, root, kind, name, config=None, seed=None, extra_header=""):
        if kind not in KINDS:
            raise ValueError(f"unknown record kind {kind!r}")
        self.root = Path(root)
        self.kind = kind
        directory = self.root / kind
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{name}_{_utc_stamp()}.txt"
        if self.path.exists():
            raise FileExistsError(f"record already exists, refusing to overwrite: {self.path}")
        header = [
            f"record: {kind}/{self.path.name}",
            f"utc: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}",
            f"git: {_git_describe()}",
            f"rng_seed: {seed if seed is not None else 'n/a'}",
            f"config: {json.dumps(config) if config is not None else 'n/a'}",
        ]
        if extra_header:
            header.append(extra_header)
        header.append("=" * 72)
        self.path.write_text("\n".join(header) + "\n", encoding="utf-8")

    def append(self, text):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")

    def section(self, title, body):
        self.append(f"\n--- {title} " + "-" * max(1, 60 - len(title)))
        self.append(body)

    def finish(self, headline):
        """Append the closing line and index this record in INDEX.txt."""
        self.append(f"\n=== RESULT: {headline}")
        index = self.root / "INDEX.txt"
        rel = self.path.relative_to(self.root).as_posix()
        with open(index, "a", encoding="utf-8") as f:
            f.write(f"{_utc_stamp()} | {self.kind} | {rel} | {headline}\n")

"""Append-only plain-text records (spec 9.2): the permanent lab notebook.

The database is the source of truth; records are the greppable audit trail
that survives db resets and gets committed to git. Creating a record fails
loudly if the path already exists — never overwrite.

v2: filenames are collision-proof (spec v2 1.5) — UTC stamp with
milliseconds, `_seed<N>` when a seed applies, `_p<pid>` always. CRITICAL
records are prefixed `!! ` in INDEX.txt so `grep '^!!' records/INDEX.txt`
is the incident query (spec v2 9.4).
"""

import datetime
import json
import os
import subprocess
from pathlib import Path

KINDS = ("runs", "experiments", "tests", "feed", "audits")


def _utc_stamp():
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}Z"


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
        seed_part = f"_seed{seed}" if seed is not None else ""
        self.path = directory / f"{name}_{_utc_stamp()}{seed_part}_p{os.getpid()}.txt"
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

    def finish(self, headline, level="INFO"):
        """Append the closing line and index this record in INDEX.txt.
        level="CRITICAL" marks the INDEX line with the `!! ` incident prefix."""
        self.append(f"\n=== RESULT: {headline}")
        index = self.root / "INDEX.txt"
        rel = self.path.relative_to(self.root).as_posix()
        prefix = "!! " if level == "CRITICAL" else ""
        with open(index, "a", encoding="utf-8") as f:
            f.write(f"{prefix}{_utc_stamp()} | {self.kind} | {rel} | {headline}\n")

"""Replay-twin audit: prove a live session is reproducible after the fact.

Reads a live-run database, rebuilds the identical run OFFLINE by replaying
the session's journal through the replay arena (same config, same seed, same
tick count), and compares the two ledgers row by row. Byte-identical ledgers
mean the live run inherits every determinism guarantee of the simulator —
the wall clock only decided WHEN ticks happened, never what they did.

Usage:
    python tools/verify_live_run.py --db colony_live.db
"""

import argparse
import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from colony import db, orchestrator  # noqa: E402
from colony.config import validate  # noqa: E402


def ledger_hash(con):
    h = hashlib.sha256()
    n = 0
    for row in con.execute(
        "SELECT tick, debit_account, credit_account, amount_cents, memo"
        " FROM ledger ORDER BY seq"
    ):
        h.update(repr(tuple(row)).encode())
        n += 1
    return h.hexdigest(), n


def main(argv=None):
    parser = argparse.ArgumentParser(description="prove a live run replays identically")
    parser.add_argument("--db", required=True, help="live-run database")
    args = parser.parse_args(argv)

    live = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    live.row_factory = sqlite3.Row
    run = live.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if run is None:
        print("no run in that database", file=sys.stderr)
        return 2
    cfg = json.loads(run["config_json"])
    if cfg["arena"].get("kind") != "live":
        print(f"not a live run (arena kind {cfg['arena'].get('kind', 'petri')!r})",
              file=sys.stderr)
        return 2
    ticks = run["last_tick"]
    audited = live.execute(
        "SELECT COUNT(*) FROM agents WHERE death_cause = 'horizon'"
    ).fetchone()[0] > 0

    twin_cfg = copy.deepcopy(cfg)
    twin_cfg["arena"] = {
        "kind": "replay",
        "name": cfg["arena"]["name"],
        "csv": cfg["arena"]["csv"],
        "lot_denominator": cfg["arena"].get("lot_denominator", 1),
    }
    validate(twin_cfg)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
        twin = db.connect(Path(workdir) / "twin.db")
        orch = orchestrator.init_colony(twin, twin_cfg)
        executed = orch.run(ticks)
        if audited:
            orch.wind_down()
        live_hash, live_rows = ledger_hash(live)
        twin_hash, twin_rows = ledger_hash(twin)
        twin.close()
    live.close()

    print(f"live run:    tick {ticks}, {live_rows} ledger rows, sha256 {live_hash[:16]}")
    print(f"replay twin: tick {executed}, {twin_rows} ledger rows, sha256 {twin_hash[:16]}")
    if live_hash == twin_hash:
        print("VERIFIED: the live session replays byte-identically from its journal")
        return 0
    print("MISMATCH: the live ledger does not replay from its journal", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

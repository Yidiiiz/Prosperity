"""Continuous verification (spec v2 6.5): the replay-twin audit.

A live colony's journal is its permanent tape. This module rebuilds the
colony OFFLINE by replaying the journal's closed daily segments through the
replay arena with the same config and seed, and compares ledger hashes
against the live database (#31). A pass proves the live run inherits every
determinism guarantee of the simulator; a MISMATCH is a CRITICAL incident —
an alarm about the past, never a reason to stop the present (the daemon
keeps running).

State lives in records/audits/state.json:
  {"segments": {"YYYY-MM-DD.csv": {"ok": bool, "utc": iso, "ticks": n}},
   "critical": bool}
`critical` latches true on any mismatch until an operator clears it
(`colony daemon clear-audit`).
"""

import copy
import hashlib
import json
import tempfile
from pathlib import Path

from . import db, orchestrator, records
from .config import validate


def ledger_hash(con, up_to_tick=None):
    where = "" if up_to_tick is None else f" WHERE tick <= {int(up_to_tick)}"
    h = hashlib.sha256()
    n = 0
    for row in con.execute(
        "SELECT tick, debit_account, credit_account, amount_u, memo"
        f" FROM ledger{where} ORDER BY seq"
    ):
        h.update(repr(tuple(row)).encode())
        n += 1
    return h.hexdigest(), n


def state_path(records_root):
    return Path(records_root) / "audits" / "state.json"


def load_state(records_root):
    try:
        return json.loads(state_path(records_root).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"segments": {}, "critical": False}


def save_state(records_root, state):
    path = state_path(records_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(path)


def clear_critical(records_root):
    state = load_state(records_root)
    state["critical"] = False
    save_state(records_root, state)


def _segment_rows(path):
    """Row count and (times, gaps) of one segment file."""
    from .arenas.live import _read_file
    times, closes = _read_file(path)
    return times, closes


def _gap_ranges(times, tick_seconds):
    gaps = []
    for a, b in zip(times, times[1:]):
        if b - a > tick_seconds:
            gaps.append((a + tick_seconds, b - tick_seconds))
    return gaps


def audit(db_path, records_root="records", utcnow=None):
    """Audit every closed, fully-consumed, not-yet-audited segment: replay
    the journal prefix through those segments offline, compare ledger
    hashes. Returns (ok, detail) — ok is False only on a real MISMATCH.
    """
    import datetime
    live = db.connect(db_path, readonly=True)
    try:
        run = live.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if run is None:
            return True, "no run to audit"
        cfg = json.loads(run["config_json"])
        arena_cfg = cfg["arena"]
        if arena_cfg.get("kind") != "live" or not arena_cfg.get("journal"):
            return True, "not a segmented live run; nothing to audit"
        last_tick = run["last_tick"]
        consumed_rows = last_tick + 1

        journal = Path(arena_cfg["journal"])
        names = sorted(p.name for p in journal.glob("*.csv"))
        closed = names[:-1]  # the newest segment is the growing tail
        state = load_state(records_root)

        # find closed segments fully consumed by the live run, in order
        rows_seen = 0
        auditable = []       # [(name, rows_cum_through_segment, times)]
        all_times = []
        for name in closed:
            times, closes = _segment_rows(journal / name)
            rows_seen += len(closes)
            all_times.extend(times)
            if rows_seen <= consumed_rows:
                auditable.append((name, rows_seen))
        todo = [name for name, _ in auditable if name not in state["segments"]]
        if not todo:
            return True, "no unaudited closed segments"

        # the twin replays from genesis through the LAST auditable segment
        end_rows = auditable[-1][1]
        ticks = end_rows - 1
        twin_cfg = copy.deepcopy(cfg)
        record = records.Record(
            records_root, "audits", f"segment_audit_{auditable[-1][0].removesuffix('.csv')}",
            config={"segments": todo, "ticks": ticks}, seed=cfg["rng_seed"],
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
            concat = Path(workdir) / "tape.csv"
            with open(concat, "w", newline="", encoding="utf-8") as out:
                out.write("Date,Close\n")
                written = 0
                for name in closed:
                    for line in (journal / name).read_text(encoding="utf-8").splitlines()[1:]:
                        if written >= end_rows:
                            break
                        if line.strip():
                            out.write(line + "\n")
                            written += 1
            twin_cfg["arena"] = {
                "kind": "replay", "name": arena_cfg["name"], "csv": str(concat),
                "lot_denominator": arena_cfg.get("lot_denominator", 1),
                "tick_seconds": arena_cfg.get("tick_seconds", 1),
            }
            twin_cfg["flush_every"] = 100  # flush cadence proven ledger-neutral
            validate(twin_cfg)
            twin = db.connect(Path(workdir) / "twin.db")
            orch = orchestrator.init_colony(twin, twin_cfg)
            executed = orch.run(ticks)
            live_hash, live_rows = ledger_hash(live, up_to_tick=ticks)
            twin_hash, twin_rows = ledger_hash(twin)
            twin.close()

        gaps = _gap_ranges(all_times[:end_rows], arena_cfg.get("tick_seconds", 1))
        now = (utcnow or datetime.datetime.now(datetime.timezone.utc)).isoformat(
            timespec="seconds")
        ok = live_hash == twin_hash and executed == ticks
        record.section(
            "replay twin",
            f"segments audited: {', '.join(todo)}\n"
            f"ticks replayed: {executed} / {ticks}\n"
            f"live ledger:  {live_rows} rows, sha256 {live_hash[:16]}\n"
            f"twin ledger:  {twin_rows} rows, sha256 {twin_hash[:16]}\n"
            f"feed gaps in range: {len(gaps)}"
            + ("".join(f"\n  gap: {a}..{b}" for a, b in gaps[:50])),
        )
        for name in todo:
            state["segments"][name] = {"ok": ok, "utc": now, "ticks": ticks}
        if ok:
            record.finish(f"PASS — {len(todo)} segment(s) replay byte-identically")
        else:
            state["critical"] = True
            record.finish(
                f"CRITICAL — ledger mismatch on {', '.join(todo)}"
                f" (live {live_hash[:16]} != twin {twin_hash[:16]})",
                level="CRITICAL",
            )
        save_state(records_root, state)
        return ok, f"audited {todo}: {'PASS' if ok else 'MISMATCH'}"
    finally:
        live.close()

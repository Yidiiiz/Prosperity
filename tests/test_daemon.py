"""The daemon (spec v2 section 6) and continuous audit (6.5/7): supervision
with a fake feed subprocess, pid guarding, gap accounting, health surface,
hard-kill resume, and the replay-twin audit's PASS and CRITICAL paths —
all offline."""

import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from colony import audit, db, orchestrator
from colony.daemon import Daemon, pid_path
from tests.conftest import make_cfg
from tests.test_determinism import ledger_hash
from tests.test_feeds import write_segment, seg_rows

UTC = datetime.timezone.utc

# a feed stand-in: appends one row per --step seconds to today's segment
FAKE_FEED = r"""
import sys, time, datetime, pathlib
directory = pathlib.Path(sys.argv[1]); step = float(sys.argv[2]); n = int(sys.argv[3])
directory.mkdir(parents=True, exist_ok=True)
day = "2026-07-18"
path = directory / (day + ".csv")
if not path.exists():
    path.write_text("Date,Close\n", encoding="utf-8")
start = sum(1 for _ in open(path)) - 1
for i in range(start, start + n):
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{day}T{i // 3600:02d}:{i % 3600 // 60:02d}:{i % 60:02d},{100 + i * 0.25}\n")
    time.sleep(step)
"""


def live_cfg(journal_dir, feed_cmd=None, **overrides):
    cfg = dict(
        arena={"kind": "live", "name": "sim_live", "journal": str(journal_dir),
               "csv": None, "regimes": None, "tick_seconds": 1,
               "poll_timeout_seconds": 5, "lot_denominator": 100},
        venue={"fill_delay_ticks": 1},
        lifecycle={"max_age_days": 31, "stagnation_seconds": 200,
                   "breed_cooldown_seconds": 50, "solo_breed_patience_seconds": 10,
                   "snapshot_every_seconds": 5, "checkpoint_every_seconds": 2000},
        rent_min_u=0,
        debug=False,
    )
    cfg.update(overrides)
    made = make_cfg(**cfg)
    if feed_cmd:
        made["feed"] = {"cmd": feed_cmd, "symbol": "SIM"}
    return made


def init_db(tmp_path, cfg, name="live.db"):
    con = db.connect(tmp_path / name)
    orchestrator.init_colony(con, cfg)
    con.close()
    return tmp_path / name


# ---------------------------------------------------------------- supervision

def test_daemon_ticks_with_supervised_feed_and_writes_health(tmp_path):
    journal = tmp_path / "journal"
    feed_cmd = [sys.executable, "-c", FAKE_FEED, str(journal), "0.02", "500"]
    cfg = live_cfg(journal, feed_cmd)
    write_segment(journal, "2026-07-18", seg_rows(3))  # seed rows so init works
    db_path = init_db(tmp_path, cfg)
    d = Daemon(db_path, cfg, records_root=str(tmp_path / "records"))
    ticks = d.run(max_ticks=25, install_signals=False)
    assert ticks == 25
    assert not pid_path(db_path).exists()  # released on clean exit
    health = json.loads(Path(f"{db_path}.health.json").read_text())
    assert health["tick"] >= 25
    assert health["flush_every"] == 1
    assert health["feed"]["last_row_utc"] > 0
    assert health["last_invariant_check_utc"]
    logs = list((tmp_path / "records" / "feed").glob("feed_*.log"))
    assert logs, "feed output must tee into records/feed/"


def test_daemon_pid_guard_blocks_double_start_and_reclaims_stale(tmp_path):
    journal = tmp_path / "journal"
    write_segment(journal, "2026-07-18", seg_rows(3))
    cfg = live_cfg(journal)
    db_path = init_db(tmp_path, cfg)
    import os
    pid_path(db_path).write_text(str(os.getpid()))  # a LIVE pid: refuse
    d = Daemon(db_path, cfg, records_root=str(tmp_path / "records"))
    with pytest.raises(RuntimeError, match="already running"):
        d.run(max_ticks=1, install_signals=False)
    pid_path(db_path).write_text("999999999")  # dead pid: reclaim and run
    d2 = Daemon(db_path, cfg, records_root=str(tmp_path / "records"))
    assert d2.run(max_ticks=2, install_signals=False) == 2


def test_daemon_counts_feed_gaps_without_erroring(tmp_path):
    journal = tmp_path / "journal"
    rows = seg_rows(5) + [("00:00:09", 105.0), ("00:00:10", 106.0)]  # 4s hole
    write_segment(journal, "2026-07-18", rows)
    cfg = live_cfg(journal)
    db_path = init_db(tmp_path, cfg)
    d = Daemon(db_path, cfg, records_root=str(tmp_path / "records"))
    assert d.run(max_ticks=6, install_signals=False) == 6
    assert d.gap_count == 1  # a gap is counted, never an error


def test_daemon_hard_kill_resumes_byte_identically(tmp_path):
    """6.2: kill -9 mid-run, restart, and the ledger equals a straight
    uninterrupted consumption of the same journal."""
    journal = tmp_path / "journal"
    write_segment(journal, "2026-07-18", seg_rows(120))
    cfg = live_cfg(journal)
    cfg_path = tmp_path / "cfg.json"
    dump = {k: v for k, v in cfg.items() if not k.startswith("_")}
    cfg_path.write_text(json.dumps(dump), encoding="utf-8")
    db_path = init_db(tmp_path, cfg, "killed.db")

    proc = subprocess.Popen(
        [sys.executable, "-m", "colony", "--db", str(db_path), "daemon",
         "--config", str(cfg_path), "--max-ticks", "119", "--port", "0"],
        cwd=str(Path(__file__).resolve().parent.parent),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    mid_db = db.connect(db_path, readonly=True)
    while time.monotonic() < deadline:
        tick = mid_db.execute("SELECT MAX(last_tick) FROM runs").fetchone()[0] or 0
        if tick >= 30:
            break
        time.sleep(0.1)
    proc.kill()  # taskkill /F equivalent: no warning, no cleanup
    proc.wait(timeout=30)
    mid_db.close()
    assert tick >= 30, "daemon never got going before the kill"

    pid_path(db_path).unlink(missing_ok=True)  # the kill left it behind
    d = Daemon(db_path, cfg, records_root=str(tmp_path / "records"))
    d.run(max_ticks=200, install_signals=False)  # consumes to journal end (119)

    straight_cfg = live_cfg(journal)
    straight_db = init_db(tmp_path, straight_cfg, "straight.db")
    d2 = Daemon(straight_db, straight_cfg, records_root=str(tmp_path / "records2"))
    d2.run(max_ticks=119, install_signals=False)

    con_a = db.connect(db_path, readonly=True)
    con_b = db.connect(straight_db, readonly=True)
    assert con_a.execute("SELECT MAX(last_tick) FROM runs").fetchone()[0] == 119
    assert ledger_hash(con_a) == ledger_hash(con_b)
    con_a.close(), con_b.close()


# --------------------------------------------------------------------- audit

def audited_colony(tmp_path):
    """A live db that consumed two closed segments and part of the tail."""
    journal = tmp_path / "journal"
    write_segment(journal, "2026-07-16", seg_rows(30, base=100))
    write_segment(journal, "2026-07-17", seg_rows(30, base=130))
    write_segment(journal, "2026-07-18", seg_rows(10, base=160))
    cfg = live_cfg(journal)
    db_path = init_db(tmp_path, cfg)
    d = Daemon(db_path, cfg, records_root=str(tmp_path / "records"))
    d.run(max_ticks=65, install_signals=False)  # through both closed segments
    return db_path, journal


def test_replay_twin_audit_passes_and_records(tmp_path):
    db_path, journal = audited_colony(tmp_path)
    records_root = tmp_path / "records"
    ok, detail = audit.audit(db_path, records_root=str(records_root))
    assert ok, detail
    state = audit.load_state(str(records_root))
    assert set(state["segments"]) == {"2026-07-16.csv", "2026-07-17.csv"}
    assert all(s["ok"] for s in state["segments"].values())
    assert state["critical"] is False
    # idempotent: nothing new to audit
    ok, detail = audit.audit(db_path, records_root=str(records_root))
    assert ok and "no unaudited" in detail
    index = (records_root / "INDEX.txt").read_text()
    assert "PASS" in index and "!! " not in index


def test_replay_twin_mismatch_is_critical_and_latches(tmp_path):
    db_path, journal = audited_colony(tmp_path)
    # a fresh records root: the daemon audited (and PASSed) these segments
    # in-run, so re-audit them from scratch against a tampered tape
    records_root = tmp_path / "records2"
    # rewrite consumed history (subtly — prices stay plausible): the tape
    # no longer matches the live ledger
    write_segment(journal, "2026-07-16", seg_rows(30, base=101))
    ok, detail = audit.audit(db_path, records_root=str(records_root))
    assert not ok
    state = audit.load_state(str(records_root))
    assert state["critical"] is True
    index = (records_root / "INDEX.txt").read_text()
    assert index.startswith("!! ") or "\n!! " in index  # the incident query works
    # operator clears the latch explicitly
    audit.clear_critical(str(records_root))
    assert audit.load_state(str(records_root))["critical"] is False


def test_daemon_status_exit_codes(tmp_path, monkeypatch):
    from colony import daemon as daemon_mod
    import urllib.request

    class FakeResponse:
        def __init__(self, payload):
            self._data = json.dumps(payload).encode()
        def read(self):
            return self._data
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    payloads = {}

    def fake_urlopen(url, timeout=None):
        return FakeResponse(payloads["health"])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    payloads["health"] = {"state": "running", "audit_critical": False,
                          "last_audit": {"segment": "x", "ok": True, "utc": "t"}}
    assert daemon_mod.status(1) == 0
    payloads["health"] = {"state": "stale", "audit_critical": False, "last_audit": None}
    assert daemon_mod.status(1) == 0  # stale is healthy: pause, not failure
    payloads["health"] = {"state": "running", "audit_critical": True, "last_audit": None}
    assert daemon_mod.status(1) == 1
    payloads["health"] = {"state": "running", "audit_critical": False,
                          "last_audit": {"segment": "x", "ok": False, "utc": "t"}}
    assert daemon_mod.status(1) == 1

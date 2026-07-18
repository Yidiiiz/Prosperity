"""Live arena: journal tailing, stale-feed timeout, torn lines, resume
guard, and the core claim — a live run equals its replay twin. All offline;
tests play the role of the feed daemon by appending rows."""

import time

import pytest

from colony.arenas.live import Live
from colony.config import ConfigError
from tests.conftest import make_cfg, make_colony
from tests.test_determinism import ledger_hash
from tests.test_replay import trend_closes, write_csv


def append_row(path, close, torn=False):
    with open(path, "a", newline="", encoding="utf-8") as f:
        row = f"2021-01-01,{close}"
        f.write(row if torn else row + "\n")


def live_cfg(csv_path, **overrides):
    return make_cfg(
        arena={"kind": "live", "name": "live_test", "csv": csv_path,
               "poll_timeout_seconds": 0.5},
        **overrides,
    )


def test_empty_or_missing_journal_refused(tmp_path):
    missing = str(tmp_path / "nope.csv")
    with pytest.raises(ValueError):
        Live({"name": "x", "csv": missing})
    header_only = tmp_path / "empty.csv"
    header_only.write_text("Date,Close\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Live({"name": "x", "csv": str(header_only)})


def test_consumes_appended_rows(tmp_path):
    path = write_csv(tmp_path / "j.csv", [2.0, 2.5])
    arena = Live({"name": "x", "csv": path})
    assert arena.price() == 200
    assert arena.wait_for_data()
    arena.step(None)
    assert arena.price() == 250
    append_row(path, 3.0)
    assert arena.wait_for_data()
    arena.step(None)
    assert arena.price() == 300
    assert arena.history(3) == [200, 250, 300]


def test_stale_feed_times_out(tmp_path):
    path = write_csv(tmp_path / "j.csv", [2.0])
    arena = Live({"name": "x", "csv": path, "poll_timeout_seconds": 0.3})
    start = time.monotonic()
    assert not arena.wait_for_data()
    assert time.monotonic() - start >= 0.3
    assert not arena.exhausted()  # stale is not exhausted; the feed may return


def test_torn_tail_line_ignored_until_complete(tmp_path):
    path = write_csv(tmp_path / "j.csv", [2.0])
    append_row(path, 9.99, torn=True)  # feed caught mid-write
    arena = Live({"name": "x", "csv": path, "poll_timeout_seconds": 0.3})
    assert not arena.wait_for_data()  # torn line is not data
    with open(path, "a", newline="", encoding="utf-8") as f:
        f.write("\n")  # the rest of the write arrives
    assert arena.wait_for_data()
    arena.step(None)
    assert arena.price() == 999


def test_resume_guard_accepts_growth_rejects_tampering(tmp_path):
    path = write_csv(tmp_path / "j.csv", [2.0, 2.5, 3.0])
    arena = Live({"name": "x", "csv": path})
    arena.step(None)
    state = arena.get_state()
    append_row(path, 3.5)  # append-only growth is fine
    resumed = Live({"name": "x", "csv": path})
    resumed.set_state(state)
    assert resumed.price() == 250
    tampered = write_csv(tmp_path / "t.csv", [9.0, 9.0, 9.0])
    other = Live({"name": "x", "csv": tampered})
    with pytest.raises(RuntimeError):
        other.set_state(state)


def test_live_run_equals_replay_twin(tmp_path):
    """The core v3 claim: with the same journal, config and seed, a live
    session and its offline replay produce byte-identical ledgers."""
    closes = trend_closes(150)
    journal = write_csv(tmp_path / "j.csv", closes)
    con_live, orch_live = make_colony(tmp_path, live_cfg(journal), "live.db")
    assert orch_live.run(120) == 120
    con_twin, orch_twin = make_colony(
        tmp_path,
        make_cfg(arena={"kind": "replay", "name": "live_test", "csv": journal}),
        "twin.db",
    )
    assert orch_twin.run(120) == 120
    assert ledger_hash(con_live) == ledger_hash(con_twin)


def test_config_live_validation(tmp_path):
    with pytest.raises(ConfigError):
        make_cfg(arena={"kind": "live", "name": "x"})  # no csv
    with pytest.raises(ConfigError):
        make_cfg(arena={"kind": "live", "name": "x", "csv": "j.csv",
                        "poll_timeout_seconds": 0})

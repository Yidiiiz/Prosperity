"""spec v3 sections 7/10: walk-forward window mechanics and the held-out
enforcement of the reuse A/B."""

import pytest

from colony import bank
from experiments.bank_reuse import check_held_out
from experiments.walk_forward import split_windows
from tests.test_bank import MOMENTUM, admit_event


def test_split_windows_contiguous_and_complete():
    times = list(range(0, 1030))
    closes = [float(i) for i in times]
    windows = split_windows(times, closes, 4)
    assert len(windows) == 4
    joined = [t for w_times, _ in windows for t in w_times]
    assert joined == times  # nothing lost, nothing duplicated
    assert len(windows[-1][0]) >= len(windows[0][0])  # remainder to the last


def test_split_windows_refuses_tiny_tapes():
    with pytest.raises(SystemExit):
        split_windows([1, 2, 3], [1.0, 2.0, 3.0], 4)


def test_held_out_enforcement_refuses_touched_windows(tmp_path):
    log = tmp_path / "bank.jsonl"
    ev = admit_event(window_end="2029-12-31T00:00:00+00:00")
    bank.append_event(log, ev)
    bank.append_event(log, {
        "event": "certify", "utc": "x", "genome_hash": ev["genome_hash"],
        "probe": {"window": ["2030-01-01T00:00:00+00:00",
                             "2030-03-01T00:00:00+00:00"]},
        "audited": {"realized_pnl_u": 1, "fills": 5,
                    "realized_bps_per_day": 1.0}})
    day = 86_400

    def utc(iso):
        from colony.arenas.replay import parse_utc
        return parse_utc(iso)

    # overlaps the admission window -> refuse
    with pytest.raises(SystemExit, match="held-out"):
        check_held_out(log, utc("2029-06-01"), utc("2029-07-01"))
    # overlaps the certification probe window -> refuse
    with pytest.raises(SystemExit, match="held-out"):
        check_held_out(log, utc("2030-02-15"), utc("2030-06-01"))
    # strictly after everything the bank has ever seen -> allowed
    check_held_out(log, utc("2030-03-01") + day, utc("2030-09-01"))

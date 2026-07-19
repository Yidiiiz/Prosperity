"""spec v3 section 4: the genome bank — events, admission, certification."""

import datetime
import json

import pytest

from colony import bank
from tests.conftest import make_cfg, make_colony

VENUE = {"taker_bps": 20, "maker_bps": 0, "spread_bps": 2, "min_fee_u": 0,
         "fill_delay_ticks": 1}

MOMENTUM = {"archetype": "momentum",
            "params": {"lookback": 10, "entry_z": 0.5, "exit_z": -1.5,
                       "risk_fraction": 0.6, "hold_max": 1500, "vol_gate_bps": 0,
                       "max_trades_per_day": 500, "active_hours_mask": (1 << 24) - 1},
            "econ": {"child_seed_fraction": 0.4}, "genes": []}


def write_tape(path, start="2030-01-01", days=120, first=100.0, drift=1.01):
    rows = ["Date,Close"]
    day = datetime.date.fromisoformat(start)
    close = first
    for i in range(days):
        rows.append(f"{day + datetime.timedelta(days=i)},{close:.4f}")
        close *= drift
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def admit_event(h="a" * 64, window_end="2029-12-31T00:00:00+00:00", genome=MOMENTUM):
    return {"event": "admit", "utc": "2029-12-31T00:00:00+00:00",
            "genome_hash": h, "genome": genome,
            "source": {"arena": "test", "tape_digest": "x",
                       "window": ["2029-01-01T00:00:00+00:00", window_end],
                       "config_seed": 1, "run_db": "x.db", "agent_id": "000001"},
            "audited": {"realized_pnl_u": 1, "realized_bps_per_day": 1.0,
                        "fills": 20, "fees_u": 0, "active_days": 10.0}}


def test_hash_is_canonical_and_order_independent():
    a = {"archetype": "momentum", "params": {"x": 1, "y": 2}}
    b = {"params": {"y": 2, "x": 1}, "archetype": "momentum"}
    assert bank.genome_hash(a) == bank.genome_hash(b)
    assert bank.genome_hash(a) != bank.genome_hash({**a, "archetype": "sitter"})


def test_fold_last_status_wins_and_lapsed_stay_visible(tmp_path):
    log = tmp_path / "bank.jsonl"
    ev = admit_event()
    bank.append_event(log, ev)
    state = bank.fold(log)
    assert state[ev["genome_hash"]]["status"] == "candidate"
    bank.append_event(log, {"event": "certify", "utc": "x",
                            "genome_hash": ev["genome_hash"], "audited": {}})
    assert bank.fold(log)[ev["genome_hash"]]["status"] == "certified"
    bank.append_event(log, {"event": "lapse", "utc": "x",
                            "genome_hash": ev["genome_hash"], "audited": {}})
    state = bank.fold(log)
    assert state[ev["genome_hash"]]["status"] == "lapsed"
    assert state[ev["genome_hash"]]["genome"] == MOMENTUM  # data, forever


def test_admission_at_terminal_audit_with_provenance(tmp_path):
    log = tmp_path / "bank.jsonl"
    cfg = make_cfg(rng_seed=7, bank_path=str(log), bank_min_fills=1,
                   records_root=str(tmp_path / "records"))
    con, orch = make_colony(tmp_path, cfg)
    orch.run(400)
    orch.wind_down()  # spec v3 4.3: admission is automatic here
    state = bank.fold(log)
    assert 0 < len(state) <= cfg["bank_admit_top_k"]
    for entry in state.values():
        assert entry["status"] == "candidate"  # in-sample earns nothing more
        src = entry["admit"]["source"]
        for key in ("arena", "tape_digest", "window", "config_seed", "run_db",
                    "agent_id"):
            assert src[key] is not None
        audited = entry["admit"]["audited"]
        assert audited["realized_pnl_u"] > 0
        assert audited["fills"] >= 1
    # dedup: admitting the same db again adds nothing
    assert bank.admit_from_db(con, log, records_root=str(tmp_path / "records")) == []
    assert len(bank.fold(log)) == len(state)
    # a bank record was written by the automatic admission (spec v3 4.5)
    assert list((tmp_path / "records" / "bank").glob("admit_*.txt"))


def test_certify_postdating_probe_certifies_or_lapses(tmp_path):
    log = tmp_path / "bank.jsonl"
    bank.append_event(log, admit_event())
    tape = write_tape(tmp_path / "up.csv", drift=1.02)
    results = bank.certify(log, tape, VENUE, records_root=str(tmp_path / "records"))
    assert len(results) == 1
    h, verdict, pnl = results[0]
    assert verdict == "certify" and pnl > 0
    assert bank.fold(log)[h]["status"] == "certified"
    # a falling tape lapses it again on re-certification
    down = write_tape(tmp_path / "down.csv", start="2031-01-01", drift=0.98)
    results = bank.certify(log, down, VENUE, recertify=True,
                           records_root=str(tmp_path / "records"))
    assert results[0][1] == "lapse"
    assert bank.fold(log)[h]["status"] == "lapsed"
    # lapsed genomes are never probed again
    assert bank.certify(log, tape, VENUE, records_root=str(tmp_path / "records")) == []


def test_certify_refuses_overlapping_window(tmp_path):
    """The leakage refusal (spec v3 4.4 / 10.2): a probe overlapping the
    admission window must be impossible."""
    log = tmp_path / "bank.jsonl"
    bank.append_event(log, admit_event(window_end="2030-02-01T00:00:00+00:00"))
    tape = write_tape(tmp_path / "overlap.csv", start="2030-01-15")
    with pytest.raises(bank.BankError, match="refusing"):
        bank.certify(log, tape, VENUE, records_root=str(tmp_path / "records"))
    # --from that trims the tape past the window end is accepted
    results = bank.certify(log, tape, VENUE, from_date="2030-02-02",
                           records_root=str(tmp_path / "records"))
    assert len(results) == 1


def test_solo_probe_is_deterministic_and_pays_costs(tmp_path):
    tape = write_tape(tmp_path / "t.csv", days=60, drift=1.015)
    from colony.arenas.replay import read_rows, to_price_u

    times, closes = read_rows(tape)
    prices = [to_price_u(c, 1) for c in closes]
    a = bank.solo_probe(MOMENTUM, times, prices, VENUE)
    b = bank.solo_probe(MOMENTUM, times, prices, VENUE)
    assert a == b
    free = dict(VENUE, taker_bps=0, spread_bps=0)
    assert bank.solo_probe(MOMENTUM, times, prices, free)[0] >= a[0]


def test_bank_stores_only_parameter_dicts(tmp_path):
    """Red line (spec v3 4.6): nothing executable enters the bank."""
    log = tmp_path / "bank.jsonl"
    bank.append_event(log, admit_event())
    for line in log.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)  # JSON round-trip: data only, by construction
        assert isinstance(event["genome"], dict)

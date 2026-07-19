"""Replay arena: CSV parsing, determinism, exhaustion, resume, small stakes."""

import csv

import pytest

from colony import orchestrator
from colony.arenas import make_arena
from colony.arenas.replay import ArenaExhausted, Replay
from colony.config import ConfigError, validate
from tests.conftest import make_cfg, make_colony
from tests.test_determinism import ledger_hash


def write_csv(path, closes):
    import datetime
    start = datetime.date(2020, 1, 1)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Close"])
        for i, close in enumerate(closes):
            writer.writerow([(start + datetime.timedelta(days=i)).isoformat(), close])
    return str(path)


def trend_closes(n=300):
    """A deterministic upward wiggle around $2.00 lots."""
    return [round(2.00 * 1.0012 ** i + 0.02 * ((i * 7) % 11 - 5) / 5, 4) for i in range(n)]


def replay_cfg(csv_path, **overrides):
    overrides.setdefault("venue", {"fill_delay_ticks": 1})
    return make_cfg(arena={"kind": "replay", "name": "test_replay", "csv": csv_path,
                           "tick_seconds": 86_400},
                    **overrides)


def test_prices_convert_to_integer_u(tmp_path):
    path = write_csv(tmp_path / "p.csv", [10.0, 10.51, 0.0000004])
    arena = Replay({"name": "x", "csv": path})
    assert arena._prices == [10_000_000, 10_510_000, 1]  # sub-u closes floor at 1 u


def test_lot_denominator_scales_price(tmp_path):
    path = write_csv(tmp_path / "p.csv", [500.0, 600.0])
    arena = Replay({"name": "x", "csv": path, "lot_denominator": 100})
    assert arena._prices == [5_000_000, 6_000_000]


def test_replay_ignores_rng_and_is_deterministic(tmp_path):
    import random
    path = write_csv(tmp_path / "p.csv", trend_closes(50))
    a, b = Replay({"name": "x", "csv": path}), Replay({"name": "x", "csv": path})
    for _ in range(30):
        a.step(random.Random(1))
        b.step(random.Random(999))
    assert a.price() == b.price()
    assert a.history(10) == b.history(10)


def test_exhaustion(tmp_path):
    path = write_csv(tmp_path / "p.csv", [1.0, 2.0, 3.0])
    arena = Replay({"name": "x", "csv": path})
    assert arena.ticks_total() == 2
    arena.step(None)
    assert not arena.exhausted()
    arena.step(None)
    assert arena.exhausted()
    with pytest.raises(ArenaExhausted):
        arena.step(None)


def test_state_digest_guards_against_swapped_csv(tmp_path):
    path_a = write_csv(tmp_path / "a.csv", trend_closes(50))
    path_b = write_csv(tmp_path / "b.csv", [5.0] * 50)
    arena = Replay({"name": "x", "csv": path_a})
    arena.step(None)
    state = arena.get_state()
    resumed = Replay({"name": "x", "csv": path_a})
    resumed.set_state(state)
    assert resumed.price() == arena.price()
    swapped = Replay({"name": "x", "csv": path_b})
    with pytest.raises(RuntimeError):
        swapped.set_state(state)


def test_config_validation(tmp_path):
    with pytest.raises(ConfigError):
        make_cfg(arena={"kind": "replay", "name": "x"})  # no csv
    with pytest.raises(ConfigError):
        make_cfg(arena={"kind": "replay", "name": "x", "csv": "p.csv", "lot_denominator": 0})
    with pytest.raises(ConfigError):
        make_cfg(arena={"kind": "warp"})


def test_granularity_check_at_init(tmp_path):
    # $10.00 lots vs a $1,000 seed violates the 200x rule (spec 3.11) ...
    path = write_csv(tmp_path / "p.csv", [10.0] * 200)
    cfg = replay_cfg(path)
    cfg["gen0_seed_u"] = 100_000
    with pytest.raises(ConfigError):
        make_colony(tmp_path, cfg, "strict.db")
    # ... unless small stakes are explicitly accepted
    cfg["small_stakes"] = True
    con, orch = make_colony(tmp_path, cfg, "waived.db")
    assert len(orch.agents) == cfg["gen0_population"]


def test_run_stops_at_exhaustion(tmp_path):
    path = write_csv(tmp_path / "p.csv", trend_closes(200))
    con, orch = make_colony(tmp_path, replay_cfg(path))
    executed = orch.run(10_000)  # far more than the data holds
    assert executed == orch.tick == 199
    assert orch.arena.exhausted()


def test_replay_resume_matches_uninterrupted_run(tmp_path):
    path = write_csv(tmp_path / "p.csv", trend_closes(200))
    cfg = replay_cfg(path)
    con_a, orch_a = make_colony(tmp_path, cfg, "a.db")
    orch_a.run(150)
    con_b, orch_b = make_colony(tmp_path, cfg, "b.db")
    orch_b.run(70)
    resumed = orchestrator.Orchestrator(con_b)
    resumed.run(80)
    assert resumed.tick == orch_a.tick == 150
    assert ledger_hash(con_a) == ledger_hash(con_b)


def test_zero_rent_posts_no_ledger_row(tmp_path):
    """At small stakes rent rounds to 0; a 0-cent rent is a no-op, not a row."""
    path = write_csv(tmp_path / "p.csv", [2.0] * 150)
    cfg = replay_cfg(
        path, gen0_seed_u=250, death_floor_u=50, reserve_floor_u=50,
        rent_min_u=0, small_stakes=True,
    )
    con, orch = make_colony(tmp_path, cfg)
    orch.run(100)
    assert len(orch.agents) > 0
    assert con.execute("SELECT COUNT(*) FROM ledger WHERE memo = 'rent'").fetchone()[0] == 0


def test_wind_down_returns_everything_to_treasury(tmp_path):
    """Terminal audit: liquidating the colony leaves all system cash in
    TREASURY and ARENA, agents empty, invariants intact."""
    path = write_csv(tmp_path / "p.csv", trend_closes(150))
    cfg = replay_cfg(path)
    con, orch = make_colony(tmp_path, cfg)
    orch.run(100)
    assert len(orch.agents) > 0
    orch.wind_down()
    assert len(orch.agents) == 0
    agent_cash = con.execute(
        "SELECT COALESCE(SUM(balance_u), 0) FROM balances WHERE account_id LIKE 'AGENT:%'"
    ).fetchone()[0]
    assert agent_cash == 0
    treasury = con.execute(
        "SELECT balance_u FROM balances WHERE account_id = 'TREASURY'"
    ).fetchone()[0]
    arena = con.execute(
        "SELECT balance_u FROM balances WHERE account_id LIKE 'ARENA:%'"
    ).fetchone()[0]
    assert treasury + arena == cfg["initial_treasury_u"]
    assert con.execute(
        "SELECT COUNT(*) FROM agents WHERE death_cause = 'horizon'"
    ).fetchone()[0] > 0


def test_make_arena_dispatch(tmp_path, base_cfg):
    path = write_csv(tmp_path / "p.csv", [1.0, 2.0])
    assert type(make_arena(base_cfg["arena"])).__name__ == "Petri"
    assert type(make_arena({"kind": "replay", "name": "x", "csv": path})).__name__ == "Replay"
    with pytest.raises(ValueError):
        make_arena({"kind": "warp"})

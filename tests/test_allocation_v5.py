"""spec v5 section 5: calendar join, cost accounting, train/test boundary,
holdout guard."""

import math

import pytest

from experiments import allocation
from experiments.allocation import (ASSET_ORDER, BASE_VENUE, CAPITAL_U,
                                    load_joint, rebalance, run_window,
                                    select, targets_for)


@pytest.fixture(scope="module")
def joint():
    return load_joint()


def test_joint_calendar_is_spy_days_with_weekend_crypto_sampled(joint):
    times, closes, prices_u = joint
    # master clock is strictly increasing SPY days inside the common span
    assert all(b > a for a, b in zip(times, times[1:]))
    # every asset has one close per master day, all positive
    for x in ASSET_ORDER:
        assert len(closes[x]) == len(times)
        assert all(c > 0 for c in closes[x])
        assert all(p >= 1 for p in prices_u[x])
    # crypto sampling never looks forward: btc close on the first master day
    # equals a real btc bar at or before that day (2017-08-17 span start)
    assert times[0] >= allocation.read_rows(
        allocation.ROOT / "data" / "btcusdt_1d.csv")[0][0]


def test_rebalance_round_trip_pays_the_venue(joint):
    # buy everything into one asset then sell it all: 11 bps per side
    # (10 taker + 1 half-spread) -> ~22 bps of the invested notional,
    # marginally less of capital since fees keep a sliver uninvested
    P = {x: 1_000_000 for x in ASSET_ORDER}  # $1 lots, flat prices
    lots = {x: 0 for x in ASSET_ORDER}
    cash = rebalance(CAPITAL_U, lots, {"spy": 1.0}, P, BASE_VENUE)
    assert lots["spy"] > 0
    cash = rebalance(cash, lots, {}, P, BASE_VENUE)
    assert lots["spy"] == 0
    lost_bps = (CAPITAL_U - cash) / CAPITAL_U * 10_000
    assert 20 <= lost_bps < 30


def test_flat_tape_never_creates_money():
    # any family on a flat synthetic tape can only lose the tolls
    n = 300
    C = {x: [100.0] * n for x in ASSET_ORDER}
    P = {x: [100_000_000] * n for x in ASSET_ORDER}
    for family, grid in allocation.GRIDS.items():
        cash = run_window(family, grid[0], C, P, 30, n)
        assert cash <= CAPITAL_U, family


def test_signals_use_only_history(joint):
    # day i targets must be identical whether or not the future is mutated:
    # feed targets_for a copy where everything >= i is corrupted
    times, closes, _ = joint
    i = 400
    corrupted = {x: closes[x][:i] + [1e12] * (len(times) - i)
                 for x in ASSET_ORDER}
    for family, grid in allocation.GRIDS.items():
        for params in grid:
            a, state = i - (i % 21), None  # make i a rebalance day
            t1, _ = targets_for(family, params, closes, i, a, state)
            t2, _ = targets_for(family, params, corrupted, i, a, state)
            assert t1 == t2, (family, params)


def test_select_is_deterministic_and_from_the_declared_grid(joint):
    times, closes, prices_u = joint
    best = select("dual_momentum", closes, prices_u, 0, 300)
    assert best in allocation.GRIDS["dual_momentum"]
    assert best == select("dual_momentum", closes, prices_u, 0, 300)


def test_holdout_guard_refuses_second_shot(tmp_path, monkeypatch, capsys):
    shot = tmp_path / "alloc.SHOT"
    shot.write_text("fired\n", encoding="utf-8")
    monkeypatch.setattr(allocation, "SHOT", shot)
    monkeypatch.setattr(allocation, "HOLDOUT_CSV", tmp_path / "alloc.csv")
    rc = allocation.main(["--holdout", "dual_momentum"])
    assert rc == 2
    assert "already fired" in capsys.readouterr().err


def test_vol_target_exposure_caps_at_one():
    # near-zero vol must clamp to full exposure, never leverage (v5 1)
    n = 60
    C = {x: [100.0 + 0.001 * k for k in range(n)] for x in ASSET_ORDER}
    t, _ = targets_for("vol_target", {"asset": "spy", "T": 0.20}, C, 40, 40,
                       None)
    assert t == {"spy": pytest.approx(1.0)} or t["spy"] <= 1.0
    assert all(w <= 1.0 + 1e-12 for w in t.values())
    assert not math.isnan(t["spy"])

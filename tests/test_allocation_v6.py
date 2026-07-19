"""spec v6 section 5: equity-era calendar, leakage, the v5 bridge, weight
invariants, flat-tape conservation, holdout guard."""

import datetime

import pytest

from experiments import allocation, allocation6
from experiments.allocation6 import (CAPITAL_U, U_ETF, U_FULL, grids_for,
                                     load_joint, run_window, select,
                                     targets_for)


@pytest.fixture(scope="module")
def etf_joint():
    return load_joint(U_ETF)


def test_equity_era_calendar_starts_at_gld_and_aligns(etf_joint):
    times, closes, prices_u = etf_joint
    first = datetime.datetime.fromtimestamp(
        times[0], datetime.timezone.utc).date()
    # GLD binds the common span (spec v6 1): inception 2004-11-18
    assert first >= datetime.date(2004, 11, 18)
    assert first <= datetime.date(2004, 12, 31)
    assert all(b > a for a, b in zip(times, times[1:]))
    for x in U_ETF:
        assert len(closes[x]) == len(times)
        assert all(c > 0 for c in closes[x])
        assert all(p >= 1 for p in prices_u[x])


def test_signals_use_only_history(etf_joint):
    # day i targets identical whether or not the future is corrupted
    times, closes, _ = etf_joint
    i = 400
    corrupted = {x: closes[x][:i] + [1e12] * (len(times) - i) for x in U_ETF}
    for family, grid in grids_for(U_ETF).items():
        for params in grid:
            a, state = i - (i % 21), None  # make i a rebalance day
            t1, _ = targets_for(family, params, closes, i, a, state, U_ETF)
            t2, _ = targets_for(family, params, corrupted, i, a, state, U_ETF)
            assert t1 == t2, (family, params)


def test_dm_topk_k1_bridges_to_v5_dual_momentum():
    # dm_topk is a strict generalization: K=1 on the v5 universe must emit
    # the v5 winner's exact targets on every rebalance day (spec v6 5)
    times, closes, _ = allocation.load_joint()
    U5 = allocation.ASSET_ORDER
    for L in (63, 126, 252):
        for i in range(0, len(times), 210):  # every 10th rebalance day
            t5, _ = allocation.targets_for("dual_momentum", {"L": L},
                                           closes, i, 0, None)
            t6, _ = targets_for("dm_topk", {"K": 1, "L": L},
                                closes, i, 0, None, U5)
            assert t5 == t6, (L, i)


def test_weights_never_exceed_one(etf_joint):
    # no leverage anywhere (spec v6 2): every family's weights sum <= 1
    _, closes, _ = etf_joint
    for family, grid in grids_for(U_ETF).items():
        for params in grid:
            for i in (300, 903, 2100):
                t, _ = targets_for(family, params, closes, i,
                                   i - (i % 21), None, U_ETF)
                if t is not None:
                    assert sum(t.values()) <= 1.0 + 1e-9, (family, params)
                    assert all(w >= 0 for w in t.values())


def test_inv_vol_weights_sum_to_one(etf_joint):
    _, closes, _ = etf_joint
    t, _ = targets_for("inv_vol", {"R": 21}, closes, 210, 210, None, U_ETF)
    assert sum(t.values()) == pytest.approx(1.0)


def test_dm_defensive_falls_back_when_momentum_is_negative():
    # a universe in steady decline has no positive momentum anywhere: the
    # defensive asset gets the whole book, never cash
    n = 300
    C = {x: [100.0 * (0.999 ** k) for k in range(n)] for x in U_ETF}
    t, _ = targets_for("dm_defensive", {"L": 126, "D": "gld"},
                       C, 273, 0, None, U_ETF)
    assert t == {"gld": 1.0}


def test_flat_tape_never_creates_money():
    n = 300
    C = {x: [100.0] * n for x in U_FULL}
    P = {x: [100_000_000] * n for x in U_FULL}
    for family, grid in grids_for(U_FULL).items():
        cash = run_window(family, grid[0], C, P, 30, n, U_FULL)
        assert cash <= CAPITAL_U, family


def test_select_is_deterministic_and_from_the_declared_grid(etf_joint):
    _, closes, prices_u = etf_joint
    best = select("dm_topk", closes, prices_u, 0, 400, U_ETF)
    assert best in grids_for(U_ETF)["dm_topk"]
    assert best == select("dm_topk", closes, prices_u, 0, 400, U_ETF)


def test_holdout_guard_refuses_second_shot(tmp_path, monkeypatch, capsys):
    shot = tmp_path / "alloc6.SHOT"
    shot.write_text("fired\n", encoding="utf-8")
    monkeypatch.setattr(allocation6, "SHOT", shot)
    monkeypatch.setattr(allocation6, "HOLDOUT_CSV", tmp_path / "alloc6.csv")
    rc = allocation6.main(["--holdout", "dm_topk"])
    assert rc == 2
    assert "already fired" in capsys.readouterr().err

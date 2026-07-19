"""spec v7 section 5: leakage, the cadence bridge, gate switching,
slow_bh investment invariant, flat-tape conservation, forward-holdout
refusals, FORWARD declaration integrity."""

import pytest

from experiments import allocation7
from experiments.allocation7 import (CAPITAL_U, SLOW_L, U_ETF, U_FULL,
                                     grids_for, load_joint, run_window,
                                     targets_for)


@pytest.fixture(scope="module")
def etf_joint():
    return load_joint(U_ETF)


def test_signals_use_only_history(etf_joint):
    times, closes, _ = etf_joint
    i = 630  # lcm(5, 21, 63, 126): a rebalance day for every declared R
    corrupted = {x: closes[x][:i] + [1e12] * (len(times) - i) for x in U_ETF}
    for family, grid in grids_for(U_ETF).items():
        for params in grid:
            a, state = 0, None
            t1, _ = targets_for(family, params, closes, i, a, state, U_ETF)
            t2, _ = targets_for(family, params, corrupted, i, a, state, U_ETF)
            assert t1 == t2, (family, params)


def test_dm_cadence_r21_bridges_to_dm_topk_k1(etf_joint):
    # spec v7 2: the cadence arm at R=21 must reproduce the incumbent
    times, closes, _ = etf_joint
    for L in (126, 252):
        for i in range(0, len(times), 210):
            t7, _ = targets_for("dm_cadence", {"L": L, "R": 21},
                                closes, i, 0, None, U_ETF)
            t6, _ = targets_for("dm_topk", {"K": 1, "L": L},
                                closes, i, 0, None, U_ETF)
            assert t7 == t6, (L, i)


def test_slow_bh_is_always_fully_invested_once_history_exists(etf_joint):
    _, closes, _ = etf_joint
    for i in (SLOW_L + 1, 1000, 2520, 4032):
        t, _ = targets_for("slow_bh", {"L": SLOW_L, "R": 63},
                           closes, i, i, None, U_ETF)
        assert sum(t.values()) == pytest.approx(1.0), i


def test_dm_gated_switches_modes_on_dispersion():
    # high dispersion: one asset melting up -> fast momentum picks it;
    # low dispersion: all drifting down together -> regime mode holds the
    # slow winner instead of retreating to cash (no positivity filter)
    n = 500
    up = {x: [100.0] * n for x in U_ETF}
    up["qqq"] = [100.0 * (1.01 ** k) for k in range(n)]
    t, _ = targets_for("dm_gated", {"Lf": 63, "G": 0.15}, up, 441, 0,
                       None, U_ETF)
    assert t == {"qqq": 1.0}
    down = {x: [100.0 * (0.9999 ** k) for k in range(n)] for x in U_ETF}
    down["gld"] = [100.0 * (0.99995 ** k) for k in range(n)]  # least bad
    t, _ = targets_for("dm_gated", {"Lf": 63, "G": 0.15}, down, 441, 0,
                       None, U_ETF)
    assert t == {"gld": 1.0}


def test_flat_tape_never_creates_money():
    n = 500
    C = {x: [100.0] * n for x in U_FULL}
    P = {x: [100_000_000] * n for x in U_FULL}
    for family, grid in grids_for(U_FULL).items():
        cash = run_window(family, grid[0], C, P, 30, n, U_FULL)
        assert cash <= CAPITAL_U, family


def test_forward_holdout_refuses_spent_shot(tmp_path, monkeypatch, capsys):
    shot = tmp_path / "alloc7.SHOT"
    shot.write_text("fired\n", encoding="utf-8")
    monkeypatch.setattr(allocation7, "SHOT", shot)
    rc = allocation7.main(["--holdout", "dm_gated"])
    assert rc == 2
    assert "already fired" in capsys.readouterr().err


def test_forward_holdout_refuses_until_ripe(tmp_path, monkeypatch, capsys):
    # a declaration whose cutoff postdates every tape row must refuse:
    # zero virgin rows exist, 126 are required (spec v7 4)
    fwd = tmp_path / "alloc7.FORWARD"
    fwd.write_text("family: dm_gated\nuniverse: full\n"
                   "cutoff: 2026-07-19\nmin_new_rows: 126\n",
                   encoding="utf-8")
    monkeypatch.setattr(allocation7, "FORWARD", fwd)
    monkeypatch.setattr(allocation7, "SHOT", tmp_path / "alloc7.SHOT")
    rc = allocation7.main(["--holdout", "dm_gated"])
    assert rc == 2
    assert "not ripe" in capsys.readouterr().err


def test_forward_holdout_refuses_undeclared_family(tmp_path, monkeypatch,
                                                   capsys):
    fwd = tmp_path / "alloc7.FORWARD"
    fwd.write_text("family: dm_gated\nuniverse: full\n"
                   "cutoff: 2026-07-19\nmin_new_rows: 126\n",
                   encoding="utf-8")
    monkeypatch.setattr(allocation7, "FORWARD", fwd)
    monkeypatch.setattr(allocation7, "SHOT", tmp_path / "alloc7.SHOT")
    rc = allocation7.main(["--holdout", "best_bh"])
    assert rc == 2
    assert "frozen" in capsys.readouterr().err


def test_forward_declaration_names_a_real_family():
    # the committed declaration must parse and target the declared grid
    kv = allocation7.read_forward()
    assert kv["family"] in grids_for(U_FULL)
    assert kv["universe"] in ("full", "etf")
    assert int(kv["min_new_rows"]) >= 126

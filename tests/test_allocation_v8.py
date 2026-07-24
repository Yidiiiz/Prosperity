"""spec v8 section 5: leakage, the mom_inv->dm_topk bridge, regime
switching, no daily churn, weight/conservation invariants, forward-holdout
refusals, FORWARD declaration integrity."""

import pytest

from experiments import allocation6, allocation8
from experiments.allocation8 import (CAPITAL_U, U_DIR, grids_for, load_joint,
                                     run_window, targets_for)


@pytest.fixture(scope="module")
def dir_joint():
    return load_joint(U_DIR)


def test_signals_use_only_history(dir_joint):
    times, closes, _ = dir_joint
    i = 420  # a mom_inv rebalance day (420 % 21 == 0) with full L history
    corrupted = {x: closes[x][:i] + [1e12] * (len(times) - i) for x in U_DIR}
    for family, grid in grids_for().items():
        for params in grid:
            t1, _ = targets_for(family, params, closes, i, 0, None, U_DIR)
            t2, _ = targets_for(family, params, corrupted, i, 0, None, U_DIR)
            assert t1 == t2, (family, params)


def test_mom_inv_bridges_to_dm_topk_k1(dir_joint):
    # spec v8 5: mom_inv over the universe minus the inverse ETFs is exactly
    # v6 dm_topk K=1 (same momentum, same r>0 filter, same 21-day gate)
    times, closes, _ = dir_joint
    sub = tuple(x for x in U_DIR if x not in ("sh", "psq"))
    for L in (63, 126, 252):
        for i in range(0, len(times), 210):
            t8, _ = targets_for("mom_inv", {"L": L}, closes, i, 0, None, sub)
            t6, _ = allocation6.targets_for("dm_topk", {"K": 1, "L": L},
                                            closes, i, 0, None, sub)
            assert t8 == t6, (L, i)


def test_regime_families_switch_on_trend():
    # bull (R melting up) -> hold R; bear (R crashing) -> inverse / cash / safe
    n = 500
    up = {x: [100.0] * n for x in U_DIR}
    up["spy"] = [100.0 * (1.001 ** k) for k in range(n)]
    for fam, params in (("regime_inv", {"L": 200, "R": "spy", "I": "sh"}),
                        ("regime_flat", {"L": 200, "R": "spy"}),
                        ("regime_safe", {"L": 200, "R": "spy", "S": "gld"})):
        t, _ = targets_for(fam, params, up, 441, 0, None, U_DIR)
        assert t == {"spy": 1.0}, fam

    down = {x: [100.0] * n for x in U_DIR}
    down["spy"] = [100.0 * (0.999 ** k) for k in range(n)]
    assert targets_for("regime_inv", {"L": 200, "R": "spy", "I": "sh"},
                       down, 441, 0, None, U_DIR)[0] == {"sh": 1.0}
    assert targets_for("regime_flat", {"L": 200, "R": "spy"},
                       down, 441, 0, None, U_DIR)[0] == {}
    assert targets_for("regime_safe", {"L": 200, "R": "spy", "S": "tlt"},
                       down, 441, 0, None, U_DIR)[0] == {"tlt": 1.0}


def test_regime_no_daily_churn():
    # a steady bull tape emits the target once, then holds (None) thereafter
    n = 500
    up = {x: [100.0] * n for x in U_DIR}
    up["spy"] = [100.0 * (1.001 ** k) for k in range(n)]
    params = {"L": 200, "R": "spy", "I": "sh"}
    t1, state = targets_for("regime_inv", params, up, 441, 0, None, U_DIR)
    assert t1 == {"spy": 1.0}
    t2, _ = targets_for("regime_inv", params, up, 442, 0, state, U_DIR)
    assert t2 is None


def test_weights_never_exceed_one(dir_joint):
    times, closes, _ = dir_joint
    for family, grid in grids_for().items():
        for params in grid:
            for i in range(300, len(times), 137):
                t, _ = targets_for(family, params, closes, i, 0, None, U_DIR)
                if t:
                    assert sum(t.values()) <= 1.0 + 1e-9, (family, params, i)


def test_flat_tape_never_creates_money():
    n = 500
    C = {x: [100.0] * n for x in U_DIR}
    P = {x: [100_000_000] * n for x in U_DIR}
    for family, grid in grids_for().items():
        cash = run_window(family, grid[0], C, P, 30, n, U_DIR)
        assert cash <= CAPITAL_U, family


def test_forward_refuses_spent_shot(tmp_path, monkeypatch, capsys):
    shot = tmp_path / "alloc8.SHOT"
    shot.write_text("fired\n", encoding="utf-8")
    monkeypatch.setattr(allocation8, "SHOT", shot)
    rc = allocation8.main(["--holdout", "regime_safe", "--forward"])
    assert rc == 2
    assert "already fired" in capsys.readouterr().err


def test_forward_refuses_until_ripe(tmp_path, monkeypatch, capsys):
    fwd = tmp_path / "alloc8.FORWARD"
    fwd.write_text("family: regime_safe\nuniverse: dir\n"
                   "cutoff: 2026-07-23\nmin_new_rows: 126\n",
                   encoding="utf-8")
    monkeypatch.setattr(allocation8, "FORWARD", fwd)
    monkeypatch.setattr(allocation8, "SHOT", tmp_path / "alloc8.SHOT")
    rc = allocation8.main(["--holdout", "regime_safe", "--forward"])
    assert rc == 2
    assert "not ripe" in capsys.readouterr().err


def test_forward_refuses_undeclared_family(tmp_path, monkeypatch, capsys):
    fwd = tmp_path / "alloc8.FORWARD"
    fwd.write_text("family: regime_safe\nuniverse: dir\n"
                   "cutoff: 2026-07-23\nmin_new_rows: 126\n",
                   encoding="utf-8")
    monkeypatch.setattr(allocation8, "FORWARD", fwd)
    monkeypatch.setattr(allocation8, "SHOT", tmp_path / "alloc8.SHOT")
    rc = allocation8.main(["--holdout", "regime_inv", "--forward"])
    assert rc == 2
    assert "frozen" in capsys.readouterr().err


def test_forward_declaration_names_a_real_family():
    kv = allocation8.read_forward()
    assert kv["family"] in grids_for()
    assert kv["universe"] == "dir"
    assert int(kv["min_new_rows"]) >= 126

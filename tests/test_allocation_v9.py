"""spec v9 section 5: leakage, Green Line Breakout mechanics, GMI switching
(reaching the -3x products), no daily churn, the sector_mom bridge,
weight/conservation invariants, forward refusals, FORWARD integrity."""

import pytest

from experiments import allocation6, allocation9
from experiments.allocation9 import (CAPITAL_U, MOM_U, U_WISH, grids_for,
                                     load_joint, run_window, targets_for)


@pytest.fixture(scope="module")
def wish_joint():
    return load_joint(U_WISH)


def test_signals_use_only_history(wish_joint):
    times, closes, _ = wish_joint
    i = 420  # a sector_mom rebalance day with full L/MA history
    corrupted = {x: closes[x][:i] + [1e12] * (len(times) - i) for x in U_WISH}
    for family, grid in grids_for().items():
        for params in grid:
            t1, _ = targets_for(family, params, closes, i, 0, None, U_WISH)
            t2, _ = targets_for(family, params, corrupted, i, 0, None, U_WISH)
            assert t1 == t2, (family, params)


def test_glb_enters_on_breakout_and_exits_on_ma_stop():
    # a long base at 100, then a breakout to a new all-time high, then a
    # collapse below the moving average
    S = 150
    base = [100.0] * 400          # sets the green line, builds the MA
    ramp = [100.0 + k for k in range(1, 121)]   # 101..220: breaks out
    crash = [220.0 - 8 * k for k in range(1, 60)]  # falls hard below MA
    series = base + ramp + crash
    C = {x: list(series) for x in U_WISH}
    p = {"R": "spy", "S": S}
    # walk the whole tape; track whether it ever went long and its final state
    entered, state = False, None
    for i in range(260, len(series)):
        t, state = targets_for("glb", p, C, i, 260, state, U_WISH)
        if t == {"spy": 1.0}:
            entered = True                # breakout entry happened
    assert entered, "glb never entered on the breakout"
    assert state is False, "glb did not exit to cash after the MA-stop crash"


def test_gmi_switches_and_reaches_3x_products():
    n = 400
    up = {x: [100.0] * n for x in U_WISH}
    up["qqq"] = [100.0 * (1.001 ** k) for k in range(n)]
    t, _ = targets_for("gmi_inv", {"R": "qqq", "I": "sqqq", "S": 150},
                       up, 380, 0, None, U_WISH)
    assert t == {"qqq": 1.0}
    down = {x: [100.0] * n for x in U_WISH}
    down["qqq"] = [100.0 * (0.999 ** k) for k in range(n)]
    t, _ = targets_for("gmi_inv", {"R": "qqq", "I": "sqqq", "S": 150},
                       down, 380, 0, None, U_WISH)
    assert t == {"sqqq": 1.0}   # the -3x product is reachable
    # the declared grid pairs each index with -1x and -3x/-2x legs
    legs = {(g["R"], g["I"]) for g in grids_for()["gmi_inv"]}
    assert ("qqq", "sqqq") in legs and ("qqq", "psq") in legs
    assert ("spy", "spxu") in legs and ("spy", "sh") in legs


def test_gmi_no_daily_churn():
    n = 400
    up = {x: [100.0] * n for x in U_WISH}
    up["qqq"] = [100.0 * (1.001 ** k) for k in range(n)]
    p = {"R": "qqq", "I": "sqqq", "S": 150}
    t1, state = targets_for("gmi_inv", p, up, 380, 0, None, U_WISH)
    assert t1 == {"qqq": 1.0}
    t2, _ = targets_for("gmi_inv", p, up, 381, 0, state, U_WISH)
    assert t2 is None


def test_sector_mom_bridges_to_dm_topk_k1(wish_joint):
    times, closes, _ = wish_joint
    for L in (63, 126, 252):
        for i in range(0, len(times), 210):
            t9, _ = targets_for("sector_mom", {"L": L, "K": 1},
                                closes, i, 0, None, U_WISH)
            t6, _ = allocation6.targets_for("dm_topk", {"K": 1, "L": L},
                                            closes, i, 0, None, MOM_U)
            assert t9 == t6, (L, i)


def test_weights_never_exceed_one(wish_joint):
    times, closes, _ = wish_joint
    for family, grid in grids_for().items():
        for params in grid:
            for i in range(300, len(times), 137):
                t, _ = targets_for(family, params, closes, i, 0, None, U_WISH)
                if t:
                    assert sum(t.values()) <= 1.0 + 1e-9, (family, params, i)


def test_flat_tape_never_creates_money():
    n = 500
    C = {x: [100.0] * n for x in U_WISH}
    P = {x: [100_000_000] * n for x in U_WISH}
    for family, grid in grids_for().items():
        cash = run_window(family, grid[0], C, P, 30, n, U_WISH)
        assert cash <= CAPITAL_U, family


def test_forward_refuses_spent_shot(tmp_path, monkeypatch, capsys):
    shot = tmp_path / "alloc9.SHOT"
    shot.write_text("fired\n", encoding="utf-8")
    monkeypatch.setattr(allocation9, "SHOT", shot)
    rc = allocation9.main(["--holdout", "gmi_inv", "--forward"])
    assert rc == 2
    assert "already fired" in capsys.readouterr().err


def test_forward_refuses_until_ripe(tmp_path, monkeypatch, capsys):
    fwd = tmp_path / "alloc9.FORWARD"
    fwd.write_text("family: gmi_inv\nuniverse: wish\n"
                   "cutoff: 2026-07-23\nmin_new_rows: 126\n", encoding="utf-8")
    monkeypatch.setattr(allocation9, "FORWARD", fwd)
    monkeypatch.setattr(allocation9, "SHOT", tmp_path / "alloc9.SHOT")
    rc = allocation9.main(["--holdout", "gmi_inv", "--forward"])
    assert rc == 2
    assert "not ripe" in capsys.readouterr().err


def test_forward_refuses_undeclared_family(tmp_path, monkeypatch, capsys):
    fwd = tmp_path / "alloc9.FORWARD"
    fwd.write_text("family: gmi_inv\nuniverse: wish\n"
                   "cutoff: 2026-07-23\nmin_new_rows: 126\n", encoding="utf-8")
    monkeypatch.setattr(allocation9, "FORWARD", fwd)
    monkeypatch.setattr(allocation9, "SHOT", tmp_path / "alloc9.SHOT")
    rc = allocation9.main(["--holdout", "glb", "--forward"])
    assert rc == 2
    assert "frozen" in capsys.readouterr().err


def test_forward_declaration_names_a_real_family():
    kv = allocation9.read_forward()
    assert kv["family"] in grids_for()
    assert kv["universe"] == "wish"
    assert int(kv["min_new_rows"]) >= 126

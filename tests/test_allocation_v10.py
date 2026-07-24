"""spec v10 section 5: leakage, the corrected GLB percent stop, GMI-lite
hysteresis reaching the inverse legs, the gmi_glb GMI-red exit, no daily churn,
weight/conservation invariants, forward refusals, FORWARD integrity."""

import pytest

from experiments import allocation10
from experiments.allocation10 import (CAPITAL_U, INV, U_WISH2, _gmi_phase,
                                      gmi_count, grids_for, load_joint,
                                      run_window, targets_for)


@pytest.fixture(scope="module")
def wish_joint():
    return load_joint(U_WISH2)


def test_signals_use_only_history(wish_joint):
    times, closes, _ = wish_joint
    i = 420  # past every warmup (GMI needs 200, GLB needs 63)
    corrupted = {x: closes[x][:i] + [1e12] * (len(times) - i) for x in U_WISH2}
    for family, grid in grids_for().items():
        for params in grid:
            t1, _ = targets_for(family, params, closes, i, 0, None, U_WISH2)
            t2, _ = targets_for(family, params, corrupted, i, 0, None, U_WISH2)
            assert t1 == t2, (family, params)


def test_glb_exits_only_on_the_percent_stop():
    # long base, a rising run of new all-time highs, shallow dips (< 5% off the
    # peak, must hold), then a drop through the 5% stop (must exit)
    pre = [100.0] * 200
    up = [100.0 + k for k in range(1, 101)]      # 101..200: fresh highs, peak 200
    dips = [196.0, 194.0, 193.0]                 # 2-3.5% off peak -> hold
    crash = [189.0, 180.0]                       # >5% off peak (<=190) -> exit
    series = pre + up + dips + crash
    C = {x: list(series) for x in U_WISH2}
    p = {"R": "spy", "p": 0.05}
    states, state = {}, None
    for i in range(70, len(series)):
        _, state = targets_for("glb_pct", p, C, i, 70, state, U_WISH2)
        states[i] = bool(state)
    assert any(states.values()), "glb_pct never entered on the breakout"
    # index 302 holds 193 (3.5% off the 200 peak); the day after still long
    assert states[303] is True, "exited on a shallow dip under the 5% stop"
    assert states[len(series) - 1] is False, "did not exit after the 5% drop"


def test_gmi_phase_hysteresis_band():
    # green holds down to the red line (3); red re-enters only at green (4)
    assert _gmi_phase(True, 3) is True     # stay green in the band
    assert _gmi_phase(True, 2) is False    # drop out of green below 3
    assert _gmi_phase(False, 3) is False   # stay red in the band
    assert _gmi_phase(False, 4) is True    # re-enter green at 4
    assert _gmi_phase(True, None) is False


def test_gmi_switch_holds_index_green_and_inverse_red():
    n = 260
    up = {x: [100.0] * n for x in U_WISH2}
    for x in ("qqq", "spy", "ita", "itb"):
        up[x] = [100.0 * (1.001 ** k) for k in range(n)]
    t, _ = targets_for("gmi_switch", {"R": "qqq", "D": "inv3"},
                       up, 259, 0, None, U_WISH2)
    assert t == {"qqq": 1.0}               # GMI green -> hold the index
    down = {x: [100.0] * n for x in U_WISH2}
    for x in ("qqq", "spy", "ita", "itb"):
        down[x] = [100.0 * (0.999 ** k) for k in range(n)]
    t3, _ = targets_for("gmi_switch", {"R": "qqq", "D": "inv3"},
                        down, 259, 0, None, U_WISH2)
    assert t3 == {INV["qqq"][1]: 1.0} == {"sqqq": 1.0}   # red -> -3x reachable
    t1, _ = targets_for("gmi_switch", {"R": "qqq", "D": "inv1"},
                        down, 259, 0, None, U_WISH2)
    assert t1 == {"psq": 1.0}              # red -> -1x
    tc, _ = targets_for("gmi_switch", {"R": "qqq", "D": "cash"},
                        down, 259, 0, None, U_WISH2)
    assert tc == {}                        # red -> "something else": cash


def test_gmi_switch_no_daily_churn():
    n = 260
    up = {x: [100.0] * n for x in U_WISH2}
    for x in ("qqq", "spy", "ita", "itb"):
        up[x] = [100.0 * (1.001 ** k) for k in range(n)]
    p = {"R": "qqq", "D": "inv3"}
    t1, state = targets_for("gmi_switch", p, up, 258, 0, None, U_WISH2)
    assert t1 == {"qqq": 1.0}
    t2, _ = targets_for("gmi_switch", p, up, 259, 0, state, U_WISH2)
    assert t2 is None                      # same regime the next day: hold


def test_gmi_glb_exits_to_cash_when_gmi_turns_red():
    # phase A: everything rallies, SPY makes new highs while GMI is green ->
    # gmi_glb goes long. phase B: SPY only drifts ~1% off its high (no price
    # stop) but QQQ/ITA/ITB collapse, dragging GMI red -> must exit to cash.
    A, B = 240, 40
    rise = [100.0 + (k / A) * 100.0 for k in range(A)]      # 100 -> ~200
    spy = rise + [198.0] * B                                # ~1% off peak 200
    weak = rise + [100.0] * B                               # crashes in phase B
    C = {x: [100.0] * (A + B) for x in U_WISH2}
    C["spy"] = spy
    for x in ("qqq", "ita", "itb"):
        C[x] = list(weak)
    p = {"R": "spy", "p": 0.05}
    entered, state = False, None
    for i in range(210, A + B):
        t, state = targets_for("gmi_glb", p, C, i, 210, state, U_WISH2)
        if t == {"spy": 1.0}:
            entered = True
    assert entered, "gmi_glb never entered while GMI was green"
    assert state is False, "gmi_glb did not exit to cash when GMI turned red"


def test_weights_never_exceed_one(wish_joint):
    times, closes, _ = wish_joint
    for family, grid in grids_for().items():
        for params in grid:
            for i in range(300, len(times), 137):
                t, _ = targets_for(family, params, closes, i, 0, None, U_WISH2)
                if t:
                    assert sum(t.values()) <= 1.0 + 1e-9, (family, params, i)


def test_flat_tape_never_creates_money():
    n = 500
    C = {x: [100.0] * n for x in U_WISH2}
    P = {x: [100_000_000] * n for x in U_WISH2}
    for family, grid in grids_for().items():
        cash = run_window(family, grid[0], C, P, 30, n, U_WISH2)
        assert cash <= CAPITAL_U, family


def test_gmi_count_is_none_during_warmup():
    n = 260
    C = {x: [100.0 + k for k in range(n)] for x in U_WISH2}
    assert gmi_count(C, 150) is None       # before 200 days
    assert gmi_count(C, 210) is not None


def test_forward_refuses_spent_shot(tmp_path, monkeypatch, capsys):
    shot = tmp_path / "alloc10.SHOT"
    shot.write_text("fired\n", encoding="utf-8")
    monkeypatch.setattr(allocation10, "SHOT", shot)
    rc = allocation10.main(["--holdout", "gmi_switch", "--forward"])
    assert rc == 2
    assert "already fired" in capsys.readouterr().err


def test_forward_refuses_until_ripe(tmp_path, monkeypatch, capsys):
    fwd = tmp_path / "alloc10.FORWARD"
    fwd.write_text("family: gmi_switch\nuniverse: wish2\n"
                   "cutoff: 2026-07-23\nmin_new_rows: 126\n", encoding="utf-8")
    monkeypatch.setattr(allocation10, "FORWARD", fwd)
    monkeypatch.setattr(allocation10, "SHOT", tmp_path / "alloc10.SHOT")
    rc = allocation10.main(["--holdout", "gmi_switch", "--forward"])
    assert rc == 2
    assert "not ripe" in capsys.readouterr().err


def test_forward_refuses_undeclared_family(tmp_path, monkeypatch, capsys):
    fwd = tmp_path / "alloc10.FORWARD"
    fwd.write_text("family: gmi_switch\nuniverse: wish2\n"
                   "cutoff: 2026-07-23\nmin_new_rows: 126\n", encoding="utf-8")
    monkeypatch.setattr(allocation10, "FORWARD", fwd)
    monkeypatch.setattr(allocation10, "SHOT", tmp_path / "alloc10.SHOT")
    rc = allocation10.main(["--holdout", "glb_pct", "--forward"])
    assert rc == 2
    assert "frozen" in capsys.readouterr().err


def test_forward_declaration_names_a_real_family():
    kv = allocation10.read_forward()
    assert kv["family"] in grids_for()
    assert kv["universe"] == "wish2"
    assert int(kv["min_new_rows"]) >= 126

"""spec v11 section 5: leakage, the regime gate (green->momentum, red->safe),
the synthesis's reason to exist (gated_mom brakes on the flip while the pick is
still rising; pure_mom does not), no daily churn, GMI hysteresis, weight/
conservation invariants, forward refusals, FORWARD integrity."""

import pytest

from experiments import allocation11
from experiments.allocation11 import (CAPITAL_U, RISK, U_GATE, gmi_count,
                                      grids_for, load_joint, run_window,
                                      targets_for)


@pytest.fixture(scope="module")
def gate_joint():
    return load_joint(U_GATE)


def test_signals_use_only_history(gate_joint):
    times, closes, _ = gate_joint
    i = 420  # past the 200-day GMI warmup and the 252-day momentum lookback
    corrupted = {x: closes[x][:i] + [1e12] * (len(times) - i) for x in U_GATE}
    for family, grid in grids_for().items():
        for params in grid:
            t1, _ = targets_for(family, params, closes, i, 0, None, U_GATE)
            t2, _ = targets_for(family, params, corrupted, i, 0, None, U_GATE)
            assert t1 == t2, (family, params)


def _green_tape(n=260):
    """Everything trending up: GMI green, and qqq the strongest risk asset."""
    C = {x: [100.0] * n for x in U_GATE}
    for x in ("spy", "qqq", "iwm", "efa"):
        C[x] = [100.0 * (1.001 ** k) for k in range(n)]
    C["qqq"] = [100.0 * (1.003 ** k) for k in range(n)]   # steepest -> momentum pick
    return C


def _red_tape(n=260):
    """Broad market rolling over: GMI red."""
    C = {x: [100.0] * n for x in U_GATE}
    for x in ("spy", "qqq", "iwm", "efa"):
        C[x] = [100.0 * (0.999 ** k) for k in range(n)]
    return C


def test_gated_mom_green_holds_top_momentum():
    C = _green_tape()
    t, state = targets_for("gated_mom", {"L": 126, "D": "gld"},
                           C, 259, 0, None, U_GATE)
    assert t == {"qqq": 1.0}          # GMI green -> strongest RISK asset
    assert state[0] is True


def test_gated_mom_red_steps_to_the_safe_sleeve():
    C = _red_tape()
    tg, _ = targets_for("gated_mom", {"L": 126, "D": "gld"},
                        C, 259, 0, None, U_GATE)
    assert tg == {"gld": 1.0}         # red -> gold
    tt, _ = targets_for("gated_mom", {"L": 126, "D": "tlt"},
                        C, 259, 0, None, U_GATE)
    assert tt == {"tlt": 1.0}         # red -> bonds
    tc, _ = targets_for("gated_mom", {"L": 126, "D": "cash"},
                        C, 259, 0, None, U_GATE)
    assert tc == {}                   # red -> cash


def _leader_then_crash(n=260, A=230, B=30):
    """btc leads momentum the whole way (outside GMI); the equity complex
    (spy/qqq/iwm/efa) rises then crashes, so GMI flips red at the end."""
    C = {x: [100.0] * n for x in U_GATE}
    C["btc"] = [100.0 * (1.003 ** k) for k in range(n)]     # the momentum leader
    for x in ("spy", "qqq", "iwm", "efa"):
        rise = [100.0 * (1.001 ** k) for k in range(A)]
        crash = [rise[-1] * (0.95 ** (k + 1)) for k in range(B)]
        C[x] = rise + crash
    return C, A, B, n


def test_gated_mom_brakes_on_the_flip_while_the_pick_is_still_rising():
    # the synthesis's reason to exist: btc keeps climbing right up to the flip,
    # but the equity complex collapses -> GMI red -> gated_mom must leave the
    # still-rising momentum pick for the safe sleeve.
    C, A, B, n = _leader_then_crash()
    p = {"L": 126, "D": "gld"}
    state, holds = None, {}
    for i in range(200, n):
        t, state = targets_for("gated_mom", p, C, i, 200, state, U_GATE)
        holds[i] = state
    assert holds[210][0] is True and holds[210][1] == "btc", "never rode btc"
    assert holds[n - 1][0] is False and holds[n - 1][1] == "gld", "did not brake"


def test_pure_mom_never_brakes_holds_the_pick_through_a_gmi_red_crash():
    # same tape: pure_mom has no gate, so it stays in the still-rising btc the
    # whole way -- the flaw gating fixes. On a rebalance day mid-crash it ranks
    # the RISK sleeve and picks btc; gld/tlt/cash are not even candidates.
    C, A, B, n = _leader_then_crash()
    t, _ = targets_for("pure_mom", {"L": 126}, C, A + 12, A + 12, None, U_GATE)
    assert t == {"btc": 1.0}                # holds the risk pick; never flees


def test_gated_mom_no_daily_churn_while_green():
    C = _green_tape()
    p = {"L": 126, "D": "gld"}
    t1, state = targets_for("gated_mom", p, C, 258, 0, None, U_GATE)
    assert t1 == {"qqq": 1.0}
    t2, _ = targets_for("gated_mom", p, C, 259, 0, state, U_GATE)
    assert t2 is None                # same regime, not a rebalance day: hold


def test_gmi_bh_holds_index_green_and_safe_red():
    up = _green_tape()
    t, _ = targets_for("gmi_bh", {"R": "qqq", "D": "gld"}, up, 259, 0, None, U_GATE)
    assert t == {"qqq": 1.0}
    down = _red_tape()
    t2, _ = targets_for("gmi_bh", {"R": "qqq", "D": "gld"}, down, 259, 0, None, U_GATE)
    assert t2 == {"gld": 1.0}        # no inverse in this universe -> flee to safety


def test_gmi_hysteresis_band():
    from experiments.allocation11 import _gmi_phase
    assert _gmi_phase(True, 3) is True
    assert _gmi_phase(True, 2) is False
    assert _gmi_phase(False, 3) is False
    assert _gmi_phase(False, 4) is True
    assert _gmi_phase(True, None) is False


def test_weights_never_exceed_one(gate_joint):
    times, closes, _ = gate_joint
    for family, grid in grids_for().items():
        for params in grid:
            for i in range(300, len(times), 149):
                t, _ = targets_for(family, params, closes, i, 0, None, U_GATE)
                if t:
                    assert sum(t.values()) <= 1.0 + 1e-9, (family, params, i)


def test_flat_tape_never_creates_money():
    n = 500
    C = {x: [100.0] * n for x in U_GATE}
    P = {x: [100_000_000] * n for x in U_GATE}
    for family, grid in grids_for().items():
        cash = run_window(family, grid[0], C, P, 30, n, U_GATE)
        assert cash <= CAPITAL_U, family


def test_risk_sleeve_excludes_the_safe_assets():
    assert "gld" not in RISK and "tlt" not in RISK
    assert set(RISK) <= set(U_GATE)


def test_gmi_count_is_none_during_warmup():
    n = 260
    C = {x: [100.0 + k for k in range(n)] for x in U_GATE}
    assert gmi_count(C, 150) is None
    assert gmi_count(C, 210) is not None


def test_forward_refuses_spent_shot(tmp_path, monkeypatch, capsys):
    shot = tmp_path / "alloc11.SHOT"
    shot.write_text("fired\n", encoding="utf-8")
    monkeypatch.setattr(allocation11, "SHOT", shot)
    rc = allocation11.main(["--holdout", "gated_mom", "--forward"])
    assert rc == 2
    assert "already fired" in capsys.readouterr().err


def test_forward_refuses_until_ripe(tmp_path, monkeypatch, capsys):
    fwd = tmp_path / "alloc11.FORWARD"
    fwd.write_text("family: gated_mom\nuniverse: gate\n"
                   "cutoff: 2026-07-23\nmin_new_rows: 126\n", encoding="utf-8")
    monkeypatch.setattr(allocation11, "FORWARD", fwd)
    monkeypatch.setattr(allocation11, "SHOT", tmp_path / "alloc11.SHOT")
    rc = allocation11.main(["--holdout", "gated_mom", "--forward"])
    assert rc == 2
    assert "not ripe" in capsys.readouterr().err


def test_forward_refuses_undeclared_family(tmp_path, monkeypatch, capsys):
    fwd = tmp_path / "alloc11.FORWARD"
    fwd.write_text("family: gated_mom\nuniverse: gate\n"
                   "cutoff: 2026-07-23\nmin_new_rows: 126\n", encoding="utf-8")
    monkeypatch.setattr(allocation11, "FORWARD", fwd)
    monkeypatch.setattr(allocation11, "SHOT", tmp_path / "alloc11.SHOT")
    rc = allocation11.main(["--holdout", "pure_mom", "--forward"])
    assert rc == 2
    assert "frozen" in capsys.readouterr().err


def test_historical_shot_is_refused_without_forward(capsys):
    rc = allocation11.main(["--holdout", "gated_mom"])
    assert rc == 2
    assert "every span is spent" in capsys.readouterr().err


def test_forward_declaration_names_a_real_family():
    kv = allocation11.read_forward()
    assert kv["family"] in grids_for()
    assert kv["universe"] == "gate"
    assert int(kv["min_new_rows"]) >= 126

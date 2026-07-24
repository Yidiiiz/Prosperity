"""spec v12 section 5: leakage, the risk-budget mechanics (vt_mom caps a hot
pick and never levers; rp_topk inverse-vol risk parity), weight/conservation
invariants, forward-only discipline, FORWARD integrity."""

import pytest

from experiments import allocation12
from experiments.allocation12 import (CAPITAL_U, RISK, U_RISK, grids_for,
                                      load_joint, run_window, targets_for)


@pytest.fixture(scope="module")
def risk_joint():
    return load_joint(U_RISK)


def _flat(n=260):
    return {x: [100.0] * n for x in U_RISK}


def test_signals_use_only_history(risk_joint):
    times, closes, _ = risk_joint
    i = 420  # past the 252-day momentum lookback and the 63-day vol window
    corrupted = {x: closes[x][:i] + [1e12] * (len(times) - i) for x in U_RISK}
    for family, grid in grids_for().items():
        for params in grid:
            t1, _ = targets_for(family, params, closes, i, 0, None, U_RISK)
            t2, _ = targets_for(family, params, corrupted, i, 0, None, U_RISK)
            assert t1 == t2, (family, params)


def test_vt_mom_caps_a_hot_pick():
    # btc is the momentum leader (positive 126-day return) but violently
    # volatile (~12%/day swings) -> vol targeting sizes it below full, cash rest.
    n = 260
    C = _flat(n)
    C["btc"] = [100.0 * (1.004 ** k) * (1 + 0.06 * ((-1) ** k)) for k in range(n)]
    t, _ = targets_for("vt_mom", {"L": 126, "TV": 0.40}, C, 210, 0, None, U_RISK)
    assert set(t) == {"btc"}
    assert 0.0 < t["btc"] < 1.0            # capped below full; remainder is cash
    assert sum(t.values()) < 1.0


def test_vt_mom_low_vol_pick_stays_full():
    # a smoothly rising leader (~0.3%/day, tiny realized vol) needs no de-risking
    n = 260
    C = _flat(n)
    C["qqq"] = [100.0 * (1.003 ** k) for k in range(n)]
    t, _ = targets_for("vt_mom", {"L": 126, "TV": 0.40}, C, 210, 0, None, U_RISK)
    assert t == {"qqq": 1.0}


def test_vt_mom_never_levers_even_with_a_loose_target():
    # TV far above realized vol would imply w>1; the min(1.0, .) clamp holds.
    n = 260
    C = _flat(n)
    C["qqq"] = [100.0 * (1.003 ** k) for k in range(n)]
    t, _ = targets_for("vt_mom", {"L": 126, "TV": 0.80}, C, 210, 0, None, U_RISK)
    assert t == {"qqq": 1.0}
    assert t["qqq"] <= 1.0


def test_vt_mom_cash_when_no_positive_momentum():
    n = 260
    C = {x: [100.0 * (0.999 ** k) for k in range(n)] for x in U_RISK}
    t, _ = targets_for("vt_mom", {"L": 126, "TV": 0.40}, C, 210, 0, None, U_RISK)
    assert t == {}


def test_rp_topk_weights_the_calmer_leg_heavier():
    # eth and btc both rise (top-2 momentum); eth is far more volatile, so risk
    # parity gives the calmer btc the larger weight; weights sum to 1.
    n = 260
    C = _flat(n)
    C["btc"] = [100.0 * (1.003 ** k) for k in range(n)]                    # calm
    C["eth"] = [100.0 * (1.0035 ** k) * (1 + 0.05 * ((-1) ** k))           # wild
                for k in range(n)]
    t, _ = targets_for("rp_topk", {"K": 2, "L": 126}, C, 210, 0, None, U_RISK)
    assert set(t) == {"btc", "eth"}
    assert t["btc"] > t["eth"]
    assert abs(sum(t.values()) - 1.0) < 1e-9


def test_rp_topk_cash_when_no_positive_momentum():
    n = 260
    C = {x: [100.0 * (0.999 ** k) for k in range(n)] for x in U_RISK}
    t, _ = targets_for("rp_topk", {"K": 3, "L": 126}, C, 210, 0, None, U_RISK)
    assert t == {}


def test_pure_mom_is_full_size_top_one():
    n = 260
    C = _flat(n)
    C["qqq"] = [100.0 * (1.003 ** k) for k in range(n)]
    t, _ = targets_for("pure_mom", {"L": 126}, C, 210, 0, None, U_RISK)
    assert t == {"qqq": 1.0}


def test_weights_never_exceed_one(risk_joint):
    times, closes, _ = risk_joint
    for family, grid in grids_for().items():
        for params in grid:
            for i in range(300, len(times), 149):
                t, _ = targets_for(family, params, closes, i, 0, None, U_RISK)
                if t:
                    assert sum(t.values()) <= 1.0 + 1e-9, (family, params, i)


def test_flat_tape_never_creates_money():
    n = 500
    C = _flat(n)
    P = {x: [100_000_000] * n for x in U_RISK}
    for family, grid in grids_for().items():
        cash = run_window(family, grid[0], C, P, 30, n, U_RISK)
        assert cash <= CAPITAL_U, family


def test_risk_sleeve_excludes_the_safe_assets():
    assert "gld" not in RISK and "tlt" not in RISK
    assert set(RISK) <= set(U_RISK)


def test_forward_refuses_spent_shot(tmp_path, monkeypatch, capsys):
    shot = tmp_path / "alloc12.SHOT"
    shot.write_text("fired\n", encoding="utf-8")
    monkeypatch.setattr(allocation12, "SHOT", shot)
    rc = allocation12.main(["--holdout", "vt_mom", "--forward"])
    assert rc == 2
    assert "already fired" in capsys.readouterr().err


def test_forward_refuses_until_ripe(tmp_path, monkeypatch, capsys):
    fwd = tmp_path / "alloc12.FORWARD"
    fwd.write_text("family: vt_mom\nuniverse: risk\n"
                   "cutoff: 2026-07-23\nmin_new_rows: 126\n", encoding="utf-8")
    monkeypatch.setattr(allocation12, "FORWARD", fwd)
    monkeypatch.setattr(allocation12, "SHOT", tmp_path / "alloc12.SHOT")
    rc = allocation12.main(["--holdout", "vt_mom", "--forward"])
    assert rc == 2
    assert "not ripe" in capsys.readouterr().err


def test_forward_refuses_undeclared_family(tmp_path, monkeypatch, capsys):
    fwd = tmp_path / "alloc12.FORWARD"
    fwd.write_text("family: vt_mom\nuniverse: risk\n"
                   "cutoff: 2026-07-23\nmin_new_rows: 126\n", encoding="utf-8")
    monkeypatch.setattr(allocation12, "FORWARD", fwd)
    monkeypatch.setattr(allocation12, "SHOT", tmp_path / "alloc12.SHOT")
    rc = allocation12.main(["--holdout", "pure_mom", "--forward"])
    assert rc == 2
    assert "frozen" in capsys.readouterr().err


def test_historical_shot_is_refused_without_forward(capsys):
    rc = allocation12.main(["--holdout", "vt_mom"])
    assert rc == 2
    assert "every span is spent" in capsys.readouterr().err


def test_forward_declaration_names_a_real_family():
    kv = allocation12.read_forward()
    assert kv["family"] in grids_for()
    assert kv["universe"] == "risk"
    assert int(kv["min_new_rows"]) >= 126

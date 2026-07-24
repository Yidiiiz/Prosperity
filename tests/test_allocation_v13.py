"""v13 cross-section bench: masking (unlisted names are invisible), the 1-day
signal lag, the cross-sectional families, the ew_all survivorship control, and
the forward-holdout refusals. All synthetic and offline — the real stock tapes
are operator-fetched and gitignored, so nothing here touches disk except the
committed FORWARD declaration and a couple of tmp CSVs."""

import datetime as dt

from experiments import allocation13 as a13
from experiments.allocation13 import (momentum_ranked, realized_daily_vol,
                                       targets_for, rebalance, run_window,
                                       load_masked)
from experiments.allocation import CAPITAL_U
from colony.arenas.replay import to_price_u

U = ("a", "b", "c", "d", "e", "f")


def ramp(rate, n=400, base=100.0):
    return [base * (rate ** k) for k in range(n)]


def prices_of(closes, U=U):
    return {x: [None if c is None else to_price_u(c, 100) for c in closes[x]]
            for x in U}


# ---- masking ---------------------------------------------------------------

def test_momentum_ranked_excludes_unlisted():
    closes = {x: ramp(1.0) for x in U}
    closes["a"] = ramp(1.003)          # strongest, but...
    closes["a"] = [None] * 200 + ramp(1.003, n=200)   # ...unlisted until day 200
    closes["b"] = ramp(1.002)
    ranked = momentum_ranked(closes, 150, 63, U)
    names = [x for _, x in ranked]
    assert "a" not in names            # no history at day 150 -> invisible
    assert names[0] == "b"


def test_momentum_ranked_orders_by_trailing_return():
    closes = {x: ramp(1.0) for x in U}
    closes["c"] = ramp(1.004)
    closes["d"] = ramp(1.002)
    ranked = momentum_ranked(closes, 300, 126, U)
    assert ranked[0][1] == "c" and ranked[1][1] == "d"


# ---- 1-day lag / no leakage ------------------------------------------------

def test_signal_uses_prior_close_not_today():
    # A spike at day i-1 (inside the window) pulls 'd' in; the SAME spike moved
    # to day i (today) must be invisible — the boundary is exactly i-1.
    i = 84                             # rebalance day, and i-1-L >= 0
    base = {x: ramp(1.0) for x in U}   # all flat -> return 0, none selected
    prior = {x: list(v) for x, v in base.items()}
    prior["d"][i - 1] = 1e7            # spike inside the trailing window
    t_prior, _ = targets_for("xs_topk", {"K": 1, "L": 63}, prior, i, 0, None, U)
    today = {x: list(v) for x, v in base.items()}
    today["d"][i] = 1e7               # spike on the fill day -> must be ignored
    t_today, _ = targets_for("xs_topk", {"K": 1, "L": 63}, today, i, 0, None, U)
    assert "d" in t_prior and "d" not in t_today


# ---- xs_topk ---------------------------------------------------------------

def test_xs_topk_top_k_equal_weight():
    closes = {x: ramp(1.0 + 0.0005 * k) for k, x in enumerate(U)}  # all rising
    t, _ = targets_for("xs_topk", {"K": 3, "L": 63}, closes, 84, 0, None, U)
    assert len(t) == 3 and all(abs(w - 1 / 3) < 1e-9 for w in t.values())


def test_xs_topk_shortfall_is_cash():
    closes = {x: ramp(0.998) for x in U}   # all falling
    closes["a"] = ramp(1.003)
    closes["b"] = ramp(1.002)              # only two positive
    t, _ = targets_for("xs_topk", {"K": 5, "L": 63}, closes, 84, 0, None, U)
    assert set(t) == {"a", "b"}
    assert abs(sum(t.values()) - 2 / 5) < 1e-9      # 3/5 left in cash


def test_xs_topk_all_negative_is_cash():
    closes = {x: ramp(0.997) for x in U}
    t, _ = targets_for("xs_topk", {"K": 3, "L": 63}, closes, 84, 0, None, U)
    assert t == {}


def test_families_never_lever():
    closes = {x: ramp(1.0 + 0.0006 * k) for k, x in enumerate(U)}
    for fam, p in (("xs_topk", {"K": 5, "L": 63}),
                   ("xs_invvol", {"K": 5, "L": 63}), ("ew_all", {})):
        t, _ = targets_for(fam, p, closes, 84, 0, None, U)
        assert sum(t.values()) <= 1.0 + 1e-9


def test_non_rebalance_day_holds():
    closes = {x: ramp(1.001) for x in U}
    t, _ = targets_for("xs_topk", {"K": 3, "L": 63}, closes, 10, 0, None, U)
    assert t is None                   # 10 % 21 != 0


# ---- xs_invvol -------------------------------------------------------------

def test_xs_invvol_full_and_calmer_leg_heavier():
    # two winners: 'a' calm, 'b' choppy; both net up. inv-vol tilts to 'a'.
    n = 400
    calm = [100.0 * (1.0008 ** k) for k in range(n)]
    choppy = [100.0 * (1.0008 ** k) * (1.05 if k % 2 else 0.95) for k in range(n)]
    closes = {x: ramp(0.997) for x in U}
    closes["a"], closes["b"] = calm, choppy
    t, _ = targets_for("xs_invvol", {"K": 2, "L": 126}, closes, 294, 0, None, U)
    assert set(t) == {"a", "b"}
    assert abs(sum(t.values()) - 1.0) < 1e-9        # fully invested
    assert t["a"] > t["b"]                          # calmer leg heavier


def test_xs_invvol_cash_when_none_positive():
    closes = {x: ramp(0.996) for x in U}
    t, _ = targets_for("xs_invvol", {"K": 3, "L": 63}, closes, 84, 0, None, U)
    assert t == {}


# ---- ew_all survivorship control -------------------------------------------

def test_ew_all_equal_weight_over_listed_only():
    closes = {x: ramp(1.0) for x in U}
    closes["f"] = [None] * 300 + ramp(1.0, n=100)   # unlisted at the decision
    t, _ = targets_for("ew_all", {}, closes, 42, 0, None, U)   # rebal day
    assert "f" not in t
    assert len(t) == 5 and all(abs(w - 1 / 5) < 1e-9 for w in t.values())


# ---- realized vol on masked gaps -------------------------------------------

def test_realized_vol_none_on_unlisted_gap():
    closes = {x: ramp(1.001) for x in U}
    closes["a"] = [None] * 50 + ramp(1.001, n=350)
    assert realized_daily_vol(closes, "a", 40, 63) is None      # window hits None
    assert realized_daily_vol(closes, "a", 300, 63) is not None


# ---- money conservation with an unlisted leg -------------------------------

def test_rebalance_conserves_and_skips_unlisted():
    closes = {x: ramp(1.0) for x in U}
    closes["f"] = [None] * 400          # never lists
    P = prices_of(closes)
    lots = {x: 0 for x in U}
    Pi = {x: P[x][100] for x in U}
    cash = rebalance(CAPITAL_U, lots, {"a": 0.5, "b": 0.5}, Pi, a13.BASE_VENUE, U)
    assert lots["f"] == 0               # unlisted never bought
    equity = cash + sum(lots[x] * Pi[x] for x in U if Pi[x] is not None)
    assert equity <= CAPITAL_U          # entry cannot mint money
    assert 0 <= cash <= CAPITAL_U


def test_run_window_stays_within_capital_ballpark():
    closes = {x: ramp(1.0) for x in U}          # flat tape
    P = prices_of(closes)
    final = run_window("xs_topk", {"K": 3, "L": 63}, closes, P, 0, 380, U)
    assert final <= CAPITAL_U           # flat prices + tolls -> no free money


# ---- masked loader end to end (tmp CSVs, offline) --------------------------

def _write_csv(path, start, closes):
    d0 = dt.date.fromisoformat(start)
    lines = ["Date,Close"]
    for k, c in enumerate(closes):
        lines.append(f"{(d0 + dt.timedelta(days=k)).isoformat()},{c}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_masked_marks_none_before_listing(tmp_path, monkeypatch):
    spy = tmp_path / "spy.csv"
    early = tmp_path / "early.csv"
    late = tmp_path / "late.csv"
    _write_csv(spy, "2020-01-01", [100 + k for k in range(200)])
    _write_csv(early, "2020-01-01", [50 + k for k in range(200)])
    _write_csv(late, "2020-04-01", [10 + k for k in range(100)])   # lists later
    monkeypatch.setattr(a13, "SPY_CSV", spy)
    monkeypatch.setattr(a13, "STOCKS",
                        {"early": (str(early), 100), "late": (str(late), 100)})
    monkeypatch.setattr(a13, "ROOT", tmp_path)
    times, closes, _ = load_masked(("early", "late"))
    assert closes["early"][0] is not None
    assert closes["late"][0] is None                # not yet listed on day 0
    assert closes["late"][-1] is not None           # listed by the end


# ---- forward-holdout discipline --------------------------------------------

def test_read_forward_names_xs_topk():
    fwd = a13.read_forward()            # the committed declaration
    assert fwd["family"] == "xs_topk"
    assert int(fwd["min_new_rows"]) >= 1


def test_holdout_refused_without_forward(capsys):
    rc = a13.main(["--holdout", "xs_topk"])         # returns before touching disk
    assert rc == 2
    assert "--forward" in capsys.readouterr().err

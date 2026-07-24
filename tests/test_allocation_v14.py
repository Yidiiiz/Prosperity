"""v14 survivorship stress bench: the 12-1 skip signal (xs_skip), the SPY-regime
sampler, the synthetic graveyard generator (deterministic; rise-then-collapse-
then-delist; a held phantom realizes the loss), and the forward-holdout
refusals. All synthetic and offline — no real tape is touched."""

import datetime as dt

from experiments import allocation14 as a14
from experiments.allocation14 import (targets_for, run_equity, make_phantoms,
                                       calibrate, grids_for, run_window, SKIP)
from experiments.allocation import CAPITAL_U
from colony.arenas.replay import to_price_u

U = ("a", "b", "c", "d", "e", "f")


def ramp(rate, n=600, base=100.0):
    return [base * (rate ** k) for k in range(n)]


def prices_of(closes, U=U):
    return {x: [None if c is None else to_price_u(c, 100) for c in closes[x]]
            for x in U}


# ---- xs_skip: the 12-1 signal skips the most recent month -------------------

def test_xs_skip_ignores_the_recent_skip_window():
    # 'd' is flat for its whole history EXCEPT a jump inside the last SKIP days.
    # Raw xs_topk (skip=0) sees the jump and picks it; xs_skip (skip=21) does
    # not, because its window ends SKIP days before today.
    i = 21 * 14                                    # rebalance day, ample warmup
    closes = {x: ramp(1.0) for x in U}             # all flat -> return 0
    for k in range(i - SKIP, i):                   # jump only in the last month
        closes["d"][k] = 1e6
    raw, _ = targets_for("xs_topk", {"K": 1, "L": 252}, closes, i, 0, None, U)
    skip, _ = targets_for("xs_skip", {"K": 1, "L": 252}, closes, i, 0, None, U)
    assert "d" in raw                              # recent jump chased
    assert "d" not in skip                         # recent jump skipped over


def test_xs_skip_sees_older_momentum():
    # A name that rose in the OLDER part of the window (before the skip gap) is
    # selected by xs_skip.
    i = 21 * 14
    closes = {x: ramp(1.0) for x in U}
    closes["c"] = ramp(1.002)                      # steady old + recent uptrend
    skip, _ = targets_for("xs_skip", {"K": 1, "L": 252}, closes, i, 0, None, U)
    assert "c" in skip


def test_xs_skip_shortfall_and_all_negative_are_cash():
    closes = {x: ramp(0.998) for x in U}           # all falling
    closes["a"] = ramp(1.003)
    t, _ = targets_for("xs_skip", {"K": 5, "L": 252}, closes, 21 * 14, 0, None, U)
    assert set(t) == {"a"} and abs(sum(t.values()) - 1 / 5) < 1e-9
    allneg, _ = targets_for("xs_skip", {"K": 3, "L": 252},
                            {x: ramp(0.997) for x in U}, 21 * 14, 0, None, U)
    assert allneg == {}


def test_grids_have_xs_skip_with_L_above_skip():
    g = grids_for(U)
    assert "xs_skip" in g
    assert all(p["L"] > SKIP for p in g["xs_skip"])   # skip endpoint never wraps


def test_xs_skip_never_levers():
    closes = {x: ramp(1.0 + 0.0006 * k) for k, x in enumerate(U)}
    t, _ = targets_for("xs_skip", {"K": 5, "L": 126}, closes, 21 * 14, 0, None, U)
    assert sum(t.values()) <= 1.0 + 1e-9


# ---- run_equity: a per-day mark-to-market curve ----------------------------

def test_run_equity_length_matches_window():
    closes = {x: ramp(1.0) for x in U}
    P = prices_of(closes)
    curve = run_equity("ew_all", {}, closes, P, 100, 300, U)
    assert len(curve) == 200


# ---- SPY regime sampler ----------------------------------------------------

def _write_csv(path, start, closes):
    d0 = dt.date.fromisoformat(start)
    lines = ["Date,Close"]
    for k, c in enumerate(closes):
        lines.append(f"{(d0 + dt.timedelta(days=k)).isoformat()},{c}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_spy_on_samples_latest_close_leq_day(tmp_path, monkeypatch):
    spy = tmp_path / "spy.csv"
    _write_csv(spy, "2020-01-01", [100 + k for k in range(50)])
    monkeypatch.setattr(a14, "SPY_CSV", spy)
    from colony.arenas.replay import read_rows
    times, _ = read_rows(spy)
    sampled = a14.spy_on(times[:10])
    assert sampled == [100 + k for k in range(10)]


# ---- synthetic graveyard generator -----------------------------------------

def test_make_phantoms_is_deterministic_under_seed():
    import random
    times = list(range(2000))
    a = make_phantoms(random.Random(42), 3, times, 0.0005, 0.02, 40)
    b = make_phantoms(random.Random(42), 3, times, 0.0005, 0.02, 40)
    assert a == b and len(a) == 3


def test_phantom_rises_then_collapses_then_delists():
    import random
    times = list(range(2000))
    ph = make_phantoms(random.Random(1), 1, times, 0.0006, 0.015, 40)
    series = next(iter(ph.values()))
    live = [(k, c) for k, c in enumerate(series) if c is not None]
    assert live[0][0] > 0                          # lists after day 0
    peak = max(c for _, c in live)
    assert live[-1][1] < 0.10 * peak               # ends near-zero (collapsed)
    last_live = live[-1][0]
    assert last_live + 1 >= len(series) or series[last_live + 1] is None  # delists


def test_held_phantom_realizes_the_loss():
    # Buy-and-hold a single phantom through its collapse+delisting: a name that
    # goes to ~5% and then delists cannot be recovered, so equity ends far below
    # the $10,000 start. (ew_all on a one-name universe = full hold.)
    import random
    times = list(range(2000))
    ph = make_phantoms(random.Random(3), 1, times, 0.0005, 0.02, 1)  # 1-day gap
    name = next(iter(ph))
    closes = {name: ph[name]}
    P = {name: [None if c is None else to_price_u(c, 100) for c in ph[name]]}
    final = run_window("ew_all", {}, closes, P, 0, len(times), (name,))
    assert final < CAPITAL_U                        # the landmine cost money


def test_calibrate_returns_plausible_daily_stats():
    closes = {x: ramp(1.0004) for x in U}           # steady climbers
    mu, sig = calibrate(closes, U, list(range(600)))
    assert 0.0 < mu < 0.01 and 0.0 <= sig < 0.1


# ---- forward-holdout discipline --------------------------------------------

def test_holdout_refused_without_forward(capsys):
    rc = a14.main(["--holdout", "xs_skip"])         # returns before touching disk
    assert rc == 2
    assert "--forward" in capsys.readouterr().err


def test_read_forward_raises_when_unarmed():
    # v14 arms no forward (xs_skip is not the frontier); the declaration is
    # absent, so read_forward refuses rather than inventing a target.
    import pytest
    if a14.FORWARD.exists():
        assert a14.read_forward()["family"]         # if armed later, must parse
    else:
        with pytest.raises(SystemExit):
            a14.read_forward()

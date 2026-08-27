"""v15 wish bench: GMI-real's six components and its two-day confirmation, the
gate's A/B integrity, Wish's GLB in the cross-section (the 3-month-old green
line, the stop set under the breakout and ratcheted up, the fresh-breakout
re-entry rule), the RWB fan / RLC entry-exit, and the forward-holdout refusals.
All synthetic and offline — no real tape is touched."""

import pytest

from experiments import allocation15 as a15
from experiments.allocation15 import (
    build_ctx, gmi_components, gmi_count, gmi_phase, targets_for, grids_for,
    rolling_new_high, rolling_above_sma, rwb_arrays, glb_arrays, run_window,
    GATE_PARAMS, GATE_DEFAULT, GLB_HOLD, NH_LOOKBACK, SUCC_LAG, RWB_PAIRS,
)
from experiments.allocation import CAPITAL_U
from colony.arenas.replay import to_price_u

U = ("a", "b", "c", "d", "e", "f")
N = 700
WARM = NH_LOOKBACK + SUCC_LAG          # first day GMI-real is computable


def ramp(rate, n=N, base=100.0):
    return [base * (rate ** k) for k in range(n)]


def prices_of(closes, U=U):
    return {x: [None if c is None else to_price_u(c, 100) for c in closes[x]]
            for x in U}


def ctx_for(closes, qqq, spy, U=U):
    return build_ctx(closes, U, {"qqq": qqq, "spy": spy})


UP, DOWN = ramp(1.001), ramp(0.999)


# ---- GMI-real: every component fires, and stays dark on its negation --------

def test_all_six_components_fire_on_a_rising_tape():
    ctx = ctx_for({x: ramp(1.001) for x in U}, UP, UP)
    parts = gmi_components(ctx, N - 1, 0.025)
    assert parts is not None and all(parts)          # 6 of 6
    assert gmi_count(ctx, N - 1, 0.025) == 6


def test_all_six_components_are_dark_on_a_falling_tape():
    ctx = ctx_for({x: ramp(0.999) for x in U}, DOWN, DOWN)
    parts = gmi_components(ctx, N - 1, 0.025)
    assert parts is not None and not any(parts)
    assert gmi_count(ctx, N - 1, 0.025) == 0


def test_breadth_and_index_components_are_independent():
    # Stocks fall (breadth components 1, 2, 6 dark) while QQQ/SPY rise
    # (index components 3, 4, 5 lit) — exactly the divergence the real GMI is
    # built to see and an index-MA proxy cannot.
    ctx = ctx_for({x: ramp(0.999) for x in U}, UP, UP)
    succ, nh, qd, sd, qw, t2108 = gmi_components(ctx, N - 1, 0.025)
    assert not succ and not nh and not t2108
    assert qd and sd and qw
    assert gmi_count(ctx, N - 1, 0.025) == 3


def test_qqq_daily_needs_both_halves_of_wishs_rule():
    # Above the 10-day EMA but NOT above the close 5 days ago: a tape that
    # rallies then stalls flat. Component 3 must stay dark.
    stalled = ramp(1.002, N - 30) + [ramp(1.002, N - 30)[-1]] * 30
    ctx = ctx_for({x: ramp(1.001) for x in U}, stalled, UP)
    assert ctx["qqq_daily"][N - 1] is False
    assert ctx["spy_daily"][N - 1] is True


def test_count_is_none_through_warmup():
    ctx = ctx_for({x: ramp(1.001) for x in U}, UP, UP)
    assert gmi_count(ctx, WARM - 1, 0.025) is None    # new-high lookback unripe
    assert gmi_count(ctx, WARM, 0.025) is not None


# ---- the breadth threshold B, and masking -----------------------------------

def test_B_scales_the_threshold_with_the_listed_count():
    # One name of six at a new high = 16.7% breadth: clears B=0.025 and B=0.10,
    # fails B=0.25.
    closes = {x: ramp(0.999) for x in U}
    closes["a"] = ramp(1.001)
    ctx = ctx_for(closes, DOWN, DOWN)
    assert abs(ctx["frac_nh"][N - 1] - 1 / 6) < 1e-9
    assert gmi_components(ctx, N - 1, 0.10)[1] is True
    assert gmi_components(ctx, N - 1, 0.25)[1] is False


def test_unlisted_names_are_invisible_to_breadth():
    # 'f' is unlisted throughout; breadth is a fraction of the FIVE listed
    # names, not of the six-name universe.
    closes = {x: ramp(0.999) for x in U}
    closes["a"] = ramp(1.001)
    closes["f"] = [None] * N
    ctx = ctx_for(closes, DOWN, DOWN)
    assert abs(ctx["frac_nh"][N - 1] - 1 / 5) < 1e-9


# ---- Wish's two-day confirmation -------------------------------------------

def test_two_day_confirmation_ignores_a_one_day_dip(monkeypatch):
    counts = [6] * 20 + [1] + [6] * 20                   # single red day
    ctx = {"n": len(counts), "phase": {}}
    monkeypatch.setattr(a15, "gmi_count", lambda c, j, B: counts[j])
    phase = gmi_phase(ctx, 0.025, 4)
    assert phase[19] is True
    assert phase[20] is True                             # one dip does not flip
    assert phase[21] is True


def test_two_consecutive_red_days_flip_the_signal(monkeypatch):
    counts = [6] * 20 + [1, 1] + [6] * 20
    ctx = {"n": len(counts), "phase": {}}
    monkeypatch.setattr(a15, "gmi_count", lambda c, j, B: counts[j])
    phase = gmi_phase(ctx, 0.025, 4)
    assert phase[20] is False or phase[21] is False
    assert phase[21] is False                            # confirmed red
    assert phase[23] is True                             # and confirmed back


def test_green_needs_two_days_above_the_level(monkeypatch):
    counts = [0] * 20 + [5, 0, 5, 5] + [5] * 10
    ctx = {"n": len(counts), "phase": {}}
    monkeypatch.setattr(a15, "gmi_count", lambda c, j, B: counts[j])
    phase = gmi_phase(ctx, 0.025, 4)
    assert phase[20] is False                            # one green day: no
    assert phase[23] is True                             # two in a row: yes


# ---- the gate: A/B integrity ------------------------------------------------

def _forced_ctx(closes, green_flags):
    ctx = ctx_for(closes, UP, UP)
    ctx["phase"][(GATE_DEFAULT["B"], GATE_DEFAULT["GREEN"])] = green_flags
    return ctx


def test_gate_open_reproduces_the_ungated_book_exactly():
    closes = {x: ramp(1.0 + 0.0002 * k) for k, x in enumerate(U)}
    ctx = _forced_ctx(closes, [True] * N)
    for i in (300, 300 + 21, 300 + 42):
        gated, _ = targets_for("wish_gmi", GATE_DEFAULT, closes, i, 300, (True,),
                               U, ctx)
        plain, _ = targets_for("xs_topk", GATE_PARAMS, closes, i, 300, None,
                               U, ctx)
        assert gated == plain            # while green the gate is a no-op

def test_gate_red_holds_cash_without_daily_churn():
    closes = {x: ramp(1.001) for x in U}
    ctx = _forced_ctx(closes, [False] * N)
    first, st = targets_for("wish_gmi", GATE_DEFAULT, closes, 300, 300, None,
                            U, ctx)
    assert first == {}                                   # flat on the red day
    again, _ = targets_for("wish_gmi", GATE_DEFAULT, closes, 301, 300, st, U, ctx)
    assert again is None                                 # no churn while red


def test_gate_reenters_off_cadence_on_the_green_flip():
    closes = {x: ramp(1.001) for x in U}
    flags = [False] * N
    flags[409] = True                                    # signal read at i-1
    ctx = _forced_ctx(closes, flags)
    assert (410 - 300) % a15.REBAL != 0                  # NOT a rebalance day
    t, _ = targets_for("wish_gmi", GATE_DEFAULT, closes, 410, 300, (False,),
                       U, ctx)
    assert t and abs(sum(t.values()) - 1.0) < 1e-9       # re-entered anyway


def test_gate_reads_the_signal_with_the_house_one_day_lag():
    closes = {x: ramp(1.001) for x in U}
    flags = [False] * N
    flags[420] = True
    ctx = _forced_ctx(closes, flags)
    same_day, _ = targets_for("wish_gmi", GATE_DEFAULT, closes, 420, 300,
                              (False,), U, ctx)
    next_day, _ = targets_for("wish_gmi", GATE_DEFAULT, closes, 421, 300,
                              (False,), U, ctx)
    assert same_day is None          # today's flag unseen: still flat, no churn
    assert next_day                  # yesterday's flag is what buys


# ---- GLB in the cross-section ----------------------------------------------

def test_green_line_must_be_three_months_old():
    # A high set 30 days ago is NOT a green line; the same high set GLB_HOLD+
    # days ago is. This is the "unpenetrated for three months" rule.
    flat = [100.0] * N
    flat[N - 31] = 150.0                                 # recent spike
    green, ath, _bo, _ls = glb_arrays(flat)
    assert green[N - 1] == 100.0                         # spike too recent
    assert ath[N - 1] == 150.0                           # but it IS the ATH
    old = [100.0] * N
    old[100] = 150.0
    green2, _a, _b, _l = glb_arrays(old)
    assert green2[N - 1] == 150.0                        # aged into the line


def test_stop_is_set_under_the_breakout_not_under_the_ath():
    # The operator's rule: "put a stop loss 5% under every time you do the
    # green line breakout." A breakout that opens 30% above its green line is
    # at its own all-time high, so an ATH-anchored stop would sit at 123.5 —
    # ABOVE the 100 green line, ejecting on the first retest. The entry-anchored
    # stop sits at 95, leaving room to retest and hold.
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * (N - 1) + [130.0]            # breaks out today
    ctx = ctx_for(closes, UP, UP)
    _t, state = targets_for("glb_xs", {"K": 5, "STOP": 0.05, "ANCHOR": "green"},
                            closes, N, 0, ({}, {}), U, ctx)
    assert state[0]["a"] == pytest.approx(95.0)             # 5% under the LINE
    assert state[0]["a"] < 100.0                            # ...survives a retest
    _t2, s2 = targets_for("glb_xs", {"K": 5, "STOP": 0.05, "ANCHOR": "entry"},
                          closes, N, 0, ({}, {}), U, ctx)
    assert s2[0]["a"] == pytest.approx(123.5)               # 5% under the CLOSE


def test_stop_ratchets_up_as_the_stock_increases_and_never_falls():
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * 400 + [130.0] * 50 + [200.0] * (N - 450)
    ctx = ctx_for(closes, UP, UP)
    p = {"K": 5, "STOP": 0.05, "ANCHOR": "green"}
    _t, st = targets_for("glb_xs", p, closes, 402, 0, ({}, {}), U, ctx)
    assert st[0]["a"] == pytest.approx(95.0)                # entry stop
    for i in range(403, 460):                            # ride it up
        _t, st = targets_for("glb_xs", p, closes, i, 0, st, U, ctx)
    assert st[0]["a"] == pytest.approx(190.0)               # raised: 5% under 200
    before = st[0]["a"]
    closes["a"][470] = 195.0                             # a dip that holds
    _t, st = targets_for("glb_xs", p, closes, 471, 0, st, U, ctx)
    assert st[0]["a"] == before                             # never lowered
    assert "a" in st[0]                                     # 195 > 190: still held


def test_a_close_through_the_ratcheted_stop_exits():
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * 400 + [200.0] * (N - 400)
    ctx = ctx_for(closes, UP, UP)
    p = {"K": 5, "STOP": 0.05, "ANCHOR": "green"}
    _t, st = targets_for("glb_xs", p, closes, 402, 0, ({}, {}), U, ctx)
    _t, st = targets_for("glb_xs", p, closes, 430, 0, st, U, ctx)
    assert st[0]["a"] == pytest.approx(190.0)
    closes["a"][440] = 189.0                             # through the stop
    t, st = targets_for("glb_xs", p, closes, 441, 0, st, U, ctx)
    assert "a" not in st[0] and t == {}                     # sold, book is cash


def test_a_stopped_out_name_needs_a_FRESH_breakout_to_come_back():
    # A stopped-out name is still sitting above its (old, lower) green line, so
    # a naive eligibility test re-buys it the very next day and the stop means
    # nothing. It must break out again to qualify.
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * 400 + [200.0] * 40 + [180.0] * (N - 440)
    ctx = ctx_for(closes, UP, UP)
    p = {"K": 5, "STOP": 0.05, "ANCHOR": "green"}
    _t, st = targets_for("glb_xs", p, closes, 402, 0, ({}, {}), U, ctx)
    for i in range(403, 442):
        _t, st = targets_for("glb_xs", p, closes, i, 0, st, U, ctx)
    assert "a" not in st[0]                              # stopped out at 180
    green, _a, _b, _l = ctx["glb"]["a"]
    assert closes["a"][450] > green[450]                 # yet still ABOVE the line
    t, st = targets_for("glb_xs", p, closes, 451, 0, st, U, ctx)
    assert "a" not in st[0] and t in ({}, None)          # and stays out


# ---- glb_sel: only take the good-looking breakouts --------------------------

def _two_breakouts():
    """Both names are GLB-ELIGIBLE on the last day, but only one looks right.

    'a' has climbed steadily for 300 days: a perfect RWB fan, RLC 6.
    'b' sat flat for years, popped inside the last 60 days (so it clears a green
    line that is still down at 100), and has been sliding for the last 19 — it
    qualifies on the breakout rule and its chart is broken.
    """
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * 400 + [100.0 * 1.004 ** k for k in range(N - 400)]
    closes["b"] = ([100.0] * 641
                   + [100.0 + k for k in range(1, 41)]      # 101 -> 140
                   + [140.0 - 0.79 * k for k in range(1, N - 681 + 1)])
    return closes


def test_unselective_glb_takes_the_ugly_breakout_and_selective_does_not():
    closes = _two_breakouts()
    ctx = ctx_for(closes, UP, UP)
    j = N - 1
    green, _a, bo, _l = ctx["glb"]["b"]
    assert closes["b"][j] > green[j] and bo[j] is not None   # b IS eligible
    _s, rlc = ctx["rwb"]["b"]
    assert rlc[j] < 6                                        # ...but broken
    _t1, st1 = targets_for("glb_xs", {"K": 5, "STOP": 0.10, "ANCHOR": "green"},
                           closes, j + 1, 0, ({}, {}), U, ctx)
    _t2, st2 = targets_for("glb_sel", {"K": 5, "STOP": 0.10, "QUAL": "rwb"},
                           closes, j + 1, 0, ({}, {}), U, ctx)
    assert "b" in st1[0]                                     # takes anything
    assert "b" not in st2[0] and "a" in st2[0]               # takes the good one


def test_selective_glb_ranks_the_best_looking_first():
    closes = _two_breakouts()
    ctx = ctx_for(closes, UP, UP)
    for qual in ("rwb", "mom", "both"):
        _t, st = targets_for("glb_sel", {"K": 1, "STOP": 0.10, "QUAL": qual},
                             closes, N, 0, ({}, {}), U, ctx)
        assert tuple(st[0]) == ("a",)      # the one slot goes to the good chart


def test_momentum_can_only_RANK_glb_candidates_never_reject_them():
    # A structural fact worth pinning: a name eligible for a green-line breakout
    # closes above EVERY close older than GLB_HOLD days, and QUAL_L > GLB_HOLD,
    # so its trailing QUAL_L return is positive BY CONSTRUCTION. QUAL='mom'
    # therefore orders the candidates; it can never veto one. Only the RWB fan
    # actually rejects.
    assert a15.QUAL_L > GLB_HOLD
    closes = _two_breakouts()
    ctx = ctx_for(closes, UP, UP)
    j = N - 1
    for x in ("a", "b"):
        green, _a, _b, _l = ctx["glb"][x]
        assert closes[x][j] > green[j]                       # eligible
        assert closes[x][j] / closes[x][j - a15.QUAL_L] - 1.0 > 0
    _t, st = targets_for("glb_sel", {"K": 5, "STOP": 0.10, "QUAL": "mom"},
                         closes, j + 1, 0, ({}, {}), U, ctx)
    assert "a" in st[0] and "b" in st[0]                     # both kept, ranked


def test_glb_sel_defaults_the_frozen_anchor_to_the_green_line():
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * (N - 1) + [130.0]
    ctx = ctx_for(closes, UP, UP)
    _t, st = targets_for("glb_sel", {"K": 5, "STOP": 0.05, "QUAL": "mom"},
                         closes, N, 0, ({}, {}), U, ctx)
    assert st[0]["a"] == pytest.approx(95.0)     # 5% under the LINE, not close


# ---- the wide search: more statistics, more stop types ----------------------

def test_atr_stop_is_wider_for_a_jumpy_name_than_a_calm_one():
    # The whole point of a volatility-scaled stop: one number cannot be right
    # for both a 1%/day stock and a 5%/day stock.
    calm = [100.0 * 1.001 ** k for k in range(N)]
    jumpy = [100.0 * 1.001 ** k * (1.04 if k % 2 else 0.97) for k in range(N)]
    closes = dict.fromkeys(U, [100.0] * N)
    closes = {x: list(v) for x, v in closes.items()}
    closes["a"], closes["b"] = calm, jumpy
    ctx = ctx_for(closes, UP, UP)
    j = N - 1
    lo = a15._stop_level(ctx, "a", j, closes["a"][j], "atr", 1.0)
    hi = a15._stop_level(ctx, "b", j, closes["b"][j], "atr", 1.0)
    assert (closes["a"][j] - lo) / closes["a"][j] < \
           (closes["b"][j] - hi) / closes["b"][j]


def test_ma_stop_sits_at_the_moving_average():
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0 * 1.002 ** k for k in range(N)]
    ctx = ctx_for(closes, UP, UP)
    j = N - 1
    lvl = a15._stop_level(ctx, "a", j, closes["a"][j], "ma", 50)
    assert lvl == pytest.approx(ctx["sma"][50]["a"][j])
    assert lvl < closes["a"][j]                  # trails below a rising tape


@pytest.mark.parametrize("smode,sp", [("pct", 0.10), ("atr", 1.0), ("ma", 50)])
def test_every_stop_mode_only_ever_ratchets_up(smode, sp):
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * 400 + [100.0 * 1.003 ** k for k in range(N - 400)]
    ctx = ctx_for(closes, UP, UP)
    p = {"K": 5, "QUAL": "mom", "SMODE": smode, "SP": sp}
    st, prev = ({}, {}), 0.0
    for i in range(402, N):
        _t, st = targets_for("glb_wide", p, closes, i, 402, st, U, ctx)
        if "a" in st[0]:
            assert st[0]["a"] >= prev             # never lowered
            prev = st[0]["a"]
    assert prev > 0


def test_the_selection_statistics_rank_by_what_they_claim():
    # Four names break out on the same day, differing one factor at a time.
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * 600 + [100.0 + 0.05 * k for k in range(N - 600)]
    closes["b"] = [100.0] * 600 + [100.0 + 0.05 * k * (1.02 if k % 2 else 0.98)
                                   for k in range(N - 600)]     # same drift, noisy
    closes["c"] = [100.0] * 600 + [100.0 + 0.60 * k for k in range(N - 600)]
    ctx = ctx_for(closes, UP, UP)
    j, spy = N - 1, ctx["idx"]["spy"]
    q = lambda x, s: a15._quality(ctx, closes, x, j, closes[x][j], s, spy)
    assert q("a", "vol") > q("b", "vol")          # calmer ranks higher
    assert q("a", "prox") > q("c", "prox")        # less extended ranks higher
    assert q("c", "mom") > q("a", "mom")          # stronger ranks higher
    assert q("c", "base") == q("a", "base")       # same base length


def test_relative_strength_is_a_REDUNDANT_axis_it_reorders_nothing():
    # Measured in the sweep, the rs and mom rows come out identical to the
    # decimal, and this is why: on a given day every name is divided by the
    # SAME index return, so the ranking cannot change. Pinned so that 'rs' is
    # never mistaken for an independent statistic.
    closes = {x: [100.0] * N for x in U}
    for k, x in enumerate(U):
        closes[x] = [100.0] * 400 + [100.0 * (1.0 + 0.0005 * (k + 1)) ** m
                                     for m in range(N - 400)]
    ctx = ctx_for(closes, UP, ramp(1.002))
    j, spy = N - 1, ctx["idx"]["spy"]
    order = lambda s: sorted(U, key=lambda x: -a15._quality(
        ctx, closes, x, j, closes[x][j], s, spy))
    assert order("mom") == order("rs")


def test_relative_strength_measures_against_SPY_not_absolute():
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * 400 + [100.0 * 1.001 ** k for k in range(N - 400)]
    ctx_weak = ctx_for(closes, UP, ramp(1.004))   # SPY ripping: a lags it
    ctx_strong = ctx_for(closes, UP, [100.0] * N)  # SPY flat: a leads it
    j = N - 1
    rs_weak = a15._quality(ctx_weak, closes, "a", j, closes["a"][j], "rs",
                           ctx_weak["idx"]["spy"])
    rs_strong = a15._quality(ctx_strong, closes, "a", j, closes["a"][j], "rs",
                             ctx_strong["idx"]["spy"])
    assert rs_weak < 0 < rs_strong                # same stock, different market


def test_glb_enters_on_the_breakout_and_shortfall_is_cash():
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * (N - 1) + [130.0]            # breaks out today
    ctx = ctx_for(closes, UP, UP)
    t, state = targets_for("glb_xs", {"K": 5, "STOP": 0.05, "ANCHOR": "green"},
                           closes, N, 0, ({}, {}), U, ctx)
    assert tuple(state[0]) == ("a",)
    assert t == {"a": 1 / 5}                             # 4 empty slots = cash
    assert sum(t.values()) <= 1.0 + 1e-9


def test_glb_trades_only_when_the_held_set_changes():
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * 400 + [130.0] * (N - 400)
    ctx = ctx_for(closes, UP, UP)
    p = {"K": 5, "STOP": 0.05, "ANCHOR": "green"}
    t1, st = targets_for("glb_xs", p, closes, 410, 0, ({}, {}), U, ctx)
    assert tuple(st[0]) == ("a",) and t1
    t2, _ = targets_for("glb_xs", p, closes, 411, 0, st, U, ctx)
    assert t2 is None                                    # no churn


def test_glb_keeps_a_held_name_after_its_green_line_catches_up():
    # GLB_HOLD days after the breakout the green line ratchets up to the new
    # level, so the name stops being ELIGIBLE — but a position already open is
    # held until the percent stop, not dropped for losing eligibility.
    closes = {x: [100.0] * N for x in U}
    closes["a"] = [100.0] * 400 + [130.0] * (N - 400)
    ctx = ctx_for(closes, UP, UP)
    j = 400 + GLB_HOLD + 5
    green, _ath, _bo, _ls = ctx["glb"]["a"]
    assert not closes["a"][j] > green[j]                 # no longer eligible
    _t, st = targets_for("glb_xs", {"K": 5, "STOP": 0.05, "ANCHOR": "green"},
                         closes, j + 1, 0, ({"a": 95.0}, {}), U, ctx)
    assert tuple(st[0]) == ("a",)                           # still held


# ---- the RWB fan and RLC ----------------------------------------------------

def test_perfect_fan_scores_above_a_tangled_one():
    strong, _rlc = rwb_arrays(ramp(1.002))
    flat, _r2 = rwb_arrays([100.0] * N)
    assert strong[N - 1] == RWB_PAIRS                    # all 66 pairs stacked
    assert flat[N - 1] < RWB_PAIRS


def test_falling_tape_inverts_the_fan_and_empties_the_rlc():
    score, rlc = rwb_arrays(ramp(0.998))
    assert score[N - 1] == 0                             # BWR: fully inverted
    assert rlc[N - 1] == 0                               # close below all six


def test_rwb_enters_only_at_rlc_six():
    closes = {x: [100.0] * N for x in U}
    closes["a"] = ramp(1.002)                            # RWB, RLC 6
    ctx = ctx_for(closes, UP, UP)
    _score, rlc = ctx["rwb"]["a"]
    assert rlc[N - 1] == 6
    t, st = targets_for("rwb_xs", {"K": 5}, closes, N, 0, (), U, ctx)
    assert st == ("a",) and t == {"a": 1 / 5}


def test_rwb_drops_a_held_name_at_rlc_zero():
    closes = {x: [100.0] * N for x in U}
    closes["a"] = ramp(0.998)                            # BWR, RLC 0
    ctx = ctx_for(closes, UP, UP)
    _s, rlc = ctx["rwb"]["a"]
    assert rlc[N - 1] == 0
    t, st = targets_for("rwb_xs", {"K": 5}, closes, N, 0, ("a",), U, ctx)
    assert "a" not in st and t == {}


# ---- rolling primitives -----------------------------------------------------

def test_rolling_new_high_needs_a_full_year_and_tracks_the_max():
    up = ramp(1.001)
    nh = rolling_new_high(up)
    assert nh[NH_LOOKBACK - 2] is False                  # not enough history
    assert nh[NH_LOOKBACK] is True
    peak = ramp(1.001, 400)[-1]
    faded = ramp(1.001, 400) + [peak * (0.999 ** k) for k in range(300)]
    assert rolling_new_high(faded)[N - 1] is False       # a year off the high


def test_rolling_helpers_reset_across_an_unlisted_gap():
    series = [None] * 500 + [100.0 * (1.001 ** k) for k in range(200)]
    nh = rolling_new_high(series)
    assert nh[699] is False                              # only 200 listed days
    above = rolling_above_sma(series, 40)
    assert above[538] is False                           # 39 listed days: unripe
    assert above[539] is True                            # the 40th: computable


# ---- invariants -------------------------------------------------------------

@pytest.mark.parametrize("family,params", [
    ("xs_topk", {"K": 5, "L": 63}),
    ("wish_gmi", GATE_DEFAULT),
    ("glb_xs", {"K": 5, "STOP": 0.05, "ANCHOR": "green"}),
    ("glb_sel", {"K": 5, "STOP": 0.05, "QUAL": "both"}),
    ("rwb_xs", {"K": 5}),
    ("ew_all", {}),
])
def test_no_family_ever_levers(family, params):
    closes = {x: ramp(1.0 + 0.0004 * k) for k, x in enumerate(U)}
    ctx = _forced_ctx(closes, [True] * N)
    state = None
    for i in range(WARM, N):
        t, state = targets_for(family, params, closes, i, WARM, state, U, ctx)
        if t is not None:
            assert sum(t.values()) <= 1.0 + 1e-9


def test_flat_tape_conserves_money_with_an_unlisted_leg():
    closes = {x: [100.0] * N for x in U}
    closes["f"] = [None] * 400 + [100.0] * (N - 400)     # lists mid-way
    ctx = _forced_ctx(closes, [True] * N)
    final = run_window("ew_all", {}, closes, prices_of(closes), WARM, N, U, ctx)
    assert final <= CAPITAL_U                            # costs only, no minting
    assert final > CAPITAL_U * 0.97


def test_grids_are_the_pre_declared_ones():
    g = grids_for(U)
    assert set(g) == {"xs_topk", "ew_all", "wish_gmi", "glb_xs",
                      "glb_sel", "glb_wide", "rwb_xs"}
    assert len(g["wish_gmi"]) == 6 and len(g["glb_xs"]) == 8 and len(g["glb_sel"]) == 12
    assert len(g["glb_wide"]) == 2 * len(a15.QUAL_WIDE) * len(a15.STOP_WIDE)
    assert all(p["GREEN"] in (4, 5) for p in g["wish_gmi"])


# ---- forward-holdout discipline --------------------------------------------

def test_holdout_refused_without_forward(capsys):
    rc = a15.main(["--holdout", "wish_gmi"])             # returns before disk
    assert rc == 2
    assert "--forward" in capsys.readouterr().err


def test_read_forward_raises_when_unarmed():
    if a15.FORWARD.exists():
        assert a15.read_forward()["family"]              # if armed, must parse
    else:
        with pytest.raises(SystemExit):
            a15.read_forward()

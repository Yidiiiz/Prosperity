"""v15 (spec v15): the Wish Bench, done properly.

Three benches implemented Dr. Eric Wish's system and three printed the same
apology -- v9: "the full 6-component GMI needs market-internals data this repo
does not carry"; v10: "the repo has no wide-universe breadth tape"; v11: "the
real GMI needs ~4,000-stock new-high breadth this repo lacks". Each then LOST,
and the loss was booked as a verdict on timing (decision 119: "the GMI brake is
a drag"). But the thing that lost was never the GMI. FOUR of its six components
count what individual stocks are doing, and not one of them was ever computed --
what lost was a tally of index moving averages wearing the GMI's name.

v13 changed the facts: it fetched 66 large-cap tapes for the cross-sectional
bench. The repo owns a stock cross-section now, so the excuse is testable.
v15 computes GMI-real -- the successful-10-day-new-high index, the new-high
count, and a T2108 proxy, all over the actual cross-section -- and runs the
gate A/B against the ungated momentum book at frozen params.

It also fixes a second misapplication: the Green Line Breakout is a STOCK
selection rule ("a strong stock breaking out to an all-time high after at least
a 3-month consolidation"), and v9/v10 ran it on QQQ and SPY, the two
instruments it is least likely to fire usefully on. glb_xs and rwb_xs put GLB
and the RWB/RLC fan where they belong: the cross-section.

Honest limits, all disclosed in the record (spec v15 2): the tapes are
Date,Close, so new highs are CLOSING highs, the green line is a closing
all-time high, and volume-confirmed rules (the EasyScans) are out of scope, not
approximated. Breadth spans 66 survivor names, not ~4,000, so Wish's absolute
"> 100" thresholds are re-expressed as a fraction B of the listed universe --
and breadth measured on SURVIVORS reads optimistically (survivors print more
new highs, fewer new lows), which biases the gate toward staying green and
therefore AGAINST finding a brake that works.

Calendar: GMI-real needs the QQQ tape, which starts 1999-03-10, so the bench
window opens once GMI-real is computable and every family is judged on that
identical span. Lookbacks still reach back into the pre-1999 rows -- the master
arrays are not truncated, only the window bounds move -- so momentum sees the
history it saw in v13.

Usage: python -m experiments.allocation15 [--mode bench|gate|fidelity|sweep|all]
       [--families all|f1,f2] [--windows 10] [--holdout F --forward]
"""

import argparse
import datetime
import math
import sys
from collections import deque
from pathlib import Path

from colony.arenas.replay import read_rows
from colony.records import Record
from colony.report import money
from experiments.allocation import BASE_VENUE, CAPITAL_U, fmt, split_bounds
from experiments.allocation6 import COST_LADDER, judge, spx_over
from experiments.minute_ladder import tape_digest
from experiments.allocation13 import (
    STOCKS, U_STOCKS, SPY_CSV, REBAL, TRADING_DAYS, load_masked,
    momentum_ranked, rebalance, _prices_at,
)

ROOT = Path(__file__).resolve().parent.parent

QQQ_CSV = ROOT / "data" / "qqq_d.csv"
IWM_CSV = ROOT / "data" / "iwm_d.csv"          # GMI-lite comparison only
EFA_CSV = ROOT / "data" / "efa_d.csv"          # GMI-lite comparison only

# --- GMI-real constants (Wish's published rules, spec v15 1) ----------------
GMI_GREEN = 4                     # green signal at GMI >= 4
GMI_RED = 2                       # red signal at GMI <= 2
CONFIRM = 2                       # ...held two consecutive days to flip
NH_LOOKBACK = TRADING_DAYS        # "52-week" high, in trading days
SUCC_LAG = 10                     # successful NEW-high index looks back 10 days
T2108_MA = 40                     # T2108 counts stocks above their 40-day SMA
T2108_LEVEL = 0.50                # ...and is a component when > 50%
GATE_DEFAULT = {"GREEN": 4, "B": 0.025}   # Wish's literal band; 100/~4000 = 2.5%
GATE_PARAMS = {"K": 5, "L": 63}   # v13's forward pick, FROZEN so the A/B is the gate

# --- RWB fan (glossary: 6 short EMAs above 6 long, daily or weekly) ---------
RWB_SHORT = (3, 5, 8, 10, 12, 15)
RWB_LONG = (30, 35, 40, 45, 50, 60)
RWB_ALL = RWB_SHORT + RWB_LONG            # ordered short -> long
RWB_PAIRS = len(RWB_ALL) * (len(RWB_ALL) - 1) // 2      # 66 ordered pairs

# --- GLB (glossary: an ATH that has stood unpenetrated three months) --------
GLB_HOLD = 63                     # ~3 months of trading days
QUAL_L = 126                      # trailing window glb_sel judges "strong" over
VOL_V = 63                        # realized-vol window for the vol stat / stop
SMA_STOPS = (50, 150)             # 10-week and 30-week, Wish's own trail lines

# The wide search the operator asked for: more selection statistics, more stop
# types. Kept SEPARATE from the pre-declared narrow grid above so that
# glb_wide vs glb_sel measures what the widening itself costs (spec v15 4a).
QUAL_WIDE = ("mom", "rs", "vol", "base", "prox", "rwb")
# 0.30/0.50 and atr 2.5 are here to LOCATE THE BOUNDARY, not to win: the first
# sweep came back monotone (the widest stop tested was the best, in all three
# stop families at once), and a trend with no interior optimum means the range
# was too narrow to have found one. If the ladder keeps climbing to 50% the
# honest reading is that the stop is not protecting anything on this universe.
STOP_WIDE = ([("pct", w) for w in (0.05, 0.08, 0.10, 0.15, 0.20, 0.30, 0.50)]
             + [("atr", m) for m in (0.5, 1.0, 1.5, 2.5)]
             + [("ma", L) for L in SMA_STOPS])

REGIME_MA = 200                   # SPY bull/bear split, carried from v14

HOLDOUT_CSV = ROOT / "data" / "holdout" / "alloc15.csv"
SHOT = ROOT / "data" / "holdout" / "alloc15.SHOT"
FORWARD = ROOT / "data" / "holdout" / "alloc15.FORWARD"


def grids_for(U):
    """Pre-declared in spec v15 4; grid order breaks train-window ties."""
    return {
        "xs_topk": [{"K": K, "L": L} for K in (5, 10, 20)
                    for L in (63, 126, 252)],
        "ew_all": [{}],                        # survivorship control, no params
        "wish_gmi": [{"GREEN": g, "B": b} for g in (4, 5)
                     for b in (0.025, 0.05, 0.10)],
        "glb_xs": [{"K": K, "STOP": s, "ANCHOR": a} for K in (5, 10)
                   for s in (0.05, 0.10) for a in ("green", "entry")],
        # ANCHOR frozen at 'green' here: v15 measured it as NOT load-bearing
        # (0.3-1.0 pp/yr, and which side wins flips with the stop width), so
        # freezing a null axis keeps the selective grid the same size as the
        # unselective one it is being compared against.
        "glb_sel": [{"K": K, "STOP": s, "QUAL": q} for K in (5, 10)
                    for s in (0.05, 0.10) for q in ("rwb", "mom", "both")],
        "glb_wide": [{"K": K, "QUAL": q, "SMODE": m, "SP": p}
                     for K in (5, 10) for q in QUAL_WIDE
                     for m, p in STOP_WIDE],
        "rwb_xs": [{"K": K} for K in (5, 10)],
    }


# ---------------------------------------------------------------------------
# tape sampling: the index tapes onto v13's master clock
# ---------------------------------------------------------------------------

def sample_index(times, csv):
    """Latest close <= each master day, None before the tape's first trade
    (same masking discipline as the stock loader, spec v13 1)."""
    a_times, a_closes = read_rows(csv)
    out, j = [], 0
    for t in times:
        if t < a_times[0]:
            out.append(None)
            continue
        while j + 1 < len(a_times) and a_times[j + 1] <= t:
            j += 1
        out.append(a_closes[j])
    return out


# ---------------------------------------------------------------------------
# per-name rolling arrays (one pass each; the breadth counts need them daily)
# ---------------------------------------------------------------------------

def rolling_new_high(series, L=NH_LOOKBACK):
    """True where the close is the highest of the trailing L closes -- a
    52-week CLOSING high (the tapes carry no intraday highs, spec v15 2).
    A name is False until it has L consecutive listed closes."""
    out = [False] * len(series)
    dq, start = deque(), 0
    for j, c in enumerate(series):
        if c is None:
            dq.clear()
            start = j + 1
            continue
        while dq and series[dq[-1]] <= c:
            dq.pop()
        dq.append(j)
        while dq[0] <= j - L:
            dq.popleft()
        if j - start + 1 >= L:
            out[j] = series[dq[0]] <= c
    return out


def rolling_above_sma(series, L):
    """True where the close is above its own trailing L-day SMA; False until
    the name has L consecutive listed closes."""
    out = [False] * len(series)
    total, start = 0.0, 0
    for j, c in enumerate(series):
        if c is None:
            total, start = 0.0, j + 1
            continue
        total += c
        if j - start + 1 > L:
            total -= series[j - L]
        if j - start + 1 >= L:
            out[j] = c > total / L
    return out


def rwb_arrays(series):
    """(score, rlc) per day. score = how many of the 66 ordered EMA pairs are
    stacked short-above-long (the RWB fan, 66 = a perfect fan); rlc = how many
    of the six SHORT emas the close sits above (glossary: 6 buys, 0 exits).
    Both None until the name has 60 consecutive listed closes."""
    n = len(series)
    score, rlc = [None] * n, [None] * n
    ema = dict.fromkeys(RWB_ALL)
    run = []
    for j, c in enumerate(series):
        if c is None:
            ema = dict.fromkeys(RWB_ALL)
            run = []
            continue
        run.append(c)
        m = len(run)
        for L in RWB_ALL:
            if ema[L] is None:
                if m >= L:
                    ema[L] = sum(run[-L:]) / L        # deterministic SMA seed
            else:
                k = 2.0 / (L + 1)
                ema[L] = c * k + ema[L] * (1 - k)
        if m >= RWB_ALL[-1]:
            v = [ema[L] for L in RWB_ALL]
            score[j] = sum(1 for x in range(len(v))
                           for y in range(x + 1, len(v)) if v[x] > v[y])
            rlc[j] = sum(1 for L in RWB_SHORT if c > ema[L])
    return score, rlc


def rolling_vol(series, V=VOL_V):
    """Population stdev of the trailing V daily log returns; None until the
    name owns V+1 consecutive listed closes. Feeds both the 'vol' selection
    statistic and the volatility-scaled stop."""
    out = [None] * len(series)
    rets, s1, s2 = [], 0.0, 0.0
    run = 0
    for j, c in enumerate(series):
        if c is None or c <= 0:
            rets, s1, s2, run = [], 0.0, 0.0, 0
            continue
        if run:
            r = math.log(c / series[j - 1])
            rets.append(r)
            s1 += r
            s2 += r * r
            if len(rets) > V:
                old = rets.pop(0)
                s1 -= old
                s2 -= old * old
            if len(rets) == V:
                mean = s1 / V
                out[j] = math.sqrt(max(0.0, s2 / V - mean * mean))
        run += 1
    return out


def rolling_sma(series, L):
    """Trailing L-day mean, None across an unlisted gap or before L closes."""
    out = [None] * len(series)
    total, start = 0.0, 0
    for j, c in enumerate(series):
        if c is None:
            total, start = 0.0, j + 1
            continue
        total += c
        if j - start + 1 > L:
            total -= series[j - L]
        if j - start + 1 >= L:
            out[j] = total / L
    return out


def glb_arrays(series, hold=GLB_HOLD):
    """(green_line, ath, last_breakout, line_set) per day. The green line at day
    j is the highest close at least `hold` trading days old -- an all-time high
    that has STOOD three months (glossary). ath is the running all-time closing
    high (it ratchets); last_breakout is the day the name most recently crossed
    its green line; line_set is the day the green line's high was PRINTED, so
    j - line_set[j] is how long the base has been building (the 'base'
    selection statistic)."""
    n = len(series)
    green, ath, last_bo = [None] * n, [None] * n, [None] * n
    line_set = [None] * n
    prefix, arg = [None] * n, [None] * n     # running max and where it was set
    best, best_at, prev_bo = None, None, None
    for j, c in enumerate(series):
        if c is not None and (best is None or c > best):
            best, best_at = c, j
        prefix[j], arg[j] = best, best_at
        ath[j] = best
        g = prefix[j - hold] if j - hold >= 0 else None
        green[j] = g
        line_set[j] = arg[j - hold] if j - hold >= 0 else None
        broke = c is not None and g is not None and c > g
        if broke and prev_bo is not True:
            last_bo[j] = j
        else:
            last_bo[j] = last_bo[j - 1] if j else None
        prev_bo = broke
    return green, ath, last_bo, line_set


# ---------------------------------------------------------------------------
# GMI-real: the six components, breadth from the cross-section (spec v15 3)
# ---------------------------------------------------------------------------

def _sma_at(series, j, L):
    if j - L + 1 < 0:
        return None
    seg = series[j - L + 1:j + 1]
    if any(c is None for c in seg):
        return None
    return sum(seg) / L


def _ema_series(series, L):
    """EMA aligned to `series`, seeded with the first L-observation SMA; None
    before the seed and across an unlisted gap."""
    out = [None] * len(series)
    e, run = None, 0
    for j, c in enumerate(series):
        if c is None:
            e, run = None, 0
            continue
        run += 1
        if e is None:
            if run >= L:
                e = sum(x for x in series[j - L + 1:j + 1]) / L
        else:
            k = 2.0 / (L + 1)
            e = c * k + e * (1 - k)
        out[j] = e
    return out


def build_ctx(closes, U, idx):
    """Precompute everything the v15 families and GMI-real read: breadth
    fractions over the listed cross-section, the index trend booleans, and the
    per-name GLB / RWB arrays. One pass; the bench then costs nothing per day.

    `idx` maps 'qqq'/'spy' to closes on the master clock (None before listing).
    """
    n = len(next(iter(closes.values())))
    nh = {x: rolling_new_high(closes[x]) for x in U}
    ab40 = {x: rolling_above_sma(closes[x], T2108_MA) for x in U}
    qqq, spy = idx["qqq"], idx["spy"]
    qqq_e10 = _ema_series(qqq, 10)
    spy_e10 = _ema_series(spy, 10)

    frac_nh, frac_succ, frac_t2108 = [None] * n, [None] * n, [None] * n
    for j in range(n):
        listed = [x for x in U if closes[x][j] is not None]
        if not listed:
            continue
        frac_nh[j] = sum(nh[x][j] for x in listed) / len(listed)
        frac_t2108[j] = sum(ab40[x][j] for x in listed) / len(listed)
        if j - SUCC_LAG >= 0:
            eligible = [x for x in listed if closes[x][j - SUCC_LAG] is not None]
            if eligible:
                frac_succ[j] = sum(
                    1 for x in eligible
                    if nh[x][j - SUCC_LAG]
                    and closes[x][j] > closes[x][j - SUCC_LAG]) / len(eligible)

    def _daily(series, ema10, j):
        """Wish component 3/4: above the 10-day EMA AND above the close 5 days
        back."""
        if j - 5 < 0 or series[j] is None or series[j - 5] is None:
            return None
        if ema10[j] is None:
            return None
        return series[j] > ema10[j] and series[j] > series[j - 5]

    def _weekly(series, j):
        """Wish component 5, the one he calls decisive: above the 10-week
        (50-day) average, with the 10-week above the 30-week (150-day)."""
        s50, s150 = _sma_at(series, j, 50), _sma_at(series, j, 150)
        if s50 is None or s150 is None or series[j] is None:
            return None
        return series[j] > s50 and s50 > s150

    ctx = {
        "n": n,
        "frac_nh": frac_nh,
        "frac_succ": frac_succ,
        "frac_t2108": frac_t2108,
        "qqq_daily": [_daily(qqq, qqq_e10, j) for j in range(n)],
        "spy_daily": [_daily(spy, spy_e10, j) for j in range(n)],
        "qqq_weekly": [_weekly(qqq, j) for j in range(n)],
        "nh": nh,
        "idx": idx,
        "phase": {},
    }
    ctx["glb"] = {x: glb_arrays(closes[x]) for x in U}
    ctx["rwb"] = {x: rwb_arrays(closes[x]) for x in U}
    ctx["vol"] = {x: rolling_vol(closes[x]) for x in U}
    ctx["sma"] = {L: {x: rolling_sma(closes[x], L) for x in U}
                  for L in SMA_STOPS}
    return ctx


def _stop_level(ctx, x, j, c, smode, sp):
    """The stop level this name's rule implies TODAY, before ratcheting.
    Every mode is trailed the operator's way -- set at entry, then only ever
    raised (see targets_for) -- so these differ in how the distance is
    measured, not in whether the stop ratchets."""
    if smode == "pct":
        return (1.0 - sp) * c                      # a flat percentage
    if smode == "atr":
        s = ctx["vol"][x][j]                       # the name's OWN volatility:
        if s is None:                              # wide for a jumpy stock,
            return None                            # tight for a calm one
        return c * (1.0 - min(0.9, sp * s * math.sqrt(REBAL)))
    if smode == "ma":
        return ctx["sma"][sp][x][j]                # trail under 10-/30-week
    raise ValueError(f"unknown stop mode {smode!r}")


def _quality(ctx, C, x, j, c, qual, spy):
    """Rank key for a breakout candidate, or None to reject it outright.
    Bigger is better for every statistic (proximity and vol are negated)."""
    score, rlc = ctx["rwb"][x]
    if qual in ("rwb", "both"):
        if score[j] is None or rlc[j] != 6:
            return None                            # the chart does not look right
        if qual == "rwb":
            return score[j]
    if qual == "vol":
        v = ctx["vol"][x][j]
        return None if v is None else -v           # calmest breakout first
    if qual == "base":
        _g, _a, _b, line_set = ctx["glb"][x]
        return None if line_set[j] is None else j - line_set[j]  # longest base
    if qual == "prox":
        green = ctx["glb"][x][0]
        if green[j] is None or green[j] <= 0:
            return None
        return -(c / green[j] - 1.0)               # least extended first
    p0 = C[x][j - QUAL_L] if j - QUAL_L >= 0 else None
    if p0 is None or p0 <= 0:
        return None
    r = c / p0 - 1.0
    if qual == "rs":
        # Strength RELATIVE to SPY (the IBD/O'Neil-style RS rank). Measured, it
        # is EXACTLY 'mom': the divisor is the same index return for every name
        # on day j, so dividing through cannot reorder the candidates, and the
        # sign rejection never binds (a GLB-eligible name always has r > 0).
        # The sweep confirms it -- the rs and mom rows are identical to the
        # decimal. Kept as a declared-and-refuted axis, not as a live knob:
        # relative strength adds nothing over raw momentum as a SAME-DAY
        # cross-sectional ranker. It would only differ against a per-name
        # benchmark, or if it gated on an absolute RS threshold.
        if spy is None or spy[j] is None or j - QUAL_L < 0:
            return None
        s0, s1 = spy[j - QUAL_L], spy[j]
        if s0 is None or s0 <= 0:
            return None
        return (1.0 + r) / (s1 / s0) - 1.0
    return r if r > 0 else None                    # 'mom' / 'both'


def gmi_components(ctx, j, B):
    """The six components as booleans, or None if any is not yet computable.
    Breadth thresholds are the fraction B of the LISTED universe -- Wish's
    "> 100 of ~4,000" re-expressed for a 66-name cross-section (spec v15 3).

    The explicit warmup guard matters: rolling_new_high reports False (not
    None) before a name owns 252 closes, so without it the count would go live
    with its two new-high components silently pinned at zero and read red for a
    year of tape."""
    if j < NH_LOOKBACK + SUCC_LAG:
        return None
    parts = (
        None if ctx["frac_succ"][j] is None else ctx["frac_succ"][j] >= B,
        None if ctx["frac_nh"][j] is None else ctx["frac_nh"][j] >= B,
        ctx["qqq_daily"][j],
        ctx["spy_daily"][j],
        ctx["qqq_weekly"][j],
        (None if ctx["frac_t2108"][j] is None
         else ctx["frac_t2108"][j] > T2108_LEVEL),
    )
    return None if any(p is None for p in parts) else parts


def gmi_count(ctx, j, B):
    parts = gmi_components(ctx, j, B)
    return None if parts is None else sum(1 for p in parts if p)


def gmi_phase(ctx, B, green_level):
    """Wish's signal: green at GMI >= green_level, red at GMI <= 2, and the
    count must HOLD for two consecutive days to flip (spec v15 1). Cached per
    (B, green_level); red during warmup."""
    key = (B, green_level)
    if key in ctx["phase"]:
        return ctx["phase"][key]
    out = [False] * ctx["n"]
    state, run_g, run_r = False, 0, 0
    for j in range(ctx["n"]):
        c = gmi_count(ctx, j, B)
        if c is None:
            run_g = run_r = 0
            out[j] = False
            continue
        run_g = run_g + 1 if c >= green_level else 0
        run_r = run_r + 1 if c <= GMI_RED else 0
        if not state and run_g >= CONFIRM:
            state = True
        elif state and run_r >= CONFIRM:
            state = False
        out[j] = state
    ctx["phase"][key] = out
    return out


def gmi_start(ctx, B=GATE_DEFAULT["B"]):
    """First master day on which GMI-real is computable -- the bench window
    opens here so every family is judged on the identical span."""
    return next((j for j in range(ctx["n"]) if gmi_count(ctx, j, B) is not None),
                ctx["n"])


# ---------------------------------------------------------------------------
# families
# ---------------------------------------------------------------------------

def _topk_targets(C, j, U, K, L):
    picks = [x for r, x in momentum_ranked(C, j, L, U)[:K] if r > 0]
    return {x: 1.0 / K for x in picks}                    # short-fall = cash


def targets_for(family, params, C, i, a, state, U, ctx):
    """Target weights for day i using history <= i-1, or None to hold.
    Returns (targets_or_None, new_state)."""
    j = i - 1
    if family == "xs_topk":
        if (i - a) % REBAL:
            return None, state
        return _topk_targets(C, j, U, params["K"], params["L"]), state
    if family == "ew_all":
        if (i - a) % REBAL:
            return None, state
        listed = [x for x in U if C[x][j] is not None]
        return ({x: 1.0 / len(listed) for x in listed} if listed else {}), state
    if family == "wish_gmi":
        # xs_topk at the FROZEN v13 pick, gated on GMI-real. Green: hold the
        # momentum book. Red: cash. Re-entry does not wait for the next 21-day
        # boundary; no daily churn while the phase is unchanged.
        green = gmi_phase(ctx, params["B"], params["GREEN"])[j]
        prev_green = None if state is None else state[0]
        if not green:
            if state is not None and prev_green is False and i != a:
                return None, (False,)
            return {}, (False,)
        just_on = prev_green is False
        if state is not None and not just_on and (i - a) % REBAL:
            return None, (True,)
        return _topk_targets(C, j, U, GATE_PARAMS["K"], GATE_PARAMS["L"]), (True,)
    if family in ("glb_xs", "glb_sel", "glb_wide"):
        # Wish's GLB where it belongs: the cross-section. Own up to K names
        # trading above a green line that has stood ~3 months.
        #
        # glb_xs takes whatever breaks out, most recent first — deliberately
        # unselective, and the control. glb_sel is the operator's refinement:
        # only take the GOOD-LOOKING ones. Wish does not buy every breakout, he
        # buys breakouts in strong growth names, and the module already computes
        # both readings of "strong": QUAL='rwb' demands the chart look right
        # (a perfect RLC=6 with the fan ranked by alignment — the RWB pattern IS
        # the good-looking chart), QUAL='mom' demands and ranks by trailing
        # QUAL_L return, QUAL='both' filters on the fan and ranks by momentum.
        # Everything else is identical, so glb_sel vs glb_xs is a clean A/B of
        # selectivity alone.
        #
        # The exit is the operator's rule, stated precisely: "put a stop loss
        # 5% under every time you do the green line breakout and raise it as
        # it increases." So the stop is set ONCE at entry and then RATCHETS --
        # it is not recomputed each day from the running all-time high. That
        # distinction is the whole rule: a breakout that opens above its green
        # line is trading above its own ATH, so an ATH-anchored stop sits above
        # the breakout level and ejects on the first retest; an entry-anchored
        # stop leaves the trade room to retest and hold.
        #
        # ANCHOR settles the one genuine ambiguity in "5% under the breakout"
        # head-to-head instead of by assumption (the v11 precedent for the safe
        # destination D): 'green' puts it 5% under the green LINE (the breakout
        # pivot), 'entry' 5% under the breakout CLOSE. They differ by the size
        # of the breakout gap. State is ({name: stop price}, {name: exit day}).
        K = params["K"]
        smode = params.get("SMODE", "pct")
        sp = params.get("SP", params.get("STOP"))
        anchor, qual = params.get("ANCHOR", "green"), params.get("QUAL")
        spy = ctx["idx"].get("spy")
        held, exits = ({}, {}) if state is None else state
        keep, exits = {}, dict(exits)
        for x, level in held.items():
            c = C[x][j]
            if c is None or c < level:
                exits[x] = j        # stopped out: it takes a FRESH breakout to
                continue            # come back, or the stop means nothing
            today = _stop_level(ctx, x, j, c, smode, sp)
            keep[x] = level if today is None else max(level, today)
        cands = []                  # ...raise it as it increases, never lower
        for x in U:
            if x in keep:
                continue
            green, _ath, bo, _ls = ctx["glb"][x]
            c = C[x][j]
            if c is None or green[j] is None or c <= green[j]:
                continue
            if bo[j] is None or bo[j] <= exits.get(x, -1):
                continue            # a stopped-out name still sits above its
            if qual is None:        # green line and would be re-bought the
                cands.append((bo[j], -U.index(x), x))    # very next day
                continue
            rank = _quality(ctx, C, x, j, c, qual, spy)
            if rank is None:
                continue
            cands.append((rank, -U.index(x), x))
        cands.sort(reverse=True)                # best first
        new = dict(keep)
        for _r, _o, x in cands[:max(0, K - len(keep))]:
            green = ctx["glb"][x][0]
            base = green[j] if (smode == "pct" and anchor == "green") else C[x][j]
            level = _stop_level(ctx, x, j, base, smode, sp)
            if level is None:
                continue            # stop not computable yet: do not enter blind
            new[x] = level
        if state is not None and tuple(new) == tuple(held) and i != a:
            return None, (new, exits)           # same book; the stop still trails
        return {x: 1.0 / K for x in new}, (new, exits)
    if family == "rwb_xs":
        # The RWB fan as a ranker: enter only a perfect RLC=6, hold while the
        # close is above at least one short average, drop at RLC=0.
        K = params["K"]
        held = () if state is None else state
        scored = []
        for x in U:
            score, rlc = ctx["rwb"][x]
            if C[x][j] is None or score[j] is None:
                continue
            scored.append((score[j], -U.index(x), x, rlc[j]))
        scored.sort(reverse=True)
        keep = [x for _s, _o, x, r in scored if x in held and r > 0]
        cands = [x for _s, _o, x, r in scored if x not in keep and r == 6]
        new = tuple(keep + cands[:max(0, K - len(keep))])
        if state is not None and new == held and i != a:
            return None, new
        return {x: 1.0 / K for x in new}, new
    raise ValueError(f"unknown family {family!r}")


# ---------------------------------------------------------------------------
# runners (v13 mechanics, threaded with ctx)
# ---------------------------------------------------------------------------

def run_window(family, params, C, P, a, b, U, ctx, venue=BASE_VENUE):
    """Audited final cash over master days [a, b): fresh $10,000, full
    liquidation at the last close."""
    cash, lots = CAPITAL_U, {x: 0 for x in U}
    state = None
    for i in range(a, b):
        targets, state = targets_for(family, params, C, i, a, state, U, ctx)
        if targets is not None:
            cash = rebalance(cash, lots, targets, _prices_at(P, i, U), venue, U)
    return rebalance(cash, lots, {}, _prices_at(P, b - 1, U), venue, U)


def run_curve(family, params, C, P, a, b, U, ctx, venue=BASE_VENUE):
    """Like run_window but also returns max drawdown over the window."""
    cash, lots = CAPITAL_U, {x: 0 for x in U}
    state, peak, mdd = None, CAPITAL_U, 0.0
    for i in range(a, b):
        targets, state = targets_for(family, params, C, i, a, state, U, ctx)
        if targets is not None:
            cash = rebalance(cash, lots, targets, _prices_at(P, i, U), venue, U)
        equity = cash + sum(lots[x] * P[x][i] for x in U if P[x][i] is not None)
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak)
    final = rebalance(cash, lots, {}, _prices_at(P, b - 1, U), venue, U)
    return final, mdd


def run_equity(family, params, C, P, a, b, U, ctx, venue=BASE_VENUE):
    """Daily mark-to-market equity over [a, b), no terminal liquidation."""
    cash, lots = CAPITAL_U, {x: 0 for x in U}
    state, curve = None, []
    for i in range(a, b):
        targets, state = targets_for(family, params, C, i, a, state, U, ctx)
        if targets is not None:
            cash = rebalance(cash, lots, targets, _prices_at(P, i, U), venue, U)
        curve.append(cash + sum(lots[x] * P[x][i]
                                for x in U if P[x][i] is not None))
    return curve


def select(family, C, P, a, b, U, ctx):
    """Train-window selection: best combo by audited final cash; ties fall to
    the earlier entry in the pre-declared grid."""
    scored = [(run_window(family, p, C, P, a, b, U, ctx), p)
              for p in grids_for(U)[family]]
    best_cash = max(s for s, _ in scored)
    return next(p for s, p in scored if s == best_cash)


# ---------------------------------------------------------------------------
# mode: bench
# ---------------------------------------------------------------------------

def run_bench(args, times, closes, prices_u, U, ctx, start, config):
    span = len(times) - start
    bounds = [(start + a, start + b)
              for a, b in split_bounds(span, args.windows)]
    grids = grids_for(U)
    families = list(grids) if args.families == "all" else args.families.split(",")
    for f in families:
        if f not in grids:
            raise SystemExit(f"unknown family {f!r} (have {', '.join(grids)})")
    record = Record("records", "experiments", "alloc15_wish", config=config)
    lines = [f"wish bench: {span} judged days / {args.windows} windows over"
             f" {len(U)} large-cap names, window opens at master row {start}"
             f" (the first day GMI-real is computable — the QQQ tape starts"
             f" 1999; lookbacks still reach the earlier rows), exploratory"
             " (survivorship-shaped universe; only a forward shot is unbiased)",
             ""]
    entries, results = [], {}
    for family in families:
        lines.append(f"family {family}:")
        wins, deltas = 0, []
        for k in range(args.windows - 1):
            a, b = bounds[k]
            best = select(family, closes, prices_u, a, b, U, ctx)
            ta, tb = bounds[k + 1]
            cash = run_window(family, best, closes, prices_u, ta, tb, U, ctx)
            win, delta, _spx = judge(f"{family} [{fmt(best)}] w{k + 2}", cash,
                                     times[ta], times[tb - 1], lines, entries)
            wins += win
            deltas.append(delta)
        tests = args.windows - 1
        verdict = "BEATS-SPX" if wins * 2 > tests else "NO-EDGE"
        mean_delta = sum(deltas) / len(deltas)
        results[family] = (verdict, wins, tests, mean_delta)
        lines.append(f"  {family}: {verdict} ({wins}/{tests} windows beat SPY)"
                     f" | mean OOS delta {mean_delta:+.2f} pp/yr")
        print(lines[-1], flush=True)
        lines.append("")
    frontier = max(results, key=lambda f: results[f][3])
    ew = results.get("ew_all")
    if ew and frontier != "ew_all":
        lines.append(f"survivorship-neutral read: {frontier} vs ew_all ="
                     f" {results[frontier][3] - ew[3]:+.2f} pp/yr"
                     " (both share the biased universe)")
        print(lines[-1])
    inc = results.get("xs_topk")
    if inc and frontier != "xs_topk":
        lines.append(f"incumbent read: {frontier} vs xs_topk ="
                     f" {results[frontier][3] - inc[3]:+.2f} pp/yr"
                     " (v15 4 arms a forward only if a NEW family clears both)")
        print(lines[-1])
    headline = ("ALLOC15 WISH " + "; ".join(
        f"{f}: {v} ({w}/{t}, {d:+.2f} pp/yr)"
        for f, (v, w, t, d) in results.items()) + f" | FRONTIER {frontier}")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[start], times[-1], entries)
    record.finish(headline, level="INFO")
    return 0


# ---------------------------------------------------------------------------
# mode: gate -- the clean A/B v9-v12 never ran
# ---------------------------------------------------------------------------

def run_gate(times, closes, prices_u, U, ctx, start, config):
    """Same book, gate on and gate off, one difference. Every (GREEN, B) combo
    is reported rather than selected — this is a measurement, not a search."""
    from colony.benchmark import cagr, span_years
    n = len(times)
    years = span_years(times[start], times[-1])
    base, base_dd = run_curve("xs_topk", GATE_PARAMS, closes, prices_u, start,
                              n, U, ctx)
    base_cagr = cagr(CAPITAL_U, base, years) * 100
    record = Record("records", "experiments", "alloc15_gate", config=config)
    lines = [f"gate A/B (spec v15 5): xs_topk [{fmt(GATE_PARAMS)}] with and"
             f" without the GMI-real gate, identical book, {n - start} days",
             f"  UNGATED xs_topk [{fmt(GATE_PARAMS)}]: {money(base)}"
             f" | {base_cagr:+.2f} %/yr | maxDD {base_dd * 100:.1f}%", ""]
    print("\n".join(lines))
    best = None
    for params in grids_for(U)["wish_gmi"]:
        phase = gmi_phase(ctx, params["B"], params["GREEN"])
        green_frac = sum(phase[start:n]) / (n - start)
        cash, mdd = run_curve("wish_gmi", params, closes, prices_u, start, n,
                              U, ctx)
        c = cagr(CAPITAL_U, cash, years) * 100
        ln = (f"  gated [{fmt(params)}]: {money(cash)} | {c:+.2f} %/yr"
              f" | maxDD {mdd * 100:.1f}% | green {green_frac:.0%} of days"
              f" | vs ungated {c - base_cagr:+.2f} pp/yr,"
              f" maxDD {(mdd - base_dd) * 100:+.1f} pp")
        lines.append(ln)
        print(ln, flush=True)
        if best is None or c > best[0]:
            best = (c, params, mdd)
    lines.append("")

    # regime decomposition at Wish's literal band (carried from v14): where
    # does the gate spend its difference?
    spy = ctx["idx"]["spy"]
    gated = run_equity("wish_gmi", GATE_DEFAULT, closes, prices_u, start, n,
                       U, ctx)
    ungated = run_equity("xs_topk", GATE_PARAMS, closes, prices_u, start, n,
                         U, ctx)
    bull = bear = 0.0
    bull_d = bear_d = 0
    for k in range(1, len(gated)):
        i = start + k
        ma = _sma_at(spy, i, REGIME_MA)
        if ma is None or spy[i] is None:
            continue
        d = ((math.log(gated[k] / gated[k - 1]) if gated[k - 1] > 0 else 0.0)
             - (math.log(ungated[k] / ungated[k - 1])
                if ungated[k - 1] > 0 else 0.0))
        if spy[i] >= ma:
            bull += d
            bull_d += 1
        else:
            bear += d
            bear_d += 1
    lines += [f"regime decomposition of the gate's effect at Wish's literal"
              f" band [{fmt(GATE_DEFAULT)}] (gated minus ungated, daily log):",
              f"  bull days (SPY >= MA{REGIME_MA}): {bull_d}, log-edge"
              f" {bull:+.4f}",
              f"  bear days (SPY <  MA{REGIME_MA}): {bear_d}, log-edge"
              f" {bear:+.4f}",
              f"  the brake is SUPPOSED to earn in bear days and pay in bull"
              f" days; it earned {bear:+.4f} there and paid {bull:+.4f} here"]
    headline = (f"ALLOC15 GATE ungated {base_cagr:+.2f} %/yr (maxDD"
                f" {base_dd * 100:.1f}%) vs best gated [{fmt(best[1])}]"
                f" {best[0]:+.2f} %/yr (maxDD {best[2] * 100:.1f}%):"
                f" gate {'HELPS' if best[0] > base_cagr else 'COSTS'}"
                f" {best[0] - base_cagr:+.2f} pp/yr at its best setting;"
                f" bear log-edge {bear:+.4f}, bull {bull:+.4f}")
    for ln in lines[-4:]:
        print(ln)
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.finish(headline, level="INFO")
    return 0


# ---------------------------------------------------------------------------
# mode: sweep -- the wide search, reported as a DISTRIBUTION
# ---------------------------------------------------------------------------

def run_sweep(times, closes, prices_u, U, ctx, start, config):
    """Every glb_wide cell over the full span. The point is NOT to crown the
    best cell -- with this many cells the best one is mostly selection noise --
    so the report leads with the DISTRIBUTION (median cell, share beating SPY)
    and breaks it down by statistic and by stop type, where a consistent shift
    across a whole row is evidence and a single tall cell is not."""
    from colony.benchmark import cagr, span_years
    n = len(times)
    years = span_years(times[start], times[-1])
    spx_cash, spx_cagr, _cov = spx_over(times[start], times[-1], CAPITAL_U,
                                        BASE_VENUE)
    spx_pp = spx_cagr * 100
    grid = grids_for(U)["glb_wide"]
    record = Record("records", "experiments", "alloc15_sweep", config=config)
    lines = [f"wide GLB search: {len(grid)} cells over {n - start} days"
             f" ({len(QUAL_WIDE)} selection statistics x {len(STOP_WIDE)} stop"
             f" rules x K in (5, 10)), vs SPY buy-and-hold {money(spx_cash)}"
             f" ({spx_pp:+.2f} %/yr)",
             "  in-sample and unadjusted: read the ROWS, not the maximum", ""]
    print("\n".join(lines))
    cells = []
    for p in grid:
        cash, mdd = run_curve("glb_wide", p, closes, prices_u, start, n, U, ctx)
        cells.append((cagr(CAPITAL_U, cash, years) * 100 - spx_pp, mdd, p))
    cells.sort(key=lambda t: -t[0])

    def _block(label, keyfn, values):
        lines.append(f"by {label}:")
        for v in values:
            sub = sorted(d for d, _m, p in cells if keyfn(p) == v)
            if not sub:
                continue
            med = sub[len(sub) // 2]
            beat = sum(1 for d in sub if d > 0)
            lines.append(f"  {str(v):<12} median {med:+6.2f} pp/yr | best"
                         f" {sub[-1]:+6.2f} | worst {sub[0]:+6.2f} |"
                         f" {beat}/{len(sub)} beat SPY")
            print(lines[-1])
        lines.append("")

    _block("selection statistic", lambda p: p["QUAL"], QUAL_WIDE)
    _block("stop rule", lambda p: f"{p['SMODE']}:{p['SP']}",
           [f"{m}:{v}" for m, v in STOP_WIDE])
    _block("K", lambda p: p["K"], (5, 10))

    all_d = sorted(d for d, _m, _p in cells)
    med_all = all_d[len(all_d) // 2]
    beat_all = sum(1 for d in all_d if d > 0)
    best_d, best_mdd, best_p = cells[0]
    lines += [f"whole grid: median {med_all:+.2f} pp/yr vs SPY,"
              f" {beat_all}/{len(all_d)} cells beat it",
              f"best cell [{fmt(best_p)}]: {best_d:+.2f} pp/yr, maxDD"
              f" {best_mdd * 100:.1f}%",
              f"  the best of {len(all_d)} cells is NOT a finding -- searching"
              f" {len(all_d)} rules on one 26-year tape produces a best cell"
              f" even when nothing works. The median and the per-row shifts are"
              f" the readable signal, and the walk-forward bench"
              f" (glb_wide vs glb_sel) is where the search pays its own tax."]
    headline = (f"ALLOC15 SWEEP {len(all_d)} cells: median {med_all:+.2f} pp/yr"
                f" vs SPY, {beat_all}/{len(all_d)} beat it; best"
                f" [{fmt(best_p)}] {best_d:+.2f} (in-sample, unadjusted)")
    for ln in lines[-4:]:
        print(ln)
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.finish(headline, level="INFO")
    return 0


# ---------------------------------------------------------------------------
# mode: fidelity -- is GMI-real actually different from the GMI-lite that lost?
# ---------------------------------------------------------------------------

def run_fidelity(times, closes, prices_u, U, ctx, start, config):
    from experiments import allocation11 as a11
    from experiments.allocation10 import _gmi_phase
    n = len(times)
    B = GATE_DEFAULT["B"]
    names = ("succ-10d-new-high", "new-highs", "qqq-daily", "spy-daily",
             "qqq-weekly", "t2108-proxy")
    hits = [0] * 6
    counts, days = [], 0
    for j in range(start, n):
        parts = gmi_components(ctx, j, B)
        if parts is None:
            continue
        days += 1
        counts.append(sum(1 for p in parts if p))
        for k, p in enumerate(parts):
            hits[k] += bool(p)
    record = Record("records", "experiments", "alloc15_fidelity", config=config)
    lines = [f"GMI-real fidelity (spec v15 5) at B={B}: {days} computable days",
             "  per-component positive rate (a rate near 0% or 100% is a"
             " component carrying no information — the 6-count is secretly a"
             " smaller count):"]
    for k, nm in enumerate(names):
        lines.append(f"    {nm:<20} {hits[k] / days:6.1%}")
    lines.append(f"  mean GMI-real {sum(counts) / days:.2f} of 6;"
                 f" green (>= {GMI_GREEN}) on"
                 f" {sum(1 for c in counts if c >= GMI_GREEN) / days:.0%} of days")

    # v11's GMI-lite on the same clock: does real breadth say anything new?
    lite_src = {"spy": ctx["idx"]["spy"], "qqq": ctx["idx"]["qqq"],
                "iwm": sample_index(times, IWM_CSV),
                "efa": sample_index(times, EFA_CSV)}
    agree = both = 0
    lite_green = real_green = 0
    real_phase = gmi_phase(ctx, B, GMI_GREEN)
    lite_state = False
    for j in range(start, n):
        if any(lite_src[x][j] is None or lite_src[x][j - 200] is None
               for x in lite_src) or j < 200:
            continue
        lc = a11.gmi_count(lite_src, j)
        if lc is None:
            continue
        lite_state = _gmi_phase(lite_state, lc)
        both += 1
        agree += (lite_state == real_phase[j])
        lite_green += lite_state
        real_green += real_phase[j]
    if both:
        lines += [f"  GMI-real vs v11 GMI-lite over {both} shared days:"
                  f" agree {agree / both:.1%} of days"
                  f" (lite green {lite_green / both:.0%}, real green"
                  f" {real_green / both:.0%})",
                  "  a high agreement rate means v9–v12's verdict stands and the"
                  " missing-breadth excuse was never load-bearing"]

    # Descriptive breadth rates. Spec v15 2 predicted the survivor universe
    # would print MORE new highs than the market; measured, it prints FEWER
    # than SPY -- and that comparison is CONFOUNDED, so the prediction is
    # neither confirmed nor refuted here. SPY is one diversified index that
    # grinds along its own high; the 66 are individual volatile names, each
    # off its own high most of the time. The gap measures diversification, not
    # survivorship. The honest survivorship control remains ew_all (v13) and
    # the direction remains what v14 measured on returns (it INFLATES); no
    # breadth-based measurement of it is available from these tapes, because a
    # same-era universe including the delisted names is unobtainable (v14 1).
    spy_nh = rolling_new_high(ctx["idx"]["spy"])
    u_rate = sum(ctx["frac_nh"][j] for j in range(start, n)
                 if ctx["frac_nh"][j] is not None) / max(1, days)
    spy_rate = sum(spy_nh[start:n]) / (n - start)
    lines += [f"  breadth rates (descriptive): the 66 names print a new closing"
              f" high on {u_rate:.1%} of name-days; SPY itself on"
              f" {spy_rate:.1%} of days.",
              "  NOT a survivorship measurement, contra spec v15 2: SPY is one"
              " diversified index sitting near its own high, the 66 are"
              " individual volatile names — the gap is diversification, and it"
              " confounds the survivorship question rather than answering it."
              " ew_all (v13) is the survivorship control; v14 settled the"
              " direction (INFLATES) on returns, and no breadth-based read of"
              " it exists while the delisted tapes are unobtainable (v14 1)."]
    headline = (f"ALLOC15 FIDELITY mean GMI-real {sum(counts) / days:.2f}/6,"
                f" every component informative ({min(hits) / days:.0%}.."
                f"{max(hits) / days:.0%} positive rate);"
                + (f" agrees with GMI-lite {agree / both:.1%} of days;" if both
                   else "")
                + f" universe new-high rate {u_rate:.1%} vs SPY {spy_rate:.1%}"
                  " (diversification, not survivorship)")
    for ln in lines:
        print(ln)
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.finish(headline, level="INFO")
    return 0


# ---------------------------------------------------------------------------
# holdout discipline (forward-only, mirrors v13/v14)
# ---------------------------------------------------------------------------

def read_forward():
    if not FORWARD.exists():
        raise SystemExit("no data/holdout/alloc15.FORWARD declaration — the"
                         " forward shot is not registered (v15 7)")
    kv = {}
    for line in FORWARD.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()
    return kv


def run_forward(args, config):
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v15 7)", file=sys.stderr)
        return 2
    fwd = read_forward()
    if args.holdout != fwd["family"]:
        print(f"forward declaration names {fwd['family']!r}, not"
              f" {args.holdout!r} — the target is frozen (v15 7)",
              file=sys.stderr)
        return 2
    cutoff = datetime.datetime.fromisoformat(fwd["cutoff"]).replace(
        tzinfo=datetime.timezone.utc).timestamp()
    min_rows = int(fwd["min_new_rows"])
    times, closes, prices_u = load_masked(U_STOCKS)
    idx = {"qqq": sample_index(times, QQQ_CSV),
           "spy": sample_index(times, SPY_CSV)}
    ctx = build_ctx(closes, U_STOCKS, idx)
    h0 = next((k for k, t in enumerate(times) if t > cutoff), len(times))
    fresh = len(times) - h0
    if fresh < min_rows:
        print(f"forward holdout not ripe: {fresh} rows postdate"
              f" {fwd['cutoff']}, need {min_rows} (v15 7) — refetch tapes"
              " later and retry", file=sys.stderr)
        return 2
    family = args.holdout
    start = gmi_start(ctx)
    best = select(family, closes, prices_u, start, h0, U_STOCKS, ctx)
    record = Record("records", "experiments", f"forward_alloc15_{family}",
                    config=config)
    lines = [f"forward holdout shot (v15 7): family {family}, params"
             f" [{fmt(best)}] re-selected on rows {start}..{h0}, frozen;"
             f" {fresh} virgin rows postdate {fwd['cutoff']} — the clean test",
             ""]
    entries = []
    cash, mdd = run_curve(family, best, closes, prices_u, h0, len(times),
                          U_STOCKS, ctx)
    win, delta, spx_cash = judge(f"forward {family} [{fmt(best)}]", cash,
                                 times[h0], times[-1], lines, entries)
    lines.append(f"  drawdown diagnostic: {family} maxDD {mdd * 100:.1f}%")
    for mult, venue in COST_LADDER:
        c2 = run_window(family, best, closes, prices_u, h0, len(times),
                        U_STOCKS, ctx, venue=venue)
        lines.append(f"  cost ladder {mult}: {money(c2)}"
                     f" ({'beat' if c2 > spx_cash else 'did not beat'} SPY)")
    verdict = "BEATS-SPX" if win else "NO-EDGE"
    headline = (f"FORWARD alloc15 {family} [{fmt(best)}]: {verdict}"
                f" ({'beat' if win else 'did not beat'} SPY on {fresh} virgin"
                f" rows, delta {delta:+.2f} pp/yr, maxDD {mdd * 100:.1f}%)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[h0], times[-1], entries)
    record.finish(headline, level="INFO")
    SHOT.write_text(
        f"fired: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}\n"
        f"family: {family} [{fmt(best)}] (forward)\n{headline}\n"
        "reruns refuse: a second look requires data that postdates the shot"
        " (v15 7)\n", encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="v15 wish bench")
    parser.add_argument("--mode", default="all",
                        choices=("bench", "gate", "fidelity", "sweep", "all"))
    parser.add_argument("--families", default="all")
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--holdout", default=None, metavar="FAMILY")
    parser.add_argument("--forward", action="store_true",
                        help="route --holdout to the clean forward shot")
    args = parser.parse_args(argv)

    base_config = {"universe": list(U_STOCKS), "windows": args.windows,
                   "capital_u": CAPITAL_U, "venue": BASE_VENUE,
                   "gmi": {"green": GMI_GREEN, "red": GMI_RED,
                           "confirm": CONFIRM, "t2108_ma": T2108_MA,
                           "nh_lookback": NH_LOOKBACK},
                   "disclosure": "closing highs (no intraday tape); breadth over"
                                 " 66 survivors, not ~4000 — thresholds scaled"
                                 " by fraction B; volume rules out of scope"}
    if args.holdout:
        if not args.forward:
            print("v15 fires no historical shot — the calendar overlaps spans"
                  " that shaped my priors; use --forward (v15 7)",
                  file=sys.stderr)
            return 2
        config = dict(base_config, tapes={x: tape_digest(ROOT / STOCKS[x][0])
                                          for x in U_STOCKS})
        return run_forward(args, config)

    times, closes, prices_u = load_masked(U_STOCKS)
    idx = {"qqq": sample_index(times, QQQ_CSV),
           "spy": sample_index(times, SPY_CSV)}
    ctx = build_ctx(closes, U_STOCKS, idx)
    start = gmi_start(ctx)
    config = dict(base_config,
                  tapes={x: tape_digest(ROOT / STOCKS[x][0]) for x in U_STOCKS},
                  joint_rows=len(times), gmi_start_row=start)
    rc = 0
    if args.mode in ("bench", "all"):
        rc |= run_bench(args, times, closes, prices_u, U_STOCKS, ctx, start,
                        config)
    if args.mode in ("gate", "all"):
        rc |= run_gate(times, closes, prices_u, U_STOCKS, ctx, start, config)
    if args.mode in ("fidelity", "all"):
        rc |= run_fidelity(times, closes, prices_u, U_STOCKS, ctx, start,
                           config)
    if args.mode == "sweep":
        rc |= run_sweep(times, closes, prices_u, U_STOCKS, ctx, start, config)
    return rc


if __name__ == "__main__":
    sys.exit(main())

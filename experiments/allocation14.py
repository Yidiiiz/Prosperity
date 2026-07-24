"""v14 (spec v14): the Survivorship Stress Bench.

v13 measured cross-sectional momentum on a fixed 66-name survivor universe and
controlled the *level* of survivorship inflation with ew_all (own every
survivor, zero skill). It left the sharper question open: would putting the
DEAD companies back into the ranking pool blow up the concentrated top-K book?
Momentum chases recent winners, and some of history's biggest winners rose for
years then went to zero (Enron, WorldCom, the dot-coms). v14 attacks that.

The literal test -- fetch the graveyard, re-rank -- is IMPOSSIBLE with honest
data: Yahoo's chart API (the only network tool this repo allows) does not serve
delisted price history. Real casualties 404 (LEH, ENE, WCOM, BSC, ...); the
post-bankruptcy "Q" tickers resolve to empty shells (LEHMQ, ENRNQ, WAMUQ, ...);
and the reusable symbols (WB, CC, SHLD, GM) point at NEW companies, not the
dead originals. Fabricating a tape and calling it history would violate the
repo's integrity, so v14 answers the question three honest ways instead:

  1. xs_skip -- the canonical 12-1 momentum signal (skip the most recent month
     to dodge short-term reversal), a real new family on real data.
  2. survivorship DIRECTION -- decompose xs_topk's edge over ew_all by SPY
     regime (>= / < 200-day MA). An edge earned in BEAR regimes comes from the
     absolute-momentum cash-exit dodging weak names -> a real graveyard would be
     dodged too, so survivorship UNDERSTATES the edge. An edge earned in BULL
     regimes is hindsight-winner chasing that survivorship INFLATES.
  3. synthetic graveyard STRESS (labeled, seeded, NOT history) -- inject phantom
     "landmine" names that rise then collapse ~-95% and delist; sweep the
     delisting intensity and report the break-even at which xs_topk's edge over
     ew_all vanishes.

Reuses v13's masked loader and money-conserving mechanics verbatim. Long-only,
exposure <= 1.0 (no-leverage red line), base-venue tolls, 1-day signal lag.

Usage: python -m experiments.allocation14 [--mode bench|regime|stress|all]
       [--families all|f1,f2] [--windows 10] [--seeds 4] [--holdout F --forward]
"""

import argparse
import datetime
import math
import random
import statistics
import sys
from pathlib import Path

from colony.arenas.replay import read_rows, to_price_u
from colony.records import Record
from colony.report import money
from experiments.allocation import BASE_VENUE, CAPITAL_U, fmt, split_bounds
from experiments.allocation6 import COST_LADDER, judge
from experiments.minute_ladder import tape_digest
from experiments.allocation13 import (
    STOCKS, U_STOCKS, SPY_CSV, REBAL, TRADING_DAYS, load_masked,
    momentum_ranked, rebalance, _prices_at,
)

ROOT = Path(__file__).resolve().parent.parent

SKIP = REBAL                               # 12-1 momentum skips the last month
REGIME_MA = 200                            # SPY bull/bear moving-average window
GRAVE_PARAMS = {"K": 5, "L": 63}           # v13 forward pick, used for regime/stress

HOLDOUT_CSV = ROOT / "data" / "holdout" / "alloc14.csv"
SHOT = ROOT / "data" / "holdout" / "alloc14.SHOT"
FORWARD = ROOT / "data" / "holdout" / "alloc14.FORWARD"


def grids_for(U):
    """Pre-declared in spec v14 2; grid order breaks train-window ties.
    xs_skip uses L > SKIP so the skip endpoint never runs off the tape front."""
    return {
        "xs_topk": [{"K": K, "L": L} for K in (5, 10, 20)
                    for L in (63, 126, 252)],
        "xs_skip": [{"K": K, "L": L} for K in (5, 10, 20)
                    for L in (126, 252)],
        "ew_all": [{}],
    }


def targets_for(family, params, C, i, a, state, U):
    """Target weights for day i using history <= i-1, or None to hold."""
    j = i - 1
    if family in ("xs_topk", "xs_skip"):
        if (i - a) % REBAL:
            return None, state
        skip = SKIP if family == "xs_skip" else 0
        ranked = momentum_ranked(C, j, params["L"], U, skip=skip)
        picks = [x for r, x in ranked[:params["K"]] if r > 0]
        return {x: 1.0 / params["K"] for x in picks}, state   # short-fall = cash
    if family == "ew_all":
        if (i - a) % REBAL:
            return None, state
        listed = [x for x in U if C[x][j] is not None]
        return ({x: 1.0 / len(listed) for x in listed} if listed else {}), state
    raise ValueError(f"unknown family {family!r}")


def run_window(family, params, C, P, a, b, U, venue=BASE_VENUE):
    """Audited final cash over master days [a, b): fresh $10,000, full
    liquidation at the last close."""
    cash, lots = CAPITAL_U, {x: 0 for x in U}
    state = None
    for i in range(a, b):
        targets, state = targets_for(family, params, C, i, a, state, U)
        if targets is not None:
            cash = rebalance(cash, lots, targets, _prices_at(P, i, U), venue, U)
    return rebalance(cash, lots, {}, _prices_at(P, b - 1, U), venue, U)


def run_equity(family, params, C, P, a, b, U, venue=BASE_VENUE):
    """Daily mark-to-market equity over [a, b) (no terminal liquidation), for
    the regime decomposition. Returns a list aligned to range(a, b)."""
    cash, lots = CAPITAL_U, {x: 0 for x in U}
    state, curve = None, []
    for i in range(a, b):
        targets, state = targets_for(family, params, C, i, a, state, U)
        if targets is not None:
            cash = rebalance(cash, lots, targets, _prices_at(P, i, U), venue, U)
        curve.append(cash + sum(lots[x] * P[x][i]
                                for x in U if P[x][i] is not None))
    return curve


def run_curve(family, params, C, P, a, b, U, venue=BASE_VENUE):
    """Like run_window but also returns max drawdown over the window."""
    cash, lots = CAPITAL_U, {x: 0 for x in U}
    state, peak, mdd = None, CAPITAL_U, 0.0
    for i in range(a, b):
        targets, state = targets_for(family, params, C, i, a, state, U)
        if targets is not None:
            cash = rebalance(cash, lots, targets, _prices_at(P, i, U), venue, U)
        equity = cash + sum(lots[x] * P[x][i] for x in U if P[x][i] is not None)
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak)
    final = rebalance(cash, lots, {}, _prices_at(P, b - 1, U), venue, U)
    return final, mdd


def select(family, C, P, a, b, U):
    """Train-window selection: best combo by audited final cash; ties fall to
    the earlier entry in the pre-declared grid."""
    scored = [(run_window(family, p, C, P, a, b, U), p)
              for p in grids_for(U)[family]]
    best_cash = max(s for s, _ in scored)
    return next(p for s, p in scored if s == best_cash)


# ---------------------------------------------------------------------------
# 1 + protocol: walk-forward bench (real data)
# ---------------------------------------------------------------------------

def run_bench(args, times, closes, prices_u, U, config):
    bounds = split_bounds(len(times), args.windows)
    grids = grids_for(U)
    families = list(grids) if args.families == "all" else args.families.split(",")
    for f in families:
        if f not in grids:
            raise SystemExit(f"unknown family {f!r} (have {', '.join(grids)})")
    record = Record("records", "experiments", "alloc14_survivorship", config=config)
    lines = [f"survivorship stress bench: {len(times)} joint days /"
             f" {args.windows} windows, {len(U)} large-cap names, exploratory"
             " (survivorship-shaped universe; only a forward shot is unbiased)",
             ""]
    entries, results = [], {}
    for family in families:
        lines.append(f"family {family}:")
        wins, deltas = 0, []
        for k in range(args.windows - 1):
            a, b = bounds[k]
            best = select(family, closes, prices_u, a, b, U)
            ta, tb = bounds[k + 1]
            cash = run_window(family, best, closes, prices_u, ta, tb, U)
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
        fd = results[frontier][3]
        lines.append(f"survivorship-neutral read: {frontier} vs ew_all ="
                     f" {fd - ew[3]:+.2f} pp/yr (both share the biased universe)")
        print(lines[-1])
    headline = ("ALLOC14 SURVIVORSHIP " + "; ".join(
        f"{f}: {v} ({w}/{t}, {d:+.2f} pp/yr)"
        for f, (v, w, t, d) in results.items())
        + f" | FRONTIER {frontier}")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[-1], entries)
    record.finish(headline, level="INFO")
    return 0


# ---------------------------------------------------------------------------
# 2: survivorship DIRECTION test (real data)
# ---------------------------------------------------------------------------

def spy_on(times):
    """SPY close sampled onto the master clock (latest close <= each day)."""
    a_times, a_closes = read_rows(SPY_CSV)
    out, j = [], 0
    for t in times:
        while j + 1 < len(a_times) and a_times[j + 1] <= t:
            j += 1
        out.append(a_closes[j])
    return out


def run_regime(times, closes, prices_u, U, config):
    """Attribute xs_topk's log-outperformance over ew_all to SPY bull vs bear
    days (close >= / < its 200-day MA). Fixed params GRAVE_PARAMS."""
    warm = max(GRAVE_PARAMS["L"], REGIME_MA) + 1
    topk = run_equity("xs_topk", GRAVE_PARAMS, closes, prices_u, warm, len(times), U)
    ew = run_equity("ew_all", {}, closes, prices_u, warm, len(times), U)
    spy = spy_on(times)
    bull_edge = bear_edge = 0.0
    bull_days = bear_days = 0
    for n in range(1, len(topk)):
        i = warm + n
        ma = sum(spy[i - REGIME_MA:i]) / REGIME_MA
        r_topk = math.log(topk[n] / topk[n - 1]) if topk[n - 1] > 0 else 0.0
        r_ew = math.log(ew[n] / ew[n - 1]) if ew[n - 1] > 0 else 0.0
        d = r_topk - r_ew
        if spy[i] >= ma:
            bull_edge += d
            bull_days += 1
        else:
            bear_edge += d
            bear_days += 1
    total = bull_edge + bear_edge
    record = Record("records", "experiments", "alloc14_regime", config=config)
    lines = [f"survivorship DIRECTION test: xs_topk [{fmt(GRAVE_PARAMS)}] minus"
             f" ew_all, daily log-edge attributed by SPY {REGIME_MA}-day regime",
             f"  bull days (SPY >= MA{REGIME_MA}): {bull_days}, cumulative"
             f" log-edge {bull_edge:+.4f}",
             f"  bear days (SPY <  MA{REGIME_MA}): {bear_days}, cumulative"
             f" log-edge {bear_edge:+.4f}",
             f"  total log-edge over ew_all: {total:+.4f}"
             f" (= {math.exp(total) - 1:+.1%} cumulative)"]
    if abs(total) > 1e-9:
        share = bear_edge / total
        verdict = ("UNDERSTATES (edge is a bear-regime cash-exit; a real"
                   " graveyard would be dodged too)" if share > 0.5 else
                   "INFLATES (edge is bull-regime winner-chasing that survivor"
                   " selection flatters)")
        lines.append(f"  -> {bear_edge / total:+.0%} of the edge is earned in"
                     f" bear regimes: survivorship {verdict}")
    headline = "ALLOC14 REGIME " + lines[-1].strip()
    for ln in lines:
        print(ln)
    record.section("results", "\n".join(lines))
    record.finish(headline, level="INFO")
    return 0


# ---------------------------------------------------------------------------
# 3: synthetic graveyard STRESS (labeled, seeded -- NOT history)
# ---------------------------------------------------------------------------

def calibrate(closes, U, times):
    """Median daily log-ret mean and vol across the real listed names -- the
    drift/vol a phantom is drawn from (so it looks like a plausible stock)."""
    mus, sigs = [], []
    for x in U:
        seg = [c for c in closes[x] if c is not None and c > 0]
        if len(seg) < TRADING_DAYS:
            continue
        rets = [math.log(seg[k + 1] / seg[k]) for k in range(len(seg) - 1)]
        mus.append(statistics.mean(rets))
        sigs.append(statistics.pstdev(rets))
    return statistics.median(mus), statistics.median(sigs)


def make_phantoms(rng, m, times, mu, sig, collapse):
    """m synthetic 'landmine' tapes on the master clock: each lists at a random
    early date, follows positive-drift GBM (earns momentum, gets chased), then
    at a random death date collapses ~-95% over `collapse` trading days and
    DELISTS (None thereafter -- a holder is stuck at the last tradeable price).
    `collapse` is the key stress lever: a slow bleed (many days) lets momentum's
    cash-exit escape, a gap (1 day) does not. All prices are synthetic and
    seeded; never written to disk as history (spec v14 3)."""
    n = len(times)
    phantoms = {}
    drift = max(mu, 0.0004)                 # ensure a rising, chase-able name
    for idx in range(m):
        name = f"_grave{idx}"
        first = rng.randint(int(0.02 * n), int(0.55 * n))
        death = rng.randint(first + TRADING_DAYS, first + 6 * TRADING_DAYS)
        price = 100.0 * math.exp(rng.uniform(-0.5, 0.5))
        series = [None] * n
        for i in range(first, n):
            if i >= death + collapse:
                break                      # delisted: None from here on
            if i < death:
                price *= math.exp(rng.gauss(drift, sig))
            else:                          # terminal collapse to ~5% of value
                price *= math.exp(math.log(0.05) / collapse)
            series[i] = price
        phantoms[name] = series
    return phantoms


def stress_once(rng, m, times, closes, prices_u, U, mu, sig, collapse):
    """Add m phantoms to the pool and return (topk_final, ew_final) over the
    full masked history at GRAVE_PARAMS."""
    phantoms = make_phantoms(rng, m, times, mu, sig, collapse)
    C = {**closes, **phantoms}
    P = {**prices_u, **{k: [to_price_u(c, 100) if c is not None else None
                            for c in v] for k, v in phantoms.items()}}
    U2 = tuple(U) + tuple(phantoms)
    warm = GRAVE_PARAMS["L"] + 1
    topk = run_window("xs_topk", GRAVE_PARAMS, C, P, warm, len(times), U2)
    ew = run_window("ew_all", {}, C, P, warm, len(times), U2)
    return topk, ew


def run_stress(args, times, closes, prices_u, U, config):
    from colony.benchmark import cagr, span_years
    mu, sig = calibrate(closes, U, times)
    warm = GRAVE_PARAMS["L"] + 1
    years = span_years(times[warm], times[-1])
    base_topk = run_window("xs_topk", GRAVE_PARAMS, closes, prices_u, warm,
                           len(times), U)
    base_ew = run_window("ew_all", {}, closes, prices_u, warm, len(times), U)
    base_edge = (cagr(CAPITAL_U, base_topk, years)
                 - cagr(CAPITAL_U, base_ew, years)) * 100
    record = Record("records", "experiments", "alloc14_stress", config=config)
    lines = [f"synthetic graveyard STRESS (labeled, seeded -- NOT history):"
             f" phantom landmines that rise then collapse -95% and delist,"
             f" injected into the ranking pool at GRAVE_PARAMS [{fmt(GRAVE_PARAMS)}]",
             f"  calibration: phantom daily mu~{mu:+.5f}, sigma~{sig:.5f}"
             f" (median of the real names), {args.seeds} seeds/intensity",
             f"  M=0 (no graveyard): xs_topk {money(base_topk)} vs ew_all"
             f" {money(base_ew)} | edge {base_edge:+.2f} pp/yr",
             f"  collapse SPEED is the lever: a slow bleed lets momentum's"
             f" cash-exit escape; a 1-day gap (bankruptcy filing / fraud reveal)"
             f" does not, and hits the concentrated top-K harder than diversified"
             f" ew_all", ""]
    print("\n".join(lines))
    intensities = [int(x) for x in args.intensities.split(",")]
    scenarios = [("slow-bleed", 40), ("1-day-gap", 1)]
    tails = []
    for label, collapse in scenarios:
        lines.append(f"scenario {label} (collapse over {collapse} trading day"
                     f"{'s' if collapse != 1 else ''}):")
        print(lines[-1])
        break_even = None
        for m in intensities:
            edges = []
            for s in range(args.seeds):
                rng = random.Random(1000 * m + s + 7 * collapse)
                topk, ew = stress_once(rng, m, times, closes, prices_u, U, mu,
                                       sig, collapse)
                edges.append((cagr(CAPITAL_U, topk, years)
                              - cagr(CAPITAL_U, ew, years)) * 100)
            mean_edge = sum(edges) / len(edges)
            ln = (f"  M={m:>3} (~{m / years:.1f}/yr): xs_topk - ew_all edge"
                  f" {mean_edge:+.2f} pp/yr"
                  f" (seeds {min(edges):+.1f}..{max(edges):+.1f})")
            lines.append(ln)
            print(ln, flush=True)
            if break_even is None and mean_edge <= 0:
                break_even = m
        tail = (f"{label}: break-even ~M={break_even}"
                f" (~{break_even / years:.1f}/yr)" if break_even else
                f"{label}: edge SURVIVED to M={intensities[-1]}"
                f" (~{intensities[-1] / years:.1f}/yr)")
        tails.append(tail)
        lines.append("  -> " + tail)
        print("  -> " + tail)
        lines.append("")
    headline = f"ALLOC14 STRESS base edge {base_edge:+.2f} pp/yr; " + "; ".join(tails)
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.finish(headline, level="INFO")
    return 0


# ---------------------------------------------------------------------------
# holdout discipline (forward-only, mirrors v13)
# ---------------------------------------------------------------------------

def read_forward():
    if not FORWARD.exists():
        raise SystemExit("no data/holdout/alloc14.FORWARD declaration — the"
                         " forward shot is not registered (v14 4)")
    kv = {}
    for line in FORWARD.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()
    return kv


def run_forward(args, config):
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v14 4)", file=sys.stderr)
        return 2
    fwd = read_forward()
    if args.holdout != fwd["family"]:
        print(f"forward declaration names {fwd['family']!r}, not"
              f" {args.holdout!r} — the target is frozen (v14 4)",
              file=sys.stderr)
        return 2
    cutoff = datetime.datetime.fromisoformat(fwd["cutoff"]).replace(
        tzinfo=datetime.timezone.utc).timestamp()
    min_rows = int(fwd["min_new_rows"])
    times, closes, prices_u = load_masked(U_STOCKS)
    h0 = next((k for k, t in enumerate(times) if t > cutoff), len(times))
    fresh = len(times) - h0
    if fresh < min_rows:
        print(f"forward holdout not ripe: {fresh} rows postdate"
              f" {fwd['cutoff']}, need {min_rows} (v14 4) — refetch tapes"
              " later and retry", file=sys.stderr)
        return 2
    family = args.holdout
    best = select(family, closes, prices_u, 0, h0, U_STOCKS)
    record = Record("records", "experiments", f"forward_alloc14_{family}",
                    config=config)
    lines = [f"forward holdout shot (v14 4): family {family}, params"
             f" [{fmt(best)}] re-selected on {h0} pre-cutoff rows, frozen;"
             f" {fresh} virgin rows postdate {fwd['cutoff']} — the clean test",
             ""]
    entries = []
    cash, mdd = run_curve(family, best, closes, prices_u, h0, len(times),
                          U_STOCKS)
    win, delta, spx_cash = judge(f"forward {family} [{fmt(best)}]", cash,
                                 times[h0], times[-1], lines, entries)
    lines.append(f"  drawdown diagnostic: {family} maxDD {mdd * 100:.1f}%")
    for mult, venue in COST_LADDER:
        c2 = run_window(family, best, closes, prices_u, h0, len(times),
                        U_STOCKS, venue=venue)
        lines.append(f"  cost ladder {mult}: {money(c2)}"
                     f" ({'beat' if c2 > spx_cash else 'did not beat'} SPY)")
    verdict = "BEATS-SPX" if win else "NO-EDGE"
    headline = (f"FORWARD alloc14 {family} [{fmt(best)}]: {verdict}"
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
        " (v14 4)\n", encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="v14 survivorship stress bench")
    parser.add_argument("--mode", default="all",
                        choices=("bench", "regime", "stress", "all"))
    parser.add_argument("--families", default="all")
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--seeds", type=int, default=4)
    parser.add_argument("--intensities", default="5,10,20,40")
    parser.add_argument("--holdout", default=None, metavar="FAMILY")
    parser.add_argument("--forward", action="store_true",
                        help="route --holdout to the clean forward shot")
    args = parser.parse_args(argv)

    if args.holdout:
        if not args.forward:
            print("v14 fires no historical shot — the calendar overlaps spans"
                  " that shaped my priors; use --forward (v14 4)",
                  file=sys.stderr)
            return 2
        config = {"tapes": {x: tape_digest(ROOT / STOCKS[x][0])
                            for x in U_STOCKS},
                  "universe": list(U_STOCKS), "windows": args.windows,
                  "capital_u": CAPITAL_U, "venue": BASE_VENUE}
        return run_forward(args, config)

    times, closes, prices_u = load_masked(U_STOCKS)
    config = {"tapes": {x: tape_digest(ROOT / STOCKS[x][0]) for x in U_STOCKS},
              "universe": list(U_STOCKS), "joint_rows": len(times),
              "windows": args.windows, "capital_u": CAPITAL_U,
              "venue": BASE_VENUE}
    rc = 0
    if args.mode in ("bench", "all"):
        rc |= run_bench(args, times, closes, prices_u, U_STOCKS, config)
    if args.mode in ("regime", "all"):
        rc |= run_regime(times, closes, prices_u, U_STOCKS, config)
    if args.mode in ("stress", "all"):
        rc |= run_stress(args, times, closes, prices_u, U_STOCKS, config)
    return rc


if __name__ == "__main__":
    sys.exit(main())

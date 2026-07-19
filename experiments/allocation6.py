"""v6 (spec v6): the Universe Bench — expand the v5 winner, try new ideas.

v5 found one survivor: monthly cross-asset momentum rotation. v6 expands it
along the two axes v5 could not test — more assets (8-asset crypto-era
universe; 6-ETF equity-era universe) and more history (GLD binds the equity
era at 2004-11-18, covering 2008/2011/2015/2018/2020) — and adds new
families from the tactical-allocation literature (spec v6 2). Same
machinery as v5: joint calendar, 1-day signal lag, base venue tolls,
integer micro-dollars, walk-forward selection, one-shot holdout.

Bench A (--bench full) is exploratory: the v5 shot spent that span's final
20%, so no holdout is carved and bench A never decides one. Bench B
(--bench etf, default) is decisive: it carves data/holdout/alloc6.csv and
the family with the highest mean OOS delta fires the one shot (v6 4),
followed by a diagnostic cost ladder at 2x and 5x friction.

Usage: python -m experiments.allocation6 [--bench etf|full]
       [--families all|f1,f2] [--windows 10] [--holdout FAMILY]
"""

import argparse
import datetime
import math
import statistics
import sys
from pathlib import Path

from colony import risk
from colony.arenas.replay import read_rows, to_price_u
from colony.benchmark import cagr, span_years
from colony.records import Record
from colony.report import money
from experiments.allocation import BASE_VENUE, CAPITAL_U, fmt, split_bounds
from experiments.minute_ladder import tape_digest
from experiments.yardstick import spx_over, spx_line

ROOT = Path(__file__).resolve().parent.parent

ASSETS = {  # name -> (tape, lot_denominator) — v6 1
    "spy": ("data/spy_d.csv", 100),
    "qqq": ("data/qqq_d.csv", 100),
    "iwm": ("data/iwm_d.csv", 100),
    "efa": ("data/efa_d.csv", 100),
    "gld": ("data/gld_d.csv", 100),
    "tlt": ("data/tlt_d.csv", 100),
    "btc": ("data/btcusdt_1d.csv", 100_000),
    "eth": ("data/ethusdt_1d.csv", 100_000),
}
U_FULL = tuple(sorted(ASSETS))
U_ETF = tuple(sorted(x for x in ASSETS if x not in ("btc", "eth")))

HOLDOUT_CSV = ROOT / "data" / "holdout" / "alloc6.csv"
SHOT = ROOT / "data" / "holdout" / "alloc6.SHOT"

COST_LADDER = (  # diagnostic only, never changes a verdict (v6 4)
    ("2x", {**BASE_VENUE, "taker_bps": 20, "spread_bps": 4}),
    ("5x", {**BASE_VENUE, "taker_bps": 50, "spread_bps": 10}),
)


def grids_for(U):
    """Pre-declared in spec v6 2; order breaks train-window ties."""
    return {
        "dm_topk": [{"K": K, "L": L} for K in (1, 2, 3) for L in (63, 126, 252)],
        "dm_1201": [{"K": K} for K in (1, 2, 3)],
        "dm_defensive": [{"L": L, "D": D} for L in (126, 252)
                         for D in ("tlt", "gld")],
        "sma_ew": [{"L": L} for L in (150, 200)],
        "inv_vol": [{"R": R} for R in (21, 63)],
        "best_bh": [{"asset": a} for a in U],
    }


def load_joint(U):
    """Master clock = SPY trading days inside the span all universe tapes
    cover; non-SPY tapes sample their latest close <= each SPY day (v5 2)."""
    raw = {name: read_rows(ROOT / ASSETS[name][0]) for name in U}
    t_lo = max(times[0] for times, _ in raw.values())
    t_hi = min(times[-1] for times, _ in raw.values())
    spy_times, spy_closes = raw["spy"]
    master = [(t, c) for t, c in zip(spy_times, spy_closes) if t_lo <= t <= t_hi]
    times = [t for t, _ in master]
    closes = {"spy": [c for _, c in master]}
    for name in U:
        if name == "spy":
            continue
        a_times, a_closes = raw[name]
        sampled, j = [], 0
        for t in times:
            while j + 1 < len(a_times) and a_times[j + 1] <= t:
                j += 1
            sampled.append(a_closes[j])
        closes[name] = sampled
    prices_u = {name: [to_price_u(c, ASSETS[name][1]) for c in closes[name]]
                for name in U}
    return times, closes, prices_u


def momentum_ranked(C, j, L, U, skip=0):
    """Trailing returns from day j-L to day j-skip, best first; ties fall to
    the earlier asset in universe order."""
    ranked = [(C[x][j - skip] / C[x][j - L] - 1.0, x) for x in U if j - L >= 0]
    return sorted(ranked, key=lambda rx: (-rx[0], U.index(rx[1])))


def targets_for(family, params, C, i, a, state, U):
    """Target weights for day i using history <= i-1, or None to hold.
    Returns (targets_or_None, new_state)."""
    j = i - 1
    if family == "dm_topk":
        if (i - a) % 21:
            return None, state
        picks = momentum_ranked(C, j, params["L"], U)[:params["K"]]
        return {x: 1.0 / params["K"] for r, x in picks if r > 0}, state
    if family == "dm_1201":
        if (i - a) % 21:
            return None, state
        if j - 252 < 0:
            return {}, state
        picks = momentum_ranked(C, j, 252, U, skip=21)[:params["K"]]
        return {x: 1.0 / params["K"] for r, x in picks if r > 0}, state
    if family == "dm_defensive":
        if (i - a) % 21:
            return None, state
        ranked = momentum_ranked(C, j, params["L"], U)
        if not ranked:
            return {}, state
        best_r, best = ranked[0]
        return ({best: 1.0} if best_r > 0 else {params["D"]: 1.0}), state
    if family == "sma_ew":
        if (i - a) % 21:
            return None, state
        L, n = params["L"], len(U)
        on = [x for x in U
              if j - L + 1 >= 0 and C[x][j] > sum(C[x][j - L + 1:j + 1]) / L]
        return {x: 1.0 / n for x in on}, state
    if family == "inv_vol":
        if (i - a) % params["R"]:
            return None, state
        if j - 63 < 0:
            return {}, state
        vols = {}
        for x in U:
            rets = [math.log(C[x][j - k] / C[x][j - k - 1]) for k in range(63)]
            vols[x] = statistics.pstdev(rets)
        if any(v <= 0 for v in vols.values()):
            return {x: 1.0 / len(U) for x in U}, state
        s = sum(1.0 / v for v in vols.values())
        return {x: 1.0 / vols[x] / s for x in U}, state
    if family == "best_bh":
        return ({params["asset"]: 1.0} if i == a else None), state
    raise ValueError(f"unknown family {family!r}")


def rebalance(cash, lots, targets, P, venue, U):
    """Trade to target weights at today's closes: sells first (freeing cash),
    then buys capped by what remains; every fill through the risk helpers."""
    equity = cash + sum(lots[x] * P[x] for x in U)
    desired = {x: int(equity * targets.get(x, 0.0)) // P[x] for x in U}
    for x in U:
        d = lots[x] - desired[x]
        if d > 0:
            proceeds = d * risk.sell_price_u(P[x], venue)
            cash += proceeds - risk.fee_u(proceeds, venue)
            lots[x] -= d
    for x in U:
        n = desired[x] - lots[x]
        if n > 0:
            fill = risk.buy_price_u(P[x], venue)
            n = min(n, cash // fill)
            while n > 0 and n * fill + risk.fee_u(n * fill, venue) > cash:
                n -= 1
            cash -= n * fill + risk.fee_u(n * fill, venue)
            lots[x] += n
    return cash


def run_window(family, params, C, P, a, b, U, venue=BASE_VENUE):
    """Audited final cash from running one family over master days [a, b):
    fresh $10,000, full liquidation at the last close."""
    cash, lots = CAPITAL_U, {x: 0 for x in U}
    state = None
    for i in range(a, b):
        targets, state = targets_for(family, params, C, i, a, state, U)
        if targets is not None:
            cash = rebalance(cash, lots, targets, {x: P[x][i] for x in U},
                             venue, U)
    return rebalance(cash, lots, {}, {x: P[x][b - 1] for x in U}, venue, U)


def select(family, C, P, a, b, U):
    """Train-window selection: best combo by audited final cash; ties fall
    to the earlier entry in the pre-declared grid (v5 3)."""
    scored = [(run_window(family, p, C, P, a, b, U), p)
              for p in grids_for(U)[family]]
    best_cash = max(s for s, _ in scored)
    return next(p for s, p in scored if s == best_cash)


def judge(label, cash, t0, t1, lines, entries):
    """Same-window SPY comparison (v4 2): (win, delta_pp_yr, spx_cash)."""
    spx_cash, spx_cagr, _cov = spx_over(t0, t1, CAPITAL_U, BASE_VENUE)
    years = span_years(t0, t1)
    delta = (cagr(CAPITAL_U, cash, years) - spx_cagr) * 100
    win = cash > spx_cash
    lines.append(f"  {label}: {money(cash)} vs SPY {money(spx_cash)}"
                 f" ({'beat' if win else 'did not beat'})")
    lines.append("  " + spx_line(label, t0, t1, CAPITAL_U, cash, BASE_VENUE))
    entries.append((label, CAPITAL_U, cash, spx_cash, t0, t1))
    return win, delta, spx_cash


def carve_holdout(times, closes, h0, U):
    HOLDOUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    if HOLDOUT_CSV.exists():
        return
    with open(HOLDOUT_CSV, "w", newline="", encoding="utf-8") as f:
        f.write("Date," + ",".join(U) + "\n")
        for i in range(h0, len(times)):
            stamp = datetime.datetime.fromtimestamp(
                times[i], datetime.timezone.utc).date().isoformat()
            f.write(stamp + "," + ",".join(str(closes[x][i]) for x in U)
                    + "\n")


def run_bench(args, times, closes, prices_u, h0, U, config):
    bounds = split_bounds(h0, args.windows)
    grids = grids_for(U)
    families = list(grids) if args.families == "all" else args.families.split(",")
    for f in families:
        if f not in grids:
            raise SystemExit(f"unknown family {f!r} (have {', '.join(grids)})")
    record = Record("records", "experiments", f"alloc6_{args.bench}",
                    config=config)
    lines = [f"universe bench ({args.bench}: {','.join(U)}):"
             f" {len(times)} joint days, grid {h0} rows /"
             f" {args.windows} windows, holdout"
             f" {len(times) - h0} rows", ""]
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
    headline = (f"ALLOC6 {args.bench.upper()} " + "; ".join(
        f"{f}: {v} ({w}/{t}, {d:+.2f} pp/yr)"
        for f, (v, w, t, d) in results.items())
        + f" | FRONTIER {frontier}"
        + (" (holdout target by v6 4)" if args.bench == "etf"
           else " (exploratory only, v6 3)"))
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[h0 - 1], entries)
    record.finish(headline, level="INFO")
    return 0


def run_holdout(args, times, closes, prices_u, h0, U, config):
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v6 4)", file=sys.stderr)
        return 2
    family = args.holdout
    if family not in grids_for(U):
        raise SystemExit(f"unknown family {family!r}")
    best = select(family, closes, prices_u, 0, h0, U)
    record = Record("records", "experiments", f"holdout_alloc6_{family}",
                    config=config)
    lines = [f"holdout shot (v6 4): family {family}, params [{fmt(best)}]"
             f" re-selected on the full grid span ({h0} rows), frozen",
             "contamination disclosure (v6 4): tail overlaps the spent v5"
             " holdout span; weaker evidence than a fully virgin window", ""]
    entries = []
    cash = run_window(family, best, closes, prices_u, h0, len(times), U)
    win, delta, spx_cash = judge(f"holdout {family} [{fmt(best)}]", cash,
                                 times[h0], times[-1], lines, entries)
    for mult, venue in COST_LADDER:
        c2 = run_window(family, best, closes, prices_u, h0, len(times), U,
                        venue=venue)
        lines.append(f"  cost ladder {mult}: {money(c2)}"
                     f" ({'beat' if c2 > spx_cash else 'did not beat'} SPY)")
    bh = {x: run_window("best_bh", {"asset": x}, closes, prices_u, h0,
                        len(times), U) for x in U}
    lines.append("  context: holdout buy-and-hold "
                 + ", ".join(f"{x} {money(bh[x])}" for x in U))
    verdict = "BEATS-SPX" if win else "NO-EDGE"
    headline = (f"HOLDOUT alloc6 {family} [{fmt(best)}]: {verdict}"
                f" ({'beat' if win else 'did not beat'} SPY on the holdout,"
                f" delta {delta:+.2f} pp/yr)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[h0], times[-1], entries)
    record.finish(headline, level="INFO")
    SHOT.write_text(
        f"fired: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}\n"
        f"family: {family} [{fmt(best)}]\n{headline}\n"
        "reruns refuse: a second look requires data that postdates the shot"
        " (v6 4)\n", encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="v6 universe bench")
    parser.add_argument("--bench", default="etf", choices=("etf", "full"))
    parser.add_argument("--families", default="all")
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--holdout", default=None, metavar="FAMILY")
    args = parser.parse_args(argv)
    if args.holdout:
        args.bench = "etf"  # the one shot lives on the equity-era calendar
    U = U_ETF if args.bench == "etf" else U_FULL
    times, closes, prices_u = load_joint(U)
    n = len(times)
    if args.bench == "etf":
        h0 = n - n // 5
        carve_holdout(times, closes, h0, U)
    else:
        h0 = n  # v5 spent this span's holdout; whole span is grid (v6 3)
    config = {"tapes": {x: tape_digest(ROOT / ASSETS[x][0]) for x in U},
              "universe": list(U), "joint_rows": n, "grid_rows": h0,
              "windows": args.windows, "capital_u": CAPITAL_U,
              "venue": BASE_VENUE}
    if args.holdout:
        return run_holdout(args, times, closes, prices_u, h0, U, config)
    return run_bench(args, times, closes, prices_u, h0, U, config)


if __name__ == "__main__":
    sys.exit(main())

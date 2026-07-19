"""v5 (spec v5): the Allocation Bench — cross-asset daily strategies vs SPY.

v4 settled the frequency axis (daily won, and still lost to SPY out of
sample); v5 tests the other axis: which assets to hold and when. Four
deterministic parameterized families plus a beta control (spec v5 1) run
walk-forward: train window selects the best combo from the pre-declared
grid, the frozen selection runs on the next window, and every test window
is judged same-window against SPY buy-and-hold at base venue costs
(v4 2). The final 20% of the joint calendar is carved to a one-shot
holdout for the pre-registered best family (v5 4). No seeds: the
families have no RNG — robustness is the test windows and the holdout.

Signals on day i use closes through day i-1 and fill at day i's close,
mirroring the colony's fill_delay_ticks = 1.

Usage: python -m experiments.allocation [--families all|f1,f2]
       [--windows 8] [--holdout FAMILY]   # the holdout fires ONCE
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
from experiments.minute_ladder import tape_digest
from experiments.yardstick import spx_over, spx_line

ROOT = Path(__file__).resolve().parent.parent
CAPITAL_U = 10_000_000_000  # $10,000, integer micro-dollars throughout

ASSETS = {  # name -> (tape, lot_denominator) — v5 2
    "spy": ("data/spy_d.csv", 100),
    "qqq": ("data/qqq_d.csv", 100),
    "btc": ("data/btcusdt_1d.csv", 100_000),
    "eth": ("data/ethusdt_1d.csv", 100_000),
}
ASSET_ORDER = tuple(sorted(ASSETS))  # deterministic trade order

BASE_VENUE = {"taker_bps": 10, "maker_bps": 0, "spread_bps": 2,
              "min_fee_u": 0, "fill_delay_ticks": 1}  # config.spy.json

GRIDS = {  # pre-declared in spec v5 1; order breaks train-window ties
    "dual_momentum": [{"L": L} for L in (63, 126, 252)],
    "trend": [{"asset": a, "L": L} for a in ASSET_ORDER for L in (100, 200)],
    "equal_weight": [{"R": R} for R in (21, 63)],
    "vol_target": [{"asset": a, "T": t} for a in ASSET_ORDER
                   for t in (0.10, 0.20)],
    "best_bh": [{"asset": a} for a in ASSET_ORDER],
}

HOLDOUT_CSV = ROOT / "data" / "holdout" / "alloc.csv"
SHOT = ROOT / "data" / "holdout" / "alloc.SHOT"


def load_joint():
    """Master clock = SPY trading days inside the span all four tapes cover;
    crypto (7-day weeks) samples its latest close <= each SPY day (v5 2)."""
    raw = {name: read_rows(ROOT / tape) for name, (tape, _) in ASSETS.items()}
    t_lo = max(times[0] for times, _ in raw.values())
    t_hi = min(times[-1] for times, _ in raw.values())
    spy_times, spy_closes = raw["spy"]
    master = [(t, c) for t, c in zip(spy_times, spy_closes) if t_lo <= t <= t_hi]
    times = [t for t, _ in master]
    closes = {"spy": [c for _, c in master]}
    for name in ASSET_ORDER:
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
                for name in ASSET_ORDER}
    return times, closes, prices_u


def targets_for(family, params, C, i, a, state):
    """Target weights for day i using history <= i-1, or None to hold.
    Returns (targets_or_None, new_state)."""
    j = i - 1
    if family == "dual_momentum":
        if (i - a) % 21:
            return None, state
        L = params["L"]
        ranked = [(C[x][j] / C[x][j - L] - 1.0, x) for x in ASSET_ORDER
                  if j - L >= 0]
        if not ranked:
            return {}, state
        best_r, best = max(ranked, key=lambda rx: (rx[0], -ASSET_ORDER.index(rx[1])))
        return ({best: 1.0} if best_r > 0 else {}), state
    if family == "trend":
        x, L = params["asset"], params["L"]
        on = j - L + 1 >= 0 and C[x][j] > sum(C[x][j - L + 1:j + 1]) / L
        if state == on:
            return None, state
        return ({x: 1.0} if on else {}), on
    if family == "equal_weight":
        if (i - a) % params["R"]:
            return None, state
        return {x: 0.25 for x in ASSET_ORDER}, state
    if family == "vol_target":
        if (i - a) % 5:
            return None, state
        x = params["asset"]
        if j - 20 < 0:
            return {}, state
        rets = [math.log(C[x][j - k] / C[x][j - k - 1]) for k in range(20)]
        vol = statistics.pstdev(rets) * math.sqrt(252)
        expo = 1.0 if vol <= 0 else min(1.0, params["T"] / vol)
        return {x: expo}, state
    if family == "best_bh":
        return ({params["asset"]: 1.0} if i == a else None), state
    raise ValueError(f"unknown family {family!r}")


def rebalance(cash, lots, targets, P, venue):
    """Trade to target weights at today's closes: sells first (freeing cash),
    then buys capped by what remains; every fill through the risk helpers."""
    equity = cash + sum(lots[x] * P[x] for x in ASSET_ORDER)
    desired = {x: int(equity * targets.get(x, 0.0)) // P[x] for x in ASSET_ORDER}
    for x in ASSET_ORDER:
        d = lots[x] - desired[x]
        if d > 0:
            proceeds = d * risk.sell_price_u(P[x], venue)
            cash += proceeds - risk.fee_u(proceeds, venue)
            lots[x] -= d
    for x in ASSET_ORDER:
        n = desired[x] - lots[x]
        if n > 0:
            fill = risk.buy_price_u(P[x], venue)
            n = min(n, cash // fill)
            while n > 0 and n * fill + risk.fee_u(n * fill, venue) > cash:
                n -= 1
            cash -= n * fill + risk.fee_u(n * fill, venue)
            lots[x] += n
    return cash


def run_window(family, params, C, P, a, b, venue=BASE_VENUE):
    """Audited final cash from running one family over master days [a, b):
    fresh $10,000, full liquidation at the last close."""
    cash, lots = CAPITAL_U, {x: 0 for x in ASSET_ORDER}
    state = None
    for i in range(a, b):
        targets, state = targets_for(family, params, C, i, a, state)
        if targets is not None:
            cash = rebalance(cash, lots, targets,
                             {x: P[x][i] for x in ASSET_ORDER}, venue)
    return rebalance(cash, lots, {}, {x: P[x][b - 1] for x in ASSET_ORDER},
                     venue)


def select(family, C, P, a, b):
    """Train-window selection: best combo by audited final cash; ties fall
    to the earlier entry in the pre-declared grid (v5 3)."""
    scored = [(run_window(family, p, C, P, a, b), p) for p in GRIDS[family]]
    best_cash = max(s for s, _ in scored)
    return next(p for s, p in scored if s == best_cash)


def fmt(params):
    return ",".join(f"{k}={v}" for k, v in params.items())


def split_bounds(n, k):
    size = n // k
    if size < 21:
        raise SystemExit(f"grid span {n} rows: too few for {k} windows")
    return [(i * size, (i + 1) * size if i < k - 1 else n) for i in range(k)]


def carve_holdout(times, closes, h0):
    HOLDOUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    if HOLDOUT_CSV.exists():
        return
    with open(HOLDOUT_CSV, "w", newline="", encoding="utf-8") as f:
        f.write("Date," + ",".join(ASSET_ORDER) + "\n")
        for i in range(h0, len(times)):
            stamp = datetime.datetime.fromtimestamp(
                times[i], datetime.timezone.utc).date().isoformat()
            f.write(stamp + "," + ",".join(str(closes[x][i])
                                           for x in ASSET_ORDER) + "\n")


def judge(label, cash, t0, t1, lines, entries):
    """Same-window SPY comparison (v4 2): returns (win, delta_pp_yr)."""
    spx_cash, spx_cagr, _cov = spx_over(t0, t1, CAPITAL_U, BASE_VENUE)
    years = span_years(t0, t1)
    delta = (cagr(CAPITAL_U, cash, years) - spx_cagr) * 100
    win = cash > spx_cash
    lines.append(f"  {label}: {money(cash)} vs SPY {money(spx_cash)}"
                 f" ({'beat' if win else 'did not beat'})")
    lines.append("  " + spx_line(label, t0, t1, CAPITAL_U, cash, BASE_VENUE))
    entries.append((label, CAPITAL_U, cash, spx_cash, t0, t1))
    return win, delta


def run_bench(args, times, closes, prices_u, h0, config):
    bounds = split_bounds(h0, args.windows)
    families = list(GRIDS) if args.families == "all" else args.families.split(",")
    for f in families:
        if f not in GRIDS:
            raise SystemExit(f"unknown family {f!r} (have {', '.join(GRIDS)})")
    record = Record("records", "experiments", "alloc_bench", config=config)
    lines = [f"allocation bench: {len(times)} joint days,"
             f" grid {h0} rows / {args.windows} windows,"
             f" holdout {len(times) - h0} rows carved", ""]
    entries, results = [], {}
    for family in families:
        lines.append(f"family {family}:")
        wins, deltas = 0, []
        for k in range(args.windows - 1):
            a, b = bounds[k]
            best = select(family, closes, prices_u, a, b)
            ta, tb = bounds[k + 1]
            cash = run_window(family, best, closes, prices_u, ta, tb)
            win, delta = judge(f"{family} [{fmt(best)}] w{k + 2}", cash,
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
    headline = ("ALLOC BENCH " + "; ".join(
        f"{f}: {v} ({w}/{t}, {d:+.2f} pp/yr)"
        for f, (v, w, t, d) in results.items())
        + f" | FRONTIER {frontier} (holdout target by v5 4)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[h0 - 1], entries)
    record.finish(headline, level="INFO")
    return 0


def run_holdout(args, times, closes, prices_u, h0, config):
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v5 4)", file=sys.stderr)
        return 2
    family = args.holdout
    if family not in GRIDS:
        raise SystemExit(f"unknown family {family!r}")
    best = select(family, closes, prices_u, 0, h0)  # one train on the grid span
    record = Record("records", "experiments", f"holdout_alloc_{family}",
                    config=config)
    lines = [f"holdout shot (v5 4): family {family}, params [{fmt(best)}]"
             f" re-selected on the full grid span ({h0} rows), frozen", ""]
    entries = []
    cash = run_window(family, best, closes, prices_u, h0, len(times))
    win, delta = judge(f"holdout {family} [{fmt(best)}]", cash,
                       times[h0], times[-1], lines, entries)
    bh = {x: run_window("best_bh", {"asset": x}, closes, prices_u, h0,
                        len(times)) for x in ASSET_ORDER}
    lines.append("  context: holdout buy-and-hold "
                 + ", ".join(f"{x} {money(bh[x])}" for x in ASSET_ORDER))
    verdict = "BEATS-SPX" if win else "NO-EDGE"
    headline = (f"HOLDOUT alloc {family} [{fmt(best)}]: {verdict}"
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
        " (v5 4)\n", encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="v5 allocation bench")
    parser.add_argument("--families", default="all")
    parser.add_argument("--windows", type=int, default=8)
    parser.add_argument("--holdout", default=None, metavar="FAMILY")
    args = parser.parse_args(argv)
    times, closes, prices_u = load_joint()
    n = len(times)
    h0 = n - n // 5
    carve_holdout(times, closes, h0)
    config = {"tapes": {x: tape_digest(ROOT / ASSETS[x][0]) for x in ASSET_ORDER},
              "joint_rows": n, "grid_rows": h0, "windows": args.windows,
              "capital_u": CAPITAL_U, "venue": BASE_VENUE}
    if args.holdout:
        return run_holdout(args, times, closes, prices_u, h0, config)
    return run_bench(args, times, closes, prices_u, h0, config)


if __name__ == "__main__":
    sys.exit(main())

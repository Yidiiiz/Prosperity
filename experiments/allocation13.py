"""v13 (spec v13): the Cross-Section Bench — momentum ACROSS many stocks.

Every prior bench rotated a handful of asset-class ETFs (+ crypto). The
operator's idea here is the classic cross-sectional momentum factor
(Jegadeesh-Titman 1993): out of a large stock universe, each month own the
handful with the strongest trailing return, long-only, and let the laggards
go. v13 tests exactly that on a fixed large-cap US universe.

Two honest departures from the ETF machinery:

  * MASKED universe. Stocks list at different times (AMZN 1997, GOOGL 2004,
    META 2012, TSLA 2010, ...). The strict joint calendar the ETF benches use
    would truncate everything to the youngest tape. Instead each tape is None
    before its first trade; a name is only ELIGIBLE to be ranked/held once it
    has real history. The universe grows over time, as it did in reality.

  * SURVIVORSHIP is disclosed, not hidden. The universe is a FIXED list of
    large caps that are (mostly) still alive today, so companies that went to
    zero (Lehman, Enron, WorldCom, the dot-coms) are absent, and even WBA —
    on the first draft of this list — 404'd because it was taken private in
    2025. That deletion of losers flatters momentum. Any edge here is an
    UPPER bound; the forward holdout (below) is the only bias-free read.

Families (long-only, exposure <= 1.0 — the no-leverage red line):
  * xs_topk  -- top-K by trailing-L return, equal weight, ONLY positive-
    momentum names; if fewer than K are positive the remainder is cash
    (absolute-momentum de-risking, same spirit as pure_mom's cash exit).
  * xs_invvol-- top-K by momentum, inverse-vol weighted, normalized to 1.0
    (risk parity across the winners; fully invested whenever >=1 is positive).
  * ew_all   -- own EVERY listed name equal-weight, rebalanced monthly. Zero
    selection skill: this is pure survivorship BETA, and it is the honest
    control. xs_topk's edge is real only insofar as it beats THIS, not SPY —
    ew_all and xs_topk share the identical biased universe, so the survivor
    inflation cancels and what remains is momentum skill.
  * best_bh  -- chase last window's single strongest name (the winner-chasing
    baseline; the passive control is SPY buy-and-hold via judge()).

Protocol is the house standard: joint calendar, 1-day signal lag, base-venue
tolls, integer micro-dollars, walk-forward (train window selects K/L by
audited final cash, frozen selection tested on the next window, win iff it
beats SPY buy-and-hold over the SAME window). This bench is EXPLORATORY — it
carves no historical one-shot (the calendar overlaps spans momentum has
already informed my priors on). If a family beats SPX out of sample, a clean
FORWARD holdout (data/holdout/alloc13.FORWARD, ripe on future data) is armed
by hand naming it — the only unbiased test of a survivorship-shaped universe.

Usage: python -m experiments.allocation13 [--families all|f1,f2]
       [--windows 10] [--holdout FAMILY --forward]
"""

import argparse
import datetime
import math
import statistics
import sys
from pathlib import Path

from colony import risk
from colony.arenas.replay import read_rows, to_price_u
from colony.records import Record
from colony.report import money
from experiments.allocation import BASE_VENUE, CAPITAL_U, fmt, split_bounds
from experiments.allocation6 import COST_LADDER, judge
from experiments.minute_ladder import tape_digest

ROOT = Path(__file__).resolve().parent.parent

# Fixed large-cap US universe (declared, not globbed — the universe is part of
# the pre-registration). Tickers -> tape path; lot denominator 100 throughout,
# matching the ETF tapes. WBA is deliberately absent: it 404'd on fetch because
# Walgreens was taken private in 2025 — a live survivorship deletion.
TICKERS = (
    "aapl msft intc csco orcl ibm txn qcom adbe amd nvda mu amat "
    "ko pep pg wmt mcd nke sbux hd low cost dis cmcsa tgt "
    "jnj pfe mrk abt unh bmy lly amgn gild mdt "
    "jpm bac wfc c gs axp usb ms "
    "ge cat ba mmm hon ups lmt de "
    "xom cvx cop slb "
    "t vz duk so "
    "amzn googl nflx crm meta tsla"
).split()
STOCKS = {t: (f"data/stocks/{t}_d.csv", 100) for t in TICKERS}
U_STOCKS = tuple(TICKERS)                 # ranking order (also the tiebreak key)
SPY_CSV = ROOT / "data" / "spy_d.csv"

REBAL = 21                                # monthly rotation cadence
TRADING_DAYS = 252

HOLDOUT_CSV = ROOT / "data" / "holdout" / "alloc13.csv"
SHOT = ROOT / "data" / "holdout" / "alloc13.SHOT"
FORWARD = ROOT / "data" / "holdout" / "alloc13.FORWARD"


def grids_for(U):
    """Pre-declared in spec v13 2; grid order breaks train-window ties."""
    return {
        "xs_topk": [{"K": K, "L": L} for K in (5, 10, 20)
                    for L in (63, 126, 252)],
        "xs_invvol": [{"K": K, "L": L} for K in (5, 10, 20)
                      for L in (63, 126, 252)],
        "ew_all": [{}],                       # survivorship control, no params
        "best_bh": [{"asset": a} for a in U],
    }


def load_masked(U):
    """Master clock = SPY trading days from SPY's start to the earliest tape
    end (never forward-filling past a tape). Each name is None before its
    first trade and samples its latest close <= each SPY day thereafter."""
    raw = {name: read_rows(ROOT / STOCKS[name][0]) for name in U}
    spy_times, _ = read_rows(SPY_CSV)
    t_lo = spy_times[0]
    t_hi = min(spy_times[-1], min(times[-1] for times, _ in raw.values()))
    times = [t for t in spy_times if t_lo <= t <= t_hi]
    closes = {}
    for name in U:
        a_times, a_closes = raw[name]
        first = a_times[0]
        sampled, j = [], 0
        for t in times:
            if t < first:
                sampled.append(None)
                continue
            while j + 1 < len(a_times) and a_times[j + 1] <= t:
                j += 1
            sampled.append(a_closes[j])
        closes[name] = sampled
    prices_u = {name: [to_price_u(c, 100) if c is not None else None
                       for c in closes[name]] for name in U}
    return times, closes, prices_u


def momentum_ranked(C, j, L, U, skip=0):
    """Trailing return j-L .. j-skip, best first, over names that are LISTED
    at both endpoints; ties fall to the earlier name in universe order."""
    out = []
    for x in U:
        if j - L < 0:
            continue
        p0, p1 = C[x][j - L], C[x][j - skip]
        if p0 is None or p1 is None or p0 <= 0:
            continue
        out.append((p1 / p0 - 1.0, x))
    out.sort(key=lambda rx: (-rx[0], U.index(rx[1])))
    return out


def realized_daily_vol(C, x, j, V):
    """Population stdev of x's trailing V daily log returns (history <= j), or
    None if the window runs off the front of the tape or hits an unlisted gap."""
    if j - V < 0:
        return None
    seg = C[x][j - V:j + 1]
    if any(c is None or c <= 0 for c in seg):
        return None
    rets = [math.log(seg[k + 1] / seg[k]) for k in range(V)]
    return statistics.pstdev(rets)


def targets_for(family, params, C, i, a, state, U):
    """Target weights for day i using history <= i-1, or None to hold.
    Returns (targets_or_None, new_state)."""
    j = i - 1
    if family == "xs_topk":
        if (i - a) % REBAL:
            return None, state
        picks = [x for r, x in momentum_ranked(C, j, params["L"], U)[:params["K"]]
                 if r > 0]
        return {x: 1.0 / params["K"] for x in picks}, state   # short-fall = cash
    if family == "xs_invvol":
        if (i - a) % REBAL:
            return None, state
        picks = [x for r, x in momentum_ranked(C, j, params["L"], U)[:params["K"]]
                 if r > 0]
        if not picks:
            return {}, state
        vols = {x: realized_daily_vol(C, x, j, 63) for x in picks}
        if any(not v for v in vols.values()):        # warmup / just-listed leg
            return {x: 1.0 / len(picks) for x in picks}, state
        inv = {x: 1.0 / vols[x] for x in picks}
        s = sum(inv.values())
        return {x: inv[x] / s for x in picks}, state
    if family == "ew_all":
        if (i - a) % REBAL:
            return None, state
        listed = [x for x in U if C[x][j] is not None]
        return ({x: 1.0 / len(listed) for x in listed} if listed else {}), state
    if family == "best_bh":
        return ({params["asset"]: 1.0} if i == a else None), state
    raise ValueError(f"unknown family {family!r}")


def rebalance(cash, lots, targets, P, venue, U):
    """Trade to target weights at today's closes over LISTED names only (P[x]
    is None before a stock lists); sells first, then buys capped by cash. Every
    fill through the risk helpers — same money-conserving path as allocation6."""
    avail = [x for x in U if P[x] is not None]
    equity = cash + sum(lots[x] * P[x] for x in avail)
    desired = {x: int(equity * targets.get(x, 0.0)) // P[x] for x in avail}
    for x in avail:
        d = lots[x] - desired[x]
        if d > 0:
            proceeds = d * risk.sell_price_u(P[x], venue)
            cash += proceeds - risk.fee_u(proceeds, venue)
            lots[x] -= d
    for x in avail:
        n = desired[x] - lots[x]
        if n > 0:
            fill = risk.buy_price_u(P[x], venue)
            n = min(n, cash // fill)
            while n > 0 and n * fill + risk.fee_u(n * fill, venue) > cash:
                n -= 1
            cash -= n * fill + risk.fee_u(n * fill, venue)
            lots[x] += n
    return cash


def _prices_at(P, i, U):
    return {x: (P[x][i] if P[x][i] is not None else None) for x in U}


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


def carve_holdout(times, closes, h0, U):
    HOLDOUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    if HOLDOUT_CSV.exists():
        return
    with open(HOLDOUT_CSV, "w", newline="", encoding="utf-8") as f:
        f.write("Date," + ",".join(U) + "\n")
        for i in range(h0, len(times)):
            stamp = datetime.datetime.fromtimestamp(
                times[i], datetime.timezone.utc).date().isoformat()
            f.write(stamp + "," + ",".join(
                "" if closes[x][i] is None else str(closes[x][i]) for x in U)
                + "\n")


def read_forward():
    if not FORWARD.exists():
        raise SystemExit("no data/holdout/alloc13.FORWARD declaration — the"
                         " forward shot is not registered (v13 4)")
    kv = {}
    for line in FORWARD.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()
    return kv


def run_bench(args, times, closes, prices_u, U, config):
    bounds = split_bounds(len(times), args.windows)
    grids = grids_for(U)
    families = list(grids) if args.families == "all" else args.families.split(",")
    for f in families:
        if f not in grids:
            raise SystemExit(f"unknown family {f!r} (have {', '.join(grids)})")
    listed = [sum(1 for x in U if closes[x][i] is not None)
              for i in (0, len(times) - 1)]
    record = Record("records", "experiments", "alloc13_xsection", config=config)
    lines = [f"cross-section bench: {len(times)} joint days / {args.windows}"
             f" windows, {len(U)} large-cap names ({listed[0]} listed at the"
             f" open, {listed[1]} at the end), exploratory — no historical carve"
             " (survivorship-shaped universe; only the forward shot is unbiased)",
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
    headline = ("ALLOC13 XSECTION " + "; ".join(
        f"{f}: {v} ({w}/{t}, {d:+.2f} pp/yr)"
        for f, (v, w, t, d) in results.items())
        + f" | FRONTIER {frontier}"
        + (" (arm a forward shot by hand if it BEATS-SPX — v13 4)"))
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[-1], entries)
    record.finish(headline, level="INFO")
    return 0


def run_forward(args, config):
    """The forward shot (v13 4): only the declared family, only on min_new_rows
    that postdate the declared cutoff, only once."""
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v13 4)", file=sys.stderr)
        return 2
    fwd = read_forward()
    if args.holdout != fwd["family"]:
        print(f"forward declaration names {fwd['family']!r}, not"
              f" {args.holdout!r} — the target is frozen (v13 4)",
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
              f" {fwd['cutoff']}, need {min_rows} (v13 4) — refetch tapes"
              " later and retry", file=sys.stderr)
        return 2
    family = args.holdout
    best = select(family, closes, prices_u, 0, h0, U_STOCKS)
    record = Record("records", "experiments", f"forward_alloc13_{family}",
                    config=config)
    lines = [f"forward holdout shot (v13 4): family {family}, params"
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
    headline = (f"FORWARD alloc13 {family} [{fmt(best)}]: {verdict}"
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
        " (v13 4)\n", encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="v13 cross-section bench")
    parser.add_argument("--families", default="all")
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--holdout", default=None, metavar="FAMILY")
    parser.add_argument("--forward", action="store_true",
                        help="route --holdout to the clean forward shot")
    args = parser.parse_args(argv)
    if args.holdout:
        if not args.forward:
            print("v13 fires no historical shot — the calendar overlaps spans"
                  " that shaped my priors; use --forward (v13 4)",
                  file=sys.stderr)
            return 2
        config = {"tapes": {x: tape_digest(ROOT / STOCKS[x][0])
                            for x in U_STOCKS},
                  "universe": list(U_STOCKS), "windows": args.windows,
                  "capital_u": CAPITAL_U, "venue": BASE_VENUE}
        return run_forward(args, config)
    times, closes, prices_u = load_masked(U_STOCKS)
    carve_holdout(times, closes, len(times) - len(times) // 5, U_STOCKS)
    config = {"tapes": {x: tape_digest(ROOT / STOCKS[x][0]) for x in U_STOCKS},
              "universe": list(U_STOCKS), "joint_rows": len(times),
              "windows": args.windows, "capital_u": CAPITAL_U,
              "venue": BASE_VENUE}
    return run_bench(args, times, closes, prices_u, U_STOCKS, config)


if __name__ == "__main__":
    sys.exit(main())

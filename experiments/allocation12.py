"""v12 (spec v12): the Risk-Budget Rotation Bench — limit risk by SIZING.

Eleven versions settled two facts. Cross-asset momentum on a high-dispersion
(crypto-era) universe is the only edge ever validated out of sample (v5/v11).
And downside *timing* is a drag: every brake that got OUT of the market —
inverse ETFs (v8), GLB stops (v9), a GMI gate (v10), that gate fused onto
momentum (v11) — either lost to buy-and-hold or clipped the dispersion edge.

So the honest answer to the operator's "risk is fine as long as you limit it"
is not another timing brake. It is the one lever every prior bench left
untouched: v4-v11 were all all-or-nothing (weight 1.0 on a single asset). v12
keeps the validated engine but makes the WEIGHT the object of study — hold the
momentum pick, but size it to a risk budget.

  * vt_mom  -- volatility-targeted top-1 momentum: w = min(1.0, target/realized
    vol of the pick). A calm asset is held full; a hot one (crypto in a vol
    spike) is sized down toward cash. The min(1.0, .) clamp is the no-leverage
    red line: v12 only ever scales a position DOWN, never up.
  * rp_topk -- risk parity across the top-K momentum names, inverse-vol
    weighted, normalized to 1.0. Diversifies pure_mom's single-asset
    concentration without a timing gate.
  * pure_mom-- the control and v11 frontier (full-size top-1 momentum).
  * best_bh -- the passive SPY-relative control.

Frontier rule is risk-adjusted (spec v12 3), pre-declared for the "limit risk"
mandate: score = mean OOS delta / max(mean OOS maxDD_pp, 5.0). Every historical
span is spent (the v5 shot consumed this calendar's tail), so v12 fires NO
historical shot — one clean forward shot only (alloc12.FORWARD, ripe ~2027),
naming whichever family wins the risk-adjusted score.

Usage: python -m experiments.allocation12 [--families all|f1,f2]
       [--windows 10] [--holdout FAMILY --forward]
"""

import argparse
import datetime
import math
import statistics
import sys
from pathlib import Path

from colony.records import Record
from colony.report import money
from experiments.allocation import BASE_VENUE, CAPITAL_U, fmt, split_bounds
from experiments.allocation6 import (ASSETS, U_FULL, COST_LADDER, judge,
                                     load_joint, momentum_ranked, rebalance)
from experiments.allocation10 import spy_maxdd
from experiments.minute_ladder import tape_digest

ROOT = Path(__file__).resolve().parent.parent

U_RISK = U_FULL                                    # 8-asset crypto-era universe
RISK = ("btc", "eth", "spy", "qqq", "iwm", "efa")  # momentum's risk sleeve
REBAL = 21                                         # monthly rotation cadence
TRADING_DAYS = 252
DD_FLOOR = 5.0                                     # frontier score dd floor (pp)

HOLDOUT_CSV = ROOT / "data" / "holdout" / "alloc12.csv"
SHOT = ROOT / "data" / "holdout" / "alloc12.SHOT"
FORWARD = ROOT / "data" / "holdout" / "alloc12.FORWARD"


def grids_for():
    """Pre-declared in spec v12 2; grid order breaks train-window ties."""
    return {
        "pure_mom": [{"L": L} for L in (63, 126, 252)],
        "vt_mom": [{"L": L, "TV": TV} for L in (63, 126, 252)
                   for TV in (0.20, 0.40, 0.80)],
        "rp_topk": [{"K": K, "L": L} for K in (2, 3) for L in (63, 126, 252)],
        "best_bh": [{"asset": a} for a in U_RISK],
    }


def realized_daily_vol(C, x, j, V):
    """Population stdev of asset x's trailing V daily log returns (history
    <= j), or None if the window runs off the front of the tape."""
    if j - V < 0:
        return None
    rets = [math.log(C[x][j - k] / C[x][j - k - 1]) for k in range(V)]
    return statistics.pstdev(rets)


def _vt_weight(C, pick, j, TV, V):
    """Vol-target weight for the pick: min(1.0, target/realized). Never > 1.0
    (no leverage). A dead-flat asset (realized 0) is held full."""
    realized = realized_daily_vol(C, pick, j, V)
    if not realized:                              # None or 0.0 -> no scaling
        return 1.0
    target_daily = TV / math.sqrt(TRADING_DAYS)
    return min(1.0, target_daily / realized)


def targets_for(family, params, C, i, a, state, U):
    """Target weights for day i using history <= i-1, or None to hold.
    Returns (targets_or_None, new_state). All families are stateless here;
    state is threaded only to keep the run_window signature uniform."""
    j = i - 1
    if family == "pure_mom":
        if (i - a) % REBAL:
            return None, state
        ranked = momentum_ranked(C, j, params["L"], RISK)
        if not ranked:
            return {}, state
        best_r, best = ranked[0]
        return ({best: 1.0} if best_r > 0 else {}), state
    if family == "vt_mom":
        if (i - a) % REBAL:
            return None, state
        ranked = momentum_ranked(C, j, params["L"], RISK)
        if not ranked:
            return {}, state
        best_r, best = ranked[0]
        if best_r <= 0:
            return {}, state                      # no positive momentum -> cash
        w = _vt_weight(C, best, j, params["TV"], 21)
        return {best: w}, state                   # remainder is cash
    if family == "rp_topk":
        if (i - a) % REBAL:
            return None, state
        ranked = momentum_ranked(C, j, params["L"], RISK)
        picks = [x for r, x in ranked[:params["K"]] if r > 0]
        if not picks:
            return {}, state
        vols = {x: realized_daily_vol(C, x, j, 63) for x in picks}
        if any(not v for v in vols.values()):     # warmup or a flat leg
            w = 1.0 / len(picks)
            return {x: w for x in picks}, state
        inv = {x: 1.0 / vols[x] for x in picks}
        s = sum(inv.values())
        return {x: inv[x] / s for x in picks}, state
    if family == "best_bh":
        return ({params["asset"]: 1.0} if i == a else None), state
    raise ValueError(f"unknown family {family!r}")


def run_window(family, params, C, P, a, b, U, venue=BASE_VENUE):
    """Audited final cash over master days [a, b): fresh $10,000, full
    liquidation at the last close."""
    cash, lots = CAPITAL_U, {x: 0 for x in U}
    state = None
    for i in range(a, b):
        targets, state = targets_for(family, params, C, i, a, state, U)
        if targets is not None:
            cash = rebalance(cash, lots, targets, {x: P[x][i] for x in U},
                             venue, U)
    return rebalance(cash, lots, {}, {x: P[x][b - 1] for x in U}, venue, U)


def run_curve(family, params, C, P, a, b, U, venue=BASE_VENUE):
    """Like run_window but also returns max drawdown over the window (spec
    v12 3: the OOS maxDD feeds the risk-adjusted frontier score)."""
    cash, lots = CAPITAL_U, {x: 0 for x in U}
    state, peak, mdd = None, CAPITAL_U, 0.0
    for i in range(a, b):
        targets, state = targets_for(family, params, C, i, a, state, U)
        if targets is not None:
            cash = rebalance(cash, lots, targets, {x: P[x][i] for x in U},
                             venue, U)
        equity = cash + sum(lots[x] * P[x][i] for x in U)
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak)
    final = rebalance(cash, lots, {}, {x: P[x][b - 1] for x in U}, venue, U)
    return final, mdd


def select(family, C, P, a, b, U):
    scored = [(run_window(family, p, C, P, a, b, U), p)
              for p in grids_for()[family]]
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
            f.write(stamp + "," + ",".join(str(closes[x][i]) for x in U)
                    + "\n")


def read_forward():
    if not FORWARD.exists():
        raise SystemExit("no data/holdout/alloc12.FORWARD declaration —"
                         " the forward shot is not registered (v12 4)")
    kv = {}
    for line in FORWARD.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()
    return kv


def run_bench(args, times, closes, prices_u, U, config):
    bounds = split_bounds(len(times), args.windows)
    grids = grids_for()
    families = list(grids) if args.families == "all" else args.families.split(",")
    for f in families:
        if f not in grids:
            raise SystemExit(f"unknown family {f!r} (have {', '.join(grids)})")
    record = Record("records", "experiments", "alloc12_risk", config=config)
    lines = [f"risk-budget rotation bench: {len(times)} joint days /"
             f" {args.windows} windows, no historical carve — every past"
             f" holdout span is spent (v12 4) (universe {','.join(U)})",
             "frontier rule (v12 3): score = mean OOS delta /"
             f" max(mean OOS maxDD_pp, {DD_FLOOR}) — risk-adjusted", ""]
    entries, results = [], {}
    for family in families:
        lines.append(f"family {family}:")
        wins, deltas, dds = 0, [], []
        for k in range(args.windows - 1):
            a, b = bounds[k]
            best = select(family, closes, prices_u, a, b, U)
            ta, tb = bounds[k + 1]
            cash, mdd = run_curve(family, best, closes, prices_u, ta, tb, U)
            win, delta, _spx = judge(f"{family} [{fmt(best)}] w{k + 2}", cash,
                                     times[ta], times[tb - 1], lines, entries)
            wins += win
            deltas.append(delta)
            dds.append(mdd * 100)
        tests = args.windows - 1
        verdict = "BEATS-SPX" if wins * 2 > tests else "NO-EDGE"
        mean_delta = sum(deltas) / len(deltas)
        mean_dd = sum(dds) / len(dds)
        score = mean_delta / max(mean_dd, DD_FLOOR)
        results[family] = (verdict, wins, tests, mean_delta, mean_dd, score)
        lines.append(f"  {family}: {verdict} ({wins}/{tests} windows beat SPY)"
                     f" | mean OOS delta {mean_delta:+.2f} pp/yr"
                     f" | mean OOS maxDD {mean_dd:.1f}%"
                     f" | risk-adj score {score:+.3f}")
        print(lines[-1], flush=True)
        lines.append("")
    frontier = max(results, key=lambda f: results[f][5])
    headline = ("ALLOC12 RISK " + "; ".join(
        f"{f}: {v} ({w}/{t}, {d:+.2f} pp/yr, DD {dd:.1f}%, score {sc:+.3f})"
        for f, (v, w, t, d, dd, sc) in results.items())
        + f" | FRONTIER {frontier} (forward-holdout target by v12 3/4)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[-1], entries)
    record.finish(headline, level="INFO")
    return 0


def run_forward(args, config):
    """The forward shot (v12 4): only the declared family, only on 126+ rows
    that postdate the declared cutoff, only once."""
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v12 4)", file=sys.stderr)
        return 2
    fwd = read_forward()
    if args.holdout != fwd["family"]:
        print(f"forward declaration names {fwd['family']!r}, not"
              f" {args.holdout!r} — the target is frozen (v12 4)",
              file=sys.stderr)
        return 2
    cutoff = datetime.datetime.fromisoformat(fwd["cutoff"]).replace(
        tzinfo=datetime.timezone.utc).timestamp()
    min_rows = int(fwd["min_new_rows"])
    times, closes, prices_u = load_joint(U_RISK)
    h0 = next((k for k, t in enumerate(times) if t > cutoff), len(times))
    fresh = len(times) - h0
    if fresh < min_rows:
        print(f"forward holdout not ripe: {fresh} rows postdate"
              f" {fwd['cutoff']}, need {min_rows} (v12 4) — refetch tapes"
              " later and retry", file=sys.stderr)
        return 2
    family = args.holdout
    best = select(family, closes, prices_u, 0, h0, U_RISK)
    record = Record("records", "experiments", f"forward_alloc12_{family}",
                    config=config)
    lines = [f"forward holdout shot (v12 4): family {family}, params"
             f" [{fmt(best)}] re-selected on {h0} pre-cutoff rows, frozen;"
             f" {fresh} virgin rows postdate {fwd['cutoff']} — the clean test",
             ""]
    entries = []
    cash, mdd = run_curve(family, best, closes, prices_u, h0, len(times), U_RISK)
    win, delta, spx_cash = judge(f"forward {family} [{fmt(best)}]", cash,
                                 times[h0], times[-1], lines, entries)
    lines.append(f"  drawdown diagnostic: {family} maxDD {mdd * 100:.1f}%"
                 f" vs SPY buy-hold maxDD {spy_maxdd(closes, h0, len(times)) * 100:.1f}%"
                 " (does not change the verdict — v12 3)")
    for mult, venue in COST_LADDER:
        c2 = run_window(family, best, closes, prices_u, h0, len(times), U_RISK,
                        venue=venue)
        lines.append(f"  cost ladder {mult}: {money(c2)}"
                     f" ({'beat' if c2 > spx_cash else 'did not beat'} SPY)")
    verdict = "BEATS-SPX" if win else "NO-EDGE"
    headline = (f"FORWARD alloc12 {family} [{fmt(best)}]: {verdict}"
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
        " (v12 4)\n", encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="v12 risk-budget rotation bench")
    parser.add_argument("--families", default="all")
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--holdout", default=None, metavar="FAMILY")
    parser.add_argument("--forward", action="store_true",
                        help="route --holdout to the clean forward shot")
    args = parser.parse_args(argv)
    U = U_RISK
    if args.holdout:
        if not args.forward:
            print("v12 fires no historical shot — every span is spent; use"
                  " --forward (v12 4)", file=sys.stderr)
            return 2
        config = {"tapes": {x: tape_digest(ROOT / ASSETS[x][0]) for x in U},
                  "universe": list(U), "windows": args.windows,
                  "capital_u": CAPITAL_U, "venue": BASE_VENUE}
        return run_forward(args, config)
    times, closes, prices_u = load_joint(U)
    carve_holdout(times, closes, len(times) - len(times) // 5, U)
    config = {"tapes": {x: tape_digest(ROOT / ASSETS[x][0]) for x in U},
              "universe": list(U), "joint_rows": len(times),
              "windows": args.windows, "capital_u": CAPITAL_U,
              "venue": BASE_VENUE}
    return run_bench(args, times, closes, prices_u, U, config)


if __name__ == "__main__":
    sys.exit(main())

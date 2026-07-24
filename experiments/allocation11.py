"""v11 (spec v11): the Regime-Gated Rotation Bench — fuse the two idea-lines.

Two lines of work each have the flaw the other fixes. Cross-asset momentum
rotation (v5/v7, the only edge ever validated out of sample) earns from
dispersion but is slow — it rides a crash for weeks before the trailing return
turns the pick defensive: upside, no brake. GMI regime timing (v10) has a
brake but, on a single index in a correlated bull, only ever sacrificed upside
(v8/v9/v10, three times): brake, no upside.

v11 gates the rotation on the regime. Green: rotate into the strongest RISK
asset (momentum's upside, crypto included). Red: step to the safe sleeve
(timing's brake). Momentum decides what to own when we're on; GMI decides
whether we're on. The bench isolates two questions the earlier ones could not:
gated_mom vs pure_mom asks whether the brake helps momentum; gated_mom vs
gmi_bh asks whether momentum's upside helps the brake.

Universe = v6's 8-asset crypto-era U_FULL (bound by the 2017 Binance tapes),
which holds real drawdowns for the brake to matter: 2018/2020/2022. No inverse
or leveraged ETFs (they decay — v8/v9/v10); the safe sleeve is cash/gld/tlt,
long only. Same machinery as v6/v10 (rebalance, judge, COST_LADDER, GMI-lite
reused). Every historical span is spent (the v5 shot consumed this calendar's
tail), so v11 fires NO historical shot — one clean forward shot only
(alloc11.FORWARD, ripe ~2027).

Usage: python -m experiments.allocation11 [--families all|f1,f2]
       [--windows 10] [--holdout FAMILY --forward]
"""

import argparse
import datetime
import sys
from pathlib import Path

from colony.records import Record
from colony.report import money
from experiments.allocation import BASE_VENUE, CAPITAL_U, fmt, split_bounds
from experiments.allocation6 import (ASSETS, U_FULL, COST_LADDER, judge,
                                     load_joint, momentum_ranked, rebalance)
from experiments.allocation10 import GMI_WARMUP, _gmi_phase, spy_maxdd
from experiments.minute_ladder import tape_digest

ROOT = Path(__file__).resolve().parent.parent

U_GATE = U_FULL                                   # 8-asset crypto-era universe
RISK = ("btc", "eth", "spy", "qqq", "iwm", "efa")  # momentum's risk sleeve
REBAL = 21                                        # monthly rotation cadence
GMI_BREADTH = ("spy", "qqq", "iwm", "efa")        # broad-market breadth proxy

HOLDOUT_CSV = ROOT / "data" / "holdout" / "alloc11.csv"
SHOT = ROOT / "data" / "holdout" / "alloc11.SHOT"
FORWARD = ROOT / "data" / "holdout" / "alloc11.FORWARD"


def grids_for():
    """Pre-declared in spec v11 2; grid order breaks train-window ties."""
    return {
        "gated_mom": [{"L": L, "D": D} for L in (63, 126, 252)
                      for D in ("cash", "gld", "tlt")],
        "pure_mom": [{"L": L} for L in (63, 126, 252)],
        "gmi_bh": [{"R": R, "D": D} for R in ("spy", "qqq")
                   for D in ("cash", "gld", "tlt")],
        "best_bh": [{"asset": a} for a in U_GATE],
    }


def _sma(C, x, j, L):
    return sum(C[x][j - L + 1:j + 1]) / L


def gmi_count(C, j):
    """GMI-lite: a 0..6 tally of trend signals (history <= j), or None during
    warmup. Breadth spans large-cap/tech/small-cap/international (spec v11 2)."""
    if j < GMI_WARMUP or j - 10 < 0:
        return None
    c = 0
    c += C["qqq"][j] > _sma(C, "qqq", j, 50)
    c += C["qqq"][j] > _sma(C, "qqq", j, 150)
    c += C["spy"][j] > _sma(C, "spy", j, 50)
    c += C["spy"][j] > _sma(C, "spy", j, 200)
    c += C["qqq"][j] > C["qqq"][j - 10]
    breadth = sum(C[x][j] > _sma(C, x, j, 50) for x in GMI_BREADTH)
    c += breadth >= 2
    return int(c)


def _safe_hold(D):
    """The safe-sleeve destination as a holding name ('cash' means flat)."""
    return "cash" if D == "cash" else D          # gld / tlt held long


def targets_for(family, params, C, i, a, state, U):
    """Target weights for day i using history <= i-1, or None to hold.
    Returns (targets_or_None, new_state)."""
    j = i - 1
    if family == "gated_mom":
        L, D = params["L"], params["D"]
        prev_green = None if state is None else state[0]
        count = gmi_count(C, j)
        if count is None:                         # warmup: sit in cash
            return ({} if i == a else None), (False, "cash")
        green = _gmi_phase(bool(prev_green), count)
        if not green:                             # brake: step to the safe sleeve
            hold = _safe_hold(D)
            new = (False, hold)
            if state is not None and state == new and i != a:
                return None, new
            return ({} if hold == "cash" else {hold: 1.0}), new
        # green: rotate monthly, and immediately on the red->green re-entry
        just_on = prev_green is not None and not prev_green
        if state is not None and not just_on and (i - a) % REBAL:
            return None, state
        ranked = momentum_ranked(C, j, L, RISK)
        if not ranked:                            # pre-warmup for L: cash
            return ({} if i == a else None), (True, "cash")
        best_r, best = ranked[0]
        hold = best if best_r > 0 else "cash"
        return ({} if hold == "cash" else {hold: 1.0}), (True, hold)
    if family == "pure_mom":
        if (i - a) % REBAL:
            return None, state
        ranked = momentum_ranked(C, j, params["L"], RISK)
        if not ranked:
            return {}, state
        best_r, best = ranked[0]
        return ({best: 1.0} if best_r > 0 else {}), state
    if family == "gmi_bh":
        R, D = params["R"], params["D"]
        prev_green = None if state is None else state[0]
        count = gmi_count(C, j)
        if count is None:
            return ({} if i == a else None), (False, "cash")
        green = _gmi_phase(bool(prev_green), count)
        hold = R if green else _safe_hold(D)
        new = (green, hold)
        if state is not None and state == new and i != a:
            return None, new
        return ({} if hold == "cash" else {hold: 1.0}), new
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
    """Like run_window but also returns max drawdown over the window (a
    diagnostic; never changes a verdict — spec v11 3)."""
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
        raise SystemExit("no data/holdout/alloc11.FORWARD declaration —"
                         " the forward shot is not registered (v11 4)")
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
    record = Record("records", "experiments", "alloc11_gate", config=config)
    lines = [f"regime-gated rotation bench: {len(times)} joint days /"
             f" {args.windows} windows, no historical carve — every past"
             f" holdout span is spent (v11 4) (universe {','.join(U)})", ""]
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
    headline = ("ALLOC11 GATE " + "; ".join(
        f"{f}: {v} ({w}/{t}, {d:+.2f} pp/yr)"
        for f, (v, w, t, d) in results.items())
        + f" | FRONTIER {frontier} (forward-holdout target by v11 4)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[-1], entries)
    record.finish(headline, level="INFO")
    return 0


def run_forward(args, config):
    """The forward shot (v11 4): only the declared family, only on 126+ rows
    that postdate the declared cutoff, only once."""
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v11 4)", file=sys.stderr)
        return 2
    fwd = read_forward()
    if args.holdout != fwd["family"]:
        print(f"forward declaration names {fwd['family']!r}, not"
              f" {args.holdout!r} — the target is frozen (v11 4)",
              file=sys.stderr)
        return 2
    cutoff = datetime.datetime.fromisoformat(fwd["cutoff"]).replace(
        tzinfo=datetime.timezone.utc).timestamp()
    min_rows = int(fwd["min_new_rows"])
    times, closes, prices_u = load_joint(U_GATE)
    h0 = next((k for k, t in enumerate(times) if t > cutoff), len(times))
    fresh = len(times) - h0
    if fresh < min_rows:
        print(f"forward holdout not ripe: {fresh} rows postdate"
              f" {fwd['cutoff']}, need {min_rows} (v11 4) — refetch tapes"
              " later and retry", file=sys.stderr)
        return 2
    family = args.holdout
    best = select(family, closes, prices_u, 0, h0, U_GATE)
    record = Record("records", "experiments", f"forward_alloc11_{family}",
                    config=config)
    lines = [f"forward holdout shot (v11 4): family {family}, params"
             f" [{fmt(best)}] re-selected on {h0} pre-cutoff rows, frozen;"
             f" {fresh} virgin rows postdate {fwd['cutoff']} — the clean test",
             ""]
    entries = []
    cash, mdd = run_curve(family, best, closes, prices_u, h0, len(times), U_GATE)
    win, delta, spx_cash = judge(f"forward {family} [{fmt(best)}]", cash,
                                 times[h0], times[-1], lines, entries)
    lines.append(f"  drawdown diagnostic: {family} maxDD {mdd * 100:.1f}%"
                 f" vs SPY buy-hold maxDD {spy_maxdd(closes, h0, len(times)) * 100:.1f}%"
                 " (does not change the verdict — v11 3)")
    for mult, venue in COST_LADDER:
        c2 = run_window(family, best, closes, prices_u, h0, len(times), U_GATE,
                        venue=venue)
        lines.append(f"  cost ladder {mult}: {money(c2)}"
                     f" ({'beat' if c2 > spx_cash else 'did not beat'} SPY)")
    verdict = "BEATS-SPX" if win else "NO-EDGE"
    headline = (f"FORWARD alloc11 {family} [{fmt(best)}]: {verdict}"
                f" ({'beat' if win else 'did not beat'} SPY on {fresh} virgin"
                f" rows, delta {delta:+.2f} pp/yr)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[h0], times[-1], entries)
    record.finish(headline, level="INFO")
    SHOT.write_text(
        f"fired: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}\n"
        f"family: {family} [{fmt(best)}] (forward)\n{headline}\n"
        "reruns refuse: a second look requires data that postdates the shot"
        " (v11 4)\n", encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="v11 regime-gated rotation bench")
    parser.add_argument("--families", default="all")
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--holdout", default=None, metavar="FAMILY")
    parser.add_argument("--forward", action="store_true",
                        help="route --holdout to the clean forward shot")
    args = parser.parse_args(argv)
    U = U_GATE
    if args.holdout:
        if not args.forward:
            print("v11 fires no historical shot — every span is spent; use"
                  " --forward (v11 4)", file=sys.stderr)
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

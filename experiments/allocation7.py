"""v7 (spec v7): the Dispersion Gate — turn the v6 diagnosis into strategy.

v6 measured that momentum rotation wins only where cross-asset dispersion
is real (crypto era) and that riding decade-scale regimes wins where it
is not (equity era). v7 builds the strategies that diagnosis implies:
dm_gated switches between the two modes on measured dispersion, slow_bh
makes the v6 control's implicit regime-riding explicit, and dm_cadence is
the weekly-vs-monthly-vs-quarterly arm for the incumbent. dm_topk and
best_bh ride along unchanged as reference and control (spec v7 2).

Every historical holdout span is spent, so v7's one shot is FORWARD
(spec v7 4): declared in data/holdout/alloc7.FORWARD, it refuses to run
until 126+ joint rows postdate 2026-07-19 — the first fully
uncontaminated test available since v5, because its data hasn't happened.

Usage: python -m experiments.allocation7 [--bench full|etf]
       [--families all|f1,f2] [--windows N] [--holdout FAMILY]
"""

import argparse
import datetime
import sys

from colony.report import money
from experiments import allocation6
from experiments.allocation import BASE_VENUE, CAPITAL_U, fmt, split_bounds
from experiments.allocation6 import (ASSETS, ROOT, U_ETF, U_FULL, judge,
                                     load_joint, momentum_ranked, rebalance)
from experiments.minute_ladder import tape_digest
from colony.records import Record

FORWARD = ROOT / "data" / "holdout" / "alloc7.FORWARD"
SHOT = ROOT / "data" / "holdout" / "alloc7.SHOT"
SLOW_L = 378  # the regime clock for dm_gated's calm mode (spec v7 2)


def grids_for(U):
    """Pre-declared in spec v7 2; order breaks train-window ties."""
    return {
        "dm_gated": [{"Lf": Lf, "G": G} for Lf in (63, 126)
                     for G in (0.15, 0.30)],
        "slow_bh": [{"L": L, "R": R} for L in (252, SLOW_L)
                    for R in (63, 126)],
        "dm_cadence": [{"L": L, "R": R} for L in (126, 252)
                       for R in (5, 21, 63)],
        "dm_topk": allocation6.grids_for(U)["dm_topk"],
        "best_bh": [{"asset": a} for a in U],
    }


def targets_for(family, params, C, i, a, state, U):
    """Target weights for day i using history <= i-1, or None to hold.
    Returns (targets_or_None, new_state)."""
    j = i - 1
    if family == "dm_gated":
        if (i - a) % 21:
            return None, state
        fast = momentum_ranked(C, j, params["Lf"], U)
        if not fast:
            return {}, state
        if fast[0][0] - fast[-1][0] >= params["G"]:
            best_r, best = fast[0]
            return ({best: 1.0} if best_r > 0 else {}), state
        slow = momentum_ranked(C, j, SLOW_L, U)
        return ({slow[0][1]: 1.0} if slow else {}), state
    if family == "slow_bh":
        if (i - a) % params["R"]:
            return None, state
        ranked = momentum_ranked(C, j, params["L"], U)
        return ({ranked[0][1]: 1.0} if ranked else {}), state
    if family == "dm_cadence":
        if (i - a) % params["R"]:
            return None, state
        picks = momentum_ranked(C, j, params["L"], U)[:1]
        return {x: 1.0 for r, x in picks if r > 0}, state
    return allocation6.targets_for(family, params, C, i, a, state, U)


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


def select(family, C, P, a, b, U):
    scored = [(run_window(family, p, C, P, a, b, U), p)
              for p in grids_for(U)[family]]
    best_cash = max(s for s, _ in scored)
    return next(p for s, p in scored if s == best_cash)


def read_forward():
    if not FORWARD.exists():
        raise SystemExit("no data/holdout/alloc7.FORWARD declaration —"
                         " the forward shot is not registered (v7 4)")
    kv = {}
    for line in FORWARD.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()
    return kv


def run_bench(args, U, config):
    times, closes, prices_u = load_joint(U)
    n = len(times)
    bounds = split_bounds(n, args.windows)
    grids = grids_for(U)
    families = list(grids) if args.families == "all" else args.families.split(",")
    for f in families:
        if f not in grids:
            raise SystemExit(f"unknown family {f!r} (have {', '.join(grids)})")
    record = Record("records", "experiments", f"alloc7_{args.bench}",
                    config=config)
    lines = [f"dispersion-gate bench ({args.bench}: {','.join(U)}):"
             f" {n} joint days / {args.windows} windows, no historical"
             " carve — every past holdout span is spent (v7 1)", ""]
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
    headline = (f"ALLOC7 {args.bench.upper()} " + "; ".join(
        f"{f}: {v} ({w}/{t}, {d:+.2f} pp/yr)"
        for f, (v, w, t, d) in results.items())
        + f" | FRONTIER {frontier}"
        + (" (forward-holdout target by v7 4)" if args.bench == "full"
           else " (regime check only)"))
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[-1], entries)
    record.finish(headline, level="INFO")
    return 0


def run_holdout(args, config):
    """The forward shot (v7 4): only the declared family, only on 126+ rows
    that postdate the declared cutoff, only once."""
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v7 4)", file=sys.stderr)
        return 2
    fwd = read_forward()
    if args.holdout != fwd["family"]:
        print(f"forward declaration names {fwd['family']!r}, not"
              f" {args.holdout!r} — the target is frozen (v7 4)",
              file=sys.stderr)
        return 2
    cutoff = datetime.datetime.fromisoformat(fwd["cutoff"]).replace(
        tzinfo=datetime.timezone.utc).timestamp()
    min_rows = int(fwd["min_new_rows"])
    U = U_FULL if fwd["universe"] == "full" else U_ETF
    times, closes, prices_u = load_joint(U)
    h0 = next((k for k, t in enumerate(times) if t > cutoff), len(times))
    fresh = len(times) - h0
    if fresh < min_rows:
        print(f"forward holdout not ripe: {fresh} rows postdate"
              f" {fwd['cutoff']}, need {min_rows} (v7 4) — refetch tapes"
              " later and retry", file=sys.stderr)
        return 2
    family = args.holdout
    best = select(family, closes, prices_u, 0, h0, U)
    record = Record("records", "experiments", f"holdout_alloc7_{family}",
                    config=config)
    lines = [f"forward holdout shot (v7 4): family {family},"
             f" params [{fmt(best)}] re-selected on the {h0} pre-cutoff"
             f" rows, frozen; {fresh} virgin rows postdate {fwd['cutoff']}",
             ""]
    entries = []
    cash = run_window(family, best, closes, prices_u, h0, len(times), U)
    win, delta, spx_cash = judge(f"holdout {family} [{fmt(best)}]", cash,
                                 times[h0], times[-1], lines, entries)
    for mult, venue in allocation6.COST_LADDER:
        c2 = run_window(family, best, closes, prices_u, h0, len(times), U,
                        venue=venue)
        lines.append(f"  cost ladder {mult}: {money(c2)}"
                     f" ({'beat' if c2 > spx_cash else 'did not beat'} SPY)")
    bh = {x: run_window("best_bh", {"asset": x}, closes, prices_u, h0,
                        len(times), U) for x in U}
    lines.append("  context: holdout buy-and-hold "
                 + ", ".join(f"{x} {money(bh[x])}" for x in U))
    verdict = "BEATS-SPX" if win else "NO-EDGE"
    headline = (f"HOLDOUT alloc7 {family} [{fmt(best)}]: {verdict}"
                f" ({'beat' if win else 'did not beat'} SPY on {fresh}"
                f" virgin rows, delta {delta:+.2f} pp/yr)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[h0], times[-1], entries)
    record.finish(headline, level="INFO")
    SHOT.write_text(
        f"fired: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}\n"
        f"family: {family} [{fmt(best)}]\n{headline}\n"
        "reruns refuse: a second look requires data that postdates the shot"
        " (v7 4)\n", encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="v7 dispersion-gate bench")
    parser.add_argument("--bench", default="full", choices=("full", "etf"))
    parser.add_argument("--families", default="all")
    parser.add_argument("--windows", type=int, default=None)
    parser.add_argument("--holdout", default=None, metavar="FAMILY")
    args = parser.parse_args(argv)
    if args.windows is None:
        args.windows = 10 if args.bench == "full" else 12
    U = U_FULL if args.bench == "full" else U_ETF
    config = {"tapes": {x: tape_digest(ROOT / ASSETS[x][0]) for x in U},
              "universe": list(U), "windows": args.windows,
              "capital_u": CAPITAL_U, "venue": BASE_VENUE}
    if args.holdout:
        return run_holdout(args, config)
    return run_bench(args, U, config)


if __name__ == "__main__":
    sys.exit(main())

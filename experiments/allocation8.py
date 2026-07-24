"""v8 (spec v8): the Regime Bench — bull/bear timing with inverse ETFs.

The operator's idea: markets have bull and bear regimes, and one can hold
an *inverse ETF* to profit while the market falls rather than only fleeing
to cash. v8 tests that with v4-v7 discipline and connects it to the v6/v7
finding: a selloff creates dispersion between a long index and its inverse,
so an inverse ETF is a second, anti-correlated return stream a momentum
rotor can rotate into (mom_inv). The three regime_* families share one
regime clock and differ only in what they do in a bear (hold the inverse /
go to cash / flee to safety), so the ranking between them isolates whether
being short actually adds value.

Universe U_DIR = gld,psq,qqq,sh,spy,tlt on real inverse-ETF tapes (SH -1x
S&P500, PSQ -1x Nasdaq-100) whose daily-reset drag and expense are baked
into the closes. Same machinery as v6/v7 (load_joint mirrors v6 but over
this universe; momentum_ranked, rebalance, judge reused unchanged).

v8 fires one historical shot (v6 4 carve, with a contamination disclosure:
the author knew this span held the 2022 bear) and registers one forward
shot (v7 4: data/holdout/alloc8.FORWARD, clean, ripe ~2027).

Usage: python -m experiments.allocation8 [--families all|f1,f2]
       [--windows 10] [--holdout FAMILY [--forward]]
"""

import argparse
import datetime
import sys
from pathlib import Path

from colony.arenas.replay import read_rows, to_price_u
from colony.records import Record
from colony.report import money
from experiments.allocation import BASE_VENUE, CAPITAL_U, fmt, split_bounds
from experiments.allocation6 import (COST_LADDER, judge, momentum_ranked,
                                     rebalance)
from experiments.minute_ladder import tape_digest

ROOT = Path(__file__).resolve().parent.parent

ASSETS = {  # name -> (tape, lot_denominator) — v8 1
    "spy": ("data/spy_d.csv", 100),
    "qqq": ("data/qqq_d.csv", 100),
    "sh": ("data/sh_d.csv", 100),    # -1x S&P 500
    "psq": ("data/psq_d.csv", 100),  # -1x Nasdaq-100
    "gld": ("data/gld_d.csv", 100),
    "tlt": ("data/tlt_d.csv", 100),
}
U_DIR = tuple(sorted(ASSETS))
INVERSE = {"spy": "sh", "qqq": "psq"}  # long -> its listed inverse

HOLDOUT_CSV = ROOT / "data" / "holdout" / "alloc8.csv"
SHOT = ROOT / "data" / "holdout" / "alloc8.SHOT"
FORWARD = ROOT / "data" / "holdout" / "alloc8.FORWARD"


def grids_for():
    """Pre-declared in spec v8 2; order breaks train-window ties."""
    return {
        "regime_inv": [{"L": L, "R": R, "I": INVERSE[R]}
                       for L in (150, 200) for R in ("spy", "qqq")],
        "regime_flat": [{"L": L, "R": R}
                        for L in (150, 200) for R in ("spy", "qqq")],
        "regime_safe": [{"L": L, "R": R, "S": S}
                        for L in (150, 200) for R in ("spy", "qqq")
                        for S in ("gld", "tlt")],
        "mom_inv": [{"L": L} for L in (63, 126, 252)],
        "best_bh": [{"asset": a} for a in U_DIR],
    }


def load_joint(U):
    """Master clock = SPY trading days inside the span all universe tapes
    cover; non-SPY tapes sample their latest close <= each SPY day (v6 2)."""
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


def targets_for(family, params, C, i, a, state, U):
    """Target weights for day i using history <= i-1, or None to hold.
    Returns (targets_or_None, new_state). Regime families carry the last
    regime label in state and trade only when it changes (spec v8 2)."""
    j = i - 1
    if family in ("regime_inv", "regime_flat", "regime_safe"):
        L, R = params["L"], params["R"]
        if j - L + 1 < 0:  # not enough history for the SMA: stay in cash
            return ({} if i == a else None), state
        sma = sum(C[R][j - L + 1:j + 1]) / L
        bull = C[R][j] > sma
        if bull:
            tgt = {R: 1.0}
        elif family == "regime_inv":
            tgt = {params["I"]: 1.0}
        elif family == "regime_safe":
            tgt = {params["S"]: 1.0}
        else:  # regime_flat
            tgt = {}
        regime = next(iter(tgt)) if tgt else "cash"
        if regime == state:
            return None, state           # unchanged: hold, no churn
        return tgt, regime
    if family == "mom_inv":
        if (i - a) % 21:
            return None, state
        picks = momentum_ranked(C, j, params["L"], U)[:1]
        return {x: 1.0 for r, x in picks if r > 0}, state
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


def select(family, C, P, a, b, U):
    """Train-window selection: best combo by audited final cash; ties fall
    to the earlier entry in the pre-declared grid (v5 3)."""
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
        raise SystemExit("no data/holdout/alloc8.FORWARD declaration —"
                         " the forward shot is not registered (v8 4)")
    kv = {}
    for line in FORWARD.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()
    return kv


def run_bench(args, times, closes, prices_u, h0, U, config):
    bounds = split_bounds(h0, args.windows)
    grids = grids_for()
    families = list(grids) if args.families == "all" else args.families.split(",")
    for f in families:
        if f not in grids:
            raise SystemExit(f"unknown family {f!r} (have {', '.join(grids)})")
    record = Record("records", "experiments", "alloc8_dir", config=config)
    lines = [f"regime bench (dir: {','.join(U)}): {len(times)} joint days,"
             f" grid {h0} rows / {args.windows} windows, holdout"
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
    headline = ("ALLOC8 DIR " + "; ".join(
        f"{f}: {v} ({w}/{t}, {d:+.2f} pp/yr)"
        for f, (v, w, t, d) in results.items())
        + f" | FRONTIER {frontier} (holdout target by v8 4)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[h0 - 1], entries)
    record.finish(headline, level="INFO")
    return 0


def run_holdout(args, times, closes, prices_u, h0, U, config):
    """Historical shot (v8 4 carve): the reserved final 20%, with a
    contamination disclosure. --forward routes to the clean shot instead."""
    if args.forward:
        return run_forward(args, config)
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v8 4)", file=sys.stderr)
        return 2
    family = args.holdout
    if family not in grids_for():
        raise SystemExit(f"unknown family {family!r}")
    best = select(family, closes, prices_u, 0, h0, U)
    record = Record("records", "experiments", f"holdout_alloc8_{family}",
                    config=config)
    lines = [f"historical holdout shot (v8 4): family {family}, params"
             f" [{fmt(best)}] re-selected on the grid span ({h0} rows), frozen",
             "contamination disclosure (v8 4): the author knew this span held"
             " the 2022 bear when designing the regime families — weaker"
             " evidence than the forward shot (alloc8.FORWARD)", ""]
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
    headline = (f"HOLDOUT alloc8 {family} [{fmt(best)}]: {verdict}"
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
        " (v8 4)\n", encoding="utf-8")
    return 0


def run_forward(args, config):
    """The clean forward shot (v8 4 / v7 4 precedent): only the declared
    family, only on 126+ rows that postdate the cutoff, only once."""
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v8 4)", file=sys.stderr)
        return 2
    fwd = read_forward()
    if args.holdout != fwd["family"]:
        print(f"forward declaration names {fwd['family']!r}, not"
              f" {args.holdout!r} — the target is frozen (v8 4)",
              file=sys.stderr)
        return 2
    cutoff = datetime.datetime.fromisoformat(fwd["cutoff"]).replace(
        tzinfo=datetime.timezone.utc).timestamp()
    min_rows = int(fwd["min_new_rows"])
    times, closes, prices_u = load_joint(U_DIR)
    h0 = next((k for k, t in enumerate(times) if t > cutoff), len(times))
    fresh = len(times) - h0
    if fresh < min_rows:
        print(f"forward holdout not ripe: {fresh} rows postdate"
              f" {fwd['cutoff']}, need {min_rows} (v8 4) — refetch tapes"
              " later and retry", file=sys.stderr)
        return 2
    family = args.holdout
    best = select(family, closes, prices_u, 0, h0, U_DIR)
    record = Record("records", "experiments", f"forward_alloc8_{family}",
                    config=config)
    lines = [f"forward holdout shot (v8 4): family {family}, params"
             f" [{fmt(best)}] re-selected on the {h0} pre-cutoff rows, frozen;"
             f" {fresh} virgin rows postdate {fwd['cutoff']}", ""]
    entries = []
    cash = run_window(family, best, closes, prices_u, h0, len(times), U_DIR)
    win, delta, spx_cash = judge(f"forward {family} [{fmt(best)}]", cash,
                                 times[h0], times[-1], lines, entries)
    for mult, venue in COST_LADDER:
        c2 = run_window(family, best, closes, prices_u, h0, len(times), U_DIR,
                        venue=venue)
        lines.append(f"  cost ladder {mult}: {money(c2)}"
                     f" ({'beat' if c2 > spx_cash else 'did not beat'} SPY)")
    verdict = "BEATS-SPX" if win else "NO-EDGE"
    headline = (f"FORWARD alloc8 {family} [{fmt(best)}]: {verdict}"
                f" ({'beat' if win else 'did not beat'} SPY on {fresh}"
                f" virgin rows, delta {delta:+.2f} pp/yr)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[h0], times[-1], entries)
    record.finish(headline, level="INFO")
    SHOT.write_text(
        f"fired: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}\n"
        f"family: {family} [{fmt(best)}] (forward)\n{headline}\n"
        "reruns refuse: a second look requires data that postdates the shot"
        " (v8 4)\n", encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="v8 regime bench")
    parser.add_argument("--families", default="all")
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--holdout", default=None, metavar="FAMILY")
    parser.add_argument("--forward", action="store_true",
                        help="route --holdout to the clean forward shot")
    args = parser.parse_args(argv)
    U = U_DIR
    times, closes, prices_u = load_joint(U)
    n = len(times)
    h0 = n - n // 5
    carve_holdout(times, closes, h0, U)
    config = {"tapes": {x: tape_digest(ROOT / ASSETS[x][0]) for x in U},
              "universe": list(U), "joint_rows": n, "grid_rows": h0,
              "windows": args.windows, "capital_u": CAPITAL_U,
              "venue": BASE_VENUE}
    if args.holdout:
        return run_holdout(args, times, closes, prices_u, h0, U, config)
    return run_bench(args, times, closes, prices_u, h0, U, config)


if __name__ == "__main__":
    sys.exit(main())

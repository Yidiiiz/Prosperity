"""v9 (spec v9): the Wish Bench — Green Line Breakout, GMI-gated inverse,
defense/construction sectors, and leveraged inverse ETFs done correctly.

Operator directive: implement Dr. Eric Wish's strategies (Green Line
Breakout and his market-timing) and the ~3x leveraged inverse products, and
add defensive/thematic sectors. v9 settles the 3x question empirically: SQQQ
tracks -3x QQQ per DAY (measured beta -2.96) but daily reset decays it to ~$0
over 2010->2026, so a -3x ETF is a short-holding tactical instrument gated by
a timing signal, never a hold. gmi_inv puts -1x, -2x, and -3x bear legs
head-to-head under the same gate.

Universe U_WISH on real tapes (SQQQ/SPXU -3x, SDS -2x, ITA defense, ITB
construction) bound by SQQQ inception 2010-02-11. Same machinery as v6/v8
(momentum_ranked, rebalance, judge, COST_LADDER reused; own load_joint).

Fires one historical shot (disclosed-contaminated) and registers one clean
forward shot (alloc9.FORWARD, ripe ~2027).

Usage: python -m experiments.allocation9 [--families all|f1,f2]
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

ASSETS = {  # name -> (tape, lot_denominator) — v9 1
    "spy": ("data/spy_d.csv", 100),
    "qqq": ("data/qqq_d.csv", 100),
    "gld": ("data/gld_d.csv", 100),
    "tlt": ("data/tlt_d.csv", 100),
    "ita": ("data/ita_d.csv", 100),    # aerospace & defense
    "itb": ("data/itb_d.csv", 100),    # home construction
    "sh": ("data/sh_d.csv", 100),      # -1x S&P 500
    "psq": ("data/psq_d.csv", 100),    # -1x Nasdaq-100
    "sds": ("data/sds_d.csv", 100),    # -2x S&P 500
    "spxu": ("data/spxu_d.csv", 100),  # -3x S&P 500
    "sqqq": ("data/sqqq_d.csv", 100),  # -3x Nasdaq-100
}
U_WISH = tuple(sorted(ASSETS))
MOM_U = ("spy", "qqq", "gld", "tlt", "ita", "itb")  # long-only rotor sleeve
GL_CONFIRM = 63  # ~3 months: a green line is an all-time high this old

HOLDOUT_CSV = ROOT / "data" / "holdout" / "alloc9.csv"
SHOT = ROOT / "data" / "holdout" / "alloc9.SHOT"
FORWARD = ROOT / "data" / "holdout" / "alloc9.FORWARD"


def grids_for():
    """Pre-declared in spec v9 2; order breaks train-window ties."""
    return {
        "glb": [{"R": R, "S": S} for R in ("qqq", "spy") for S in (150, 210)],
        "gmi_inv": [{"R": R, "I": I, "S": S}
                    for R, I in (("qqq", "psq"), ("qqq", "sqqq"),
                                 ("spy", "sh"), ("spy", "spxu"), ("spy", "sds"))
                    for S in (150, 210)],
        "sector_mom": [{"L": L, "K": K} for L in (63, 126, 252)
                       for K in (1, 2)],
        "best_bh": [{"asset": a} for a in U_WISH],
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
    Returns (targets_or_None, new_state)."""
    j = i - 1
    if family == "glb":
        R, S = params["R"], params["S"]
        if j - GL_CONFIRM < 0 or j - S + 1 < 0:  # warmup: cash
            return ({} if i == a else None), False
        green = max(C[R][:j - GL_CONFIRM + 1])   # highest close >= 3mo old
        ma = sum(C[R][j - S + 1:j + 1]) / S
        prev = bool(state)
        if not prev and C[R][j] > green:
            cur = True                            # breakout entry
        elif prev and C[R][j] < ma:
            cur = False                           # MA-stop exit
        else:
            cur = prev
        if cur == prev and i != a:
            return None, cur                      # no change: hold
        return ({R: 1.0} if cur else {}), cur
    if family == "gmi_inv":
        R, I, S = params["R"], params["I"], params["S"]
        if j - S + 1 < 0:
            return ({} if i == a else None), state
        ma = sum(C[R][j - S + 1:j + 1]) / S
        tgt = {R: 1.0} if C[R][j] > ma else {I: 1.0}
        regime = next(iter(tgt))
        if regime == state:
            return None, state
        return tgt, regime
    if family == "sector_mom":
        if (i - a) % 21:
            return None, state
        picks = momentum_ranked(C, j, params["L"], MOM_U)[:params["K"]]
        return {x: 1.0 / params["K"] for r, x in picks if r > 0}, state
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
        raise SystemExit("no data/holdout/alloc9.FORWARD declaration —"
                         " the forward shot is not registered (v9 4)")
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
    record = Record("records", "experiments", "alloc9_wish", config=config)
    lines = [f"wish bench: {len(times)} joint days, grid {h0} rows /"
             f" {args.windows} windows, holdout {len(times) - h0} rows"
             f" (universe {','.join(U)})", ""]
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
    headline = ("ALLOC9 WISH " + "; ".join(
        f"{f}: {v} ({w}/{t}, {d:+.2f} pp/yr)"
        for f, (v, w, t, d) in results.items())
        + f" | FRONTIER {frontier} (holdout target by v9 4)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[h0 - 1], entries)
    record.finish(headline, level="INFO")
    return 0


def _shot_common(family, best, times, closes, prices_u, h0, U, config,
                 tag, disclosure):
    record = Record("records", "experiments", f"{tag}_alloc9_{family}",
                    config=config)
    lines = [f"{disclosure[0]}: family {family}, params [{fmt(best)}]"
             f" re-selected on {h0} rows, frozen", disclosure[1], ""]
    entries = []
    cash = run_window(family, best, closes, prices_u, h0, len(times), U)
    win, delta, spx_cash = judge(f"{tag} {family} [{fmt(best)}]", cash,
                                 times[h0], times[-1], lines, entries)
    for mult, venue in COST_LADDER:
        c2 = run_window(family, best, closes, prices_u, h0, len(times), U,
                        venue=venue)
        lines.append(f"  cost ladder {mult}: {money(c2)}"
                     f" ({'beat' if c2 > spx_cash else 'did not beat'} SPY)")
    verdict = "BEATS-SPX" if win else "NO-EDGE"
    fresh = len(times) - h0
    headline = (f"{tag.upper()} alloc9 {family} [{fmt(best)}]: {verdict}"
                f" ({'beat' if win else 'did not beat'} SPY on {fresh} rows,"
                f" delta {delta:+.2f} pp/yr)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[h0], times[-1], entries)
    record.finish(headline, level="INFO")
    SHOT.write_text(
        f"fired: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}\n"
        f"family: {family} [{fmt(best)}] ({tag})\n{headline}\n"
        "reruns refuse: a second look requires data that postdates the shot"
        " (v9 4)\n", encoding="utf-8")
    return 0


def run_holdout(args, times, closes, prices_u, h0, U, config):
    if args.forward:
        return run_forward(args, config)
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v9 4)", file=sys.stderr)
        return 2
    family = args.holdout
    if family not in grids_for():
        raise SystemExit(f"unknown family {family!r}")
    best = select(family, closes, prices_u, 0, h0, U)
    return _shot_common(
        family, best, times, closes, prices_u, h0, U, config, "holdout",
        ("historical holdout shot (v9 4)",
         "contamination disclosure (v9 4): the author knew this span held the"
         " 2018/2020/2022 drawdowns when designing the families — weaker"
         " evidence than the forward shot (alloc9.FORWARD)"))


def run_forward(args, config):
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v9 4)", file=sys.stderr)
        return 2
    fwd = read_forward()
    if args.holdout != fwd["family"]:
        print(f"forward declaration names {fwd['family']!r}, not"
              f" {args.holdout!r} — the target is frozen (v9 4)",
              file=sys.stderr)
        return 2
    cutoff = datetime.datetime.fromisoformat(fwd["cutoff"]).replace(
        tzinfo=datetime.timezone.utc).timestamp()
    min_rows = int(fwd["min_new_rows"])
    times, closes, prices_u = load_joint(U_WISH)
    h0 = next((k for k, t in enumerate(times) if t > cutoff), len(times))
    fresh = len(times) - h0
    if fresh < min_rows:
        print(f"forward holdout not ripe: {fresh} rows postdate"
              f" {fwd['cutoff']}, need {min_rows} (v9 4) — refetch tapes"
              " later and retry", file=sys.stderr)
        return 2
    family = args.holdout
    best = select(family, closes, prices_u, 0, h0, U_WISH)
    return _shot_common(
        family, best, times, closes, prices_u, h0, U_WISH, config, "forward",
        ("forward holdout shot (v9 4)",
         f"{fresh} virgin rows postdate {fwd['cutoff']} — the clean test"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="v9 wish bench")
    parser.add_argument("--families", default="all")
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--holdout", default=None, metavar="FAMILY")
    parser.add_argument("--forward", action="store_true",
                        help="route --holdout to the clean forward shot")
    args = parser.parse_args(argv)
    U = U_WISH
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

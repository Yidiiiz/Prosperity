"""v10 (spec v10): the Careful Wish Bench — the operator's refinements.

v9 implemented Dr. Wish's ideas crudely and everything lost to buy-and-hold;
gmi_inv (single-MA gate straight into a decaying leveraged inverse) was the
worst family. The operator refined the brief: GMI is a *count*, switch on it
carefully (red -> inverse OR cash/bonds/gold, green -> back to the ETF), and
GLB rides an all-time high until a ~5% drop from it.

v10 does three things carefully:
  * gmi_switch -- a 6-component GMI-lite (0..6) with a hysteresis band (red
    below 3, green at/above 4, per Wish's "defensive below 4, cash below 3"),
    and the red destination {cash, gld, tlt, -1x, -3x} as a bench parameter so
    "an inverse or smth else" is decided head-to-head, not assumed.
  * glb_pct -- Green Line Breakout with a percent trailing stop from the
    running all-time high (the operator's 5% rule), replacing v9's MA stop.
  * gmi_glb -- GLB entries gated by GMI-lite green, as Wish actually trades.

GMI-lite is a disclosed approximation: the real GMI's breadth/new-high
components need a ~4,000-stock universe the repo lacks; components here are
index-vs-MA/short-trend on SPY/QQQ plus a narrow {spy,qqq,ita,itb} breadth
proxy. Faithful in spirit, not the true GMI.

Universe = the v9 tapes bound by SQQQ inception 2010. Same machinery as
v6/v9 (rebalance, judge, COST_LADDER reused; own load_joint). Fires one
historical shot (disclosed-contaminated, now with a drawdown diagnostic) and
registers one clean forward shot (alloc10.FORWARD, ripe ~2027).

Usage: python -m experiments.allocation10 [--families all|f1,f2]
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
from experiments.allocation6 import COST_LADDER, judge, rebalance
from experiments.minute_ladder import tape_digest

ROOT = Path(__file__).resolve().parent.parent

ASSETS = {  # name -> (tape, lot_denominator) — v10 1
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
U_WISH2 = tuple(sorted(ASSETS))
GL_CONFIRM = 63              # ~3 months: a green line is an all-time high this old
INV = {"qqq": ("psq", "sqqq"), "spy": ("sh", "spxu")}  # (-1x, -3x) per index
GMI_RED, GMI_GREEN = 3, 4    # hysteresis band (Wish: defensive <4, cash <3)
GMI_WARMUP = 200             # longest lookback in the GMI-lite count

HOLDOUT_CSV = ROOT / "data" / "holdout" / "alloc10.csv"
SHOT = ROOT / "data" / "holdout" / "alloc10.SHOT"
FORWARD = ROOT / "data" / "holdout" / "alloc10.FORWARD"


def grids_for():
    """Pre-declared in spec v10 2; order breaks train-window ties."""
    return {
        "glb_pct": [{"R": R, "p": p} for R in ("qqq", "spy")
                    for p in (0.03, 0.05, 0.08)],
        "gmi_switch": [{"R": R, "D": D} for R in ("qqq", "spy")
                       for D in ("cash", "gld", "tlt", "inv1", "inv3")],
        "gmi_glb": [{"R": R, "p": p} for R in ("qqq", "spy")
                    for p in (0.05, 0.08)],
        "best_bh": [{"asset": a} for a in U_WISH2],
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


def _sma(C, x, j, L):
    return sum(C[x][j - L + 1:j + 1]) / L


def gmi_count(C, j):
    """GMI-lite: a 0..6 tally of trend signals (history <= j), or None during
    warmup. A disclosed proxy for Dr. Wish's six-component GMI (spec v10 0)."""
    if j < GMI_WARMUP or j - 10 < 0:
        return None
    c = 0
    c += C["qqq"][j] > _sma(C, "qqq", j, 50)
    c += C["qqq"][j] > _sma(C, "qqq", j, 150)
    c += C["spy"][j] > _sma(C, "spy", j, 50)
    c += C["spy"][j] > _sma(C, "spy", j, 200)
    c += C["qqq"][j] > C["qqq"][j - 10]
    breadth = sum(C[x][j] > _sma(C, x, j, 50) for x in ("spy", "qqq", "ita", "itb"))
    c += breadth >= 2
    return int(c)


def _gmi_phase(prev_green, count):
    """Hysteresis: stay put inside the [GMI_RED, GMI_GREEN) band."""
    if count is None:
        return False
    if prev_green:
        return count >= GMI_RED          # only leave green below the red line
    return count >= GMI_GREEN            # only re-enter green at/above 4


def targets_for(family, params, C, i, a, state, U):
    """Target weights for day i using history <= i-1, or None to hold.
    Returns (targets_or_None, new_state)."""
    j = i - 1
    if family == "glb_pct":
        R, p = params["R"], params["p"]
        if j - GL_CONFIRM < 0:
            return ({} if i == a else None), False
        green = max(C[R][:j - GL_CONFIRM + 1])   # highest close >= 3mo old
        peak = max(C[R][:j + 1])                 # running all-time high
        prev = bool(state)
        if not prev and C[R][j] > green:
            cur = True                            # breakout entry
        elif prev and C[R][j] <= (1.0 - p) * peak:
            cur = False                           # p% stop from the high
        else:
            cur = prev
        if cur == prev and i != a:
            return None, cur
        return ({R: 1.0} if cur else {}), cur
    if family == "gmi_switch":
        R, D = params["R"], params["D"]
        prev_green = None if state is None else state[0]
        count = gmi_count(C, j)
        if count is None:                         # warmup: cash
            return ({} if i == a else None), (False, "cash")
        green = _gmi_phase(bool(prev_green), count)
        if green:
            hold = R
        elif D == "cash":
            hold = "cash"
        elif D == "inv1":
            hold = INV[R][0]
        elif D == "inv3":
            hold = INV[R][1]
        else:
            hold = D                              # gld / tlt
        new = (green, hold)
        if state is not None and state == new and i != a:
            return None, new
        return ({} if hold == "cash" else {hold: 1.0}), new
    if family == "gmi_glb":
        R, p = params["R"], params["p"]
        if j - GL_CONFIRM < 0:
            return ({} if i == a else None), False
        count = gmi_count(C, j)
        green_mkt = count is not None and count >= GMI_RED  # market not red
        green_line = max(C[R][:j - GL_CONFIRM + 1])
        peak = max(C[R][:j + 1])
        prev = bool(state)
        if not prev and green_mkt and C[R][j] > green_line:
            cur = True                            # breakout while GMI green
        elif prev and (not green_mkt or C[R][j] <= (1.0 - p) * peak):
            cur = False                           # GMI red or p% stop
        else:
            cur = prev
        if cur == prev and i != a:
            return None, cur
        return ({R: 1.0} if cur else {}), cur
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
    """Like run_window but also returns the strategy's max drawdown over the
    window (a diagnostic; never changes a verdict — spec v10 3)."""
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


def spy_maxdd(closes, a, b):
    """Max drawdown of a SPY buy-and-hold equity curve over [a, b)."""
    peak, mdd = closes["spy"][a], 0.0
    for i in range(a, b):
        c = closes["spy"][i]
        peak = max(peak, c)
        mdd = max(mdd, (peak - c) / peak)
    return mdd


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
        raise SystemExit("no data/holdout/alloc10.FORWARD declaration —"
                         " the forward shot is not registered (v10 4)")
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
    record = Record("records", "experiments", "alloc10_wish2", config=config)
    lines = [f"careful wish bench: {len(times)} joint days, grid {h0} rows /"
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
    headline = ("ALLOC10 WISH2 " + "; ".join(
        f"{f}: {v} ({w}/{t}, {d:+.2f} pp/yr)"
        for f, (v, w, t, d) in results.items())
        + f" | FRONTIER {frontier} (holdout target by v10 4)")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[h0 - 1], entries)
    record.finish(headline, level="INFO")
    return 0


def _shot_common(family, best, times, closes, prices_u, h0, U, config,
                 tag, disclosure):
    record = Record("records", "experiments", f"{tag}_alloc10_{family}",
                    config=config)
    lines = [f"{disclosure[0]}: family {family}, params [{fmt(best)}]"
             f" re-selected on {h0} rows, frozen", disclosure[1], ""]
    entries = []
    cash, mdd = run_curve(family, best, closes, prices_u, h0, len(times), U)
    win, delta, spx_cash = judge(f"{tag} {family} [{fmt(best)}]", cash,
                                 times[h0], times[-1], lines, entries)
    lines.append(f"  drawdown diagnostic: {family} maxDD {mdd * 100:.1f}%"
                 f" vs SPY buy-hold maxDD {spy_maxdd(closes, h0, len(times)) * 100:.1f}%"
                 " (does not change the verdict — v10 3)")
    for mult, venue in COST_LADDER:
        c2 = run_window(family, best, closes, prices_u, h0, len(times), U,
                        venue=venue)
        lines.append(f"  cost ladder {mult}: {money(c2)}"
                     f" ({'beat' if c2 > spx_cash else 'did not beat'} SPY)")
    verdict = "BEATS-SPX" if win else "NO-EDGE"
    fresh = len(times) - h0
    headline = (f"{tag.upper()} alloc10 {family} [{fmt(best)}]: {verdict}"
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
        " (v10 4)\n", encoding="utf-8")
    return 0


def run_holdout(args, times, closes, prices_u, h0, U, config):
    if args.forward:
        return run_forward(args, config)
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v10 4)", file=sys.stderr)
        return 2
    family = args.holdout
    if family not in grids_for():
        raise SystemExit(f"unknown family {family!r}")
    best = select(family, closes, prices_u, 0, h0, U)
    return _shot_common(
        family, best, times, closes, prices_u, h0, U, config, "holdout",
        ("historical holdout shot (v10 4)",
         "contamination disclosure (v10 4): the author knew this span held the"
         " 2018/2020/2022/2025 drawdowns when designing the families — weaker"
         " evidence than the forward shot (alloc10.FORWARD)"))


def run_forward(args, config):
    if SHOT.exists():
        print(f"holdout already fired — {SHOT} exists; a second look requires"
              " data that postdates the shot (v10 4)", file=sys.stderr)
        return 2
    fwd = read_forward()
    if args.holdout != fwd["family"]:
        print(f"forward declaration names {fwd['family']!r}, not"
              f" {args.holdout!r} — the target is frozen (v10 4)",
              file=sys.stderr)
        return 2
    cutoff = datetime.datetime.fromisoformat(fwd["cutoff"]).replace(
        tzinfo=datetime.timezone.utc).timestamp()
    min_rows = int(fwd["min_new_rows"])
    times, closes, prices_u = load_joint(U_WISH2)
    h0 = next((k for k, t in enumerate(times) if t > cutoff), len(times))
    fresh = len(times) - h0
    if fresh < min_rows:
        print(f"forward holdout not ripe: {fresh} rows postdate"
              f" {fwd['cutoff']}, need {min_rows} (v10 4) — refetch tapes"
              " later and retry", file=sys.stderr)
        return 2
    family = args.holdout
    best = select(family, closes, prices_u, 0, h0, U_WISH2)
    return _shot_common(
        family, best, times, closes, prices_u, h0, U_WISH2, config, "forward",
        ("forward holdout shot (v10 4)",
         f"{fresh} virgin rows postdate {fwd['cutoff']} — the clean test"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="v10 careful wish bench")
    parser.add_argument("--families", default="all")
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--holdout", default=None, metavar="FAMILY")
    parser.add_argument("--forward", action="store_true",
                        help="route --holdout to the clean forward shot")
    args = parser.parse_args(argv)
    U = U_WISH2
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

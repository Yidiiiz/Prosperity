"""The v4 flagship: the frequency × asset grid (spec v4 sections 4–6).

Every cell = (tape, cadence profile) runs the v3 walk-forward — evolve on
window k, certify champions by frozen solo probe on window k+1 — on the tape
MINUS its holdout (the final 20 % by rows, carved before windowing and never
readable by any grid run; v4 6.1). Certified out-of-sample cash is judged
three ways: vs initial, vs buy-and-hold of the same tape, vs buy-and-hold of
SPY over the SAME calendar window (the operator's bar). Cell verdicts per
seed (v4 2.3): BEATS-SPX / EDGE / NO-EDGE, FAIL only for broken machinery.

The cost arm (v4 5) re-runs a cell at counterfactual venue costs (`--cost
cheap|free`) to separate "signal too weak" from "friction eats it" — those
records are labeled counterfactual and never touch the holdout. The holdout
shot (v4 6.2, `--holdout CELL`) runs exactly once, at base costs.

Usage: python -m experiments.frequency_grid [--cells a,b,...] [--seeds ...]
       [--workdir work_grid] [--cost base|cheap|free] [--min-fills N]
       [--parallel] [--summarize] [--holdout CELL]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from colony import bank, benchmark
from colony.arenas.replay import read_rows
from colony.records import Record
from experiments import walk_forward as wf
from experiments.minute_ladder import tape_digest
from experiments.profiles import PROFILES
from experiments.yardstick import spx_line, spx_over

ROOT = Path(__file__).resolve().parent.parent
SEEDS = [42, 7, 2026]
HOLDOUT_FRACTION = 0.2  # v4 6.1: final 20 % by row count, carved first
COSTS = {"base": (10, 2), "cheap": (2, 1), "free": (0, 0)}  # (taker, spread) bps

CELLS = {  # v4 4.1 — the built-in manifest
    "btc_1s": {"csv": "data/btcusdt_1s.csv", "profile": "second",
               "lot": 100_000, "windows": 3},
    "btc_1m": {"csv": "data/btcusdt_1m.csv", "profile": "minute",
               "lot": 100_000, "windows": 3},
    "btc_1h": {"csv": "data/btcusdt_1h.csv", "profile": "hourly",
               "lot": 100_000, "windows": 4},
    "btc_1d": {"csv": "data/btcusdt_1d.csv", "profile": "daily",
               "lot": 100_000, "windows": 4},
    "eth_1h": {"csv": "data/ethusdt_1h.csv", "profile": "hourly",
               "lot": 100_000, "windows": 4},
    "eth_1d": {"csv": "data/ethusdt_1d.csv", "profile": "daily",
               "lot": 100_000, "windows": 4},
    "spy_1d": {"csv": "data/spy_d.csv", "profile": "daily",
               "lot": 100, "windows": 4},
    "qqq_1d": {"csv": "data/qqq_d.csv", "profile": "daily",
               "lot": 100, "windows": 4},
}


def carve_holdout(name, times, closes):
    """Slice the final HOLDOUT_FRACTION off BEFORE windowing (v4 6.1) and pin
    it to data/holdout/<cell>.csv. A drifted holdout file refuses — the
    holdout is one-shot evidence, not a scratch file."""
    cut = int(len(closes) * (1 - HOLDOUT_FRACTION))
    if cut < 4 or len(closes) - cut < 2:
        raise SystemExit(f"{name}: tape too small to carve a holdout")
    path = ROOT / "data" / "holdout" / f"{name}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _, existing = read_rows(path)
        if existing != closes[cut:]:
            raise SystemExit(f"{name}: holdout file {path} does not match the"
                             " tape's final rows — refusing (v4 6.1)")
    else:  # atomic: parallel seeds of one cell carve concurrently
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
        wf.write_window(times[cut:], closes[cut:], tmp)
        try:
            os.replace(tmp, path)
        except PermissionError:  # Windows: another seed carved it and holds
            tmp.unlink()         # it open — same rows either way; verify
            _, existing = read_rows(path)
            if existing != closes[cut:]:
                raise SystemExit(f"{name}: holdout drift under contention")
    return (times[:cut], closes[:cut]), (times[cut:], closes[cut:]), path


def make_factory(spec, cell_name, cost):
    prof, (taker, spread) = PROFILES[spec["profile"]], COSTS[cost]

    def factory(seed, csv_path):
        cfg = prof(seed, csv_path, lot_denominator=spec["lot"], name=cell_name)
        cfg["venue"]["taker_bps"] = taker
        cfg["venue"]["spread_bps"] = spread
        cfg.update(spec.get("overrides", {}))  # tests shrink populations here
        return cfg
    return factory


def judge_seed(v3_verdict, entries, venue):
    """Cell verdict per seed (v4 2.3) + per-window metrics. SPY windows with
    zero coverage don't count as SPX tests (there is nothing to beat)."""
    wins_spx = tests_spx = 0
    metrics, lines = [], []
    for label, initial_u, pooled_u, bench_u, t0, t1 in entries:
        spx_cash, spx_cagr, cov = spx_over(t0, t1, initial_u, venue)
        years = benchmark.span_years(t0, t1)
        if cov > 0:
            tests_spx += 1
            wins_spx += pooled_u > spx_cash
        lines.append("  " + spx_line(label, t0, t1, initial_u, pooled_u, venue))
        metrics.append({
            "label": label, "years": years,
            "cell_cagr": benchmark.cagr(initial_u, pooled_u, years),
            "tape_cagr": benchmark.cagr(initial_u, bench_u, years),
            "spx_cagr": spx_cagr if cov > 0 else None, "spx_coverage": cov,
        })
    if entries and tests_spx and wins_spx * 2 > tests_spx:
        verdict = "BEATS-SPX"
    elif v3_verdict == "EDGE":
        verdict = "EDGE"
    else:
        verdict = "NO-EDGE"
    return verdict, metrics, lines


def base_venue():
    return make_factory(CELLS["spy_1d"], "spy_1d", "base")(0, "x")["venue"]


def run_cell(name, args, seeds):
    spec = CELLS[name]
    csv_path = ROOT / spec["csv"]
    if not csv_path.exists():
        raise SystemExit(f"{name}: missing tape {csv_path} — fetch it first (v4 7)")
    tag = name if args.cost == "base" else f"{name}_cost_{args.cost}"
    workdir = Path(args.workdir) / tag
    times, closes = read_rows(csv_path)
    (g_times, g_closes), (h_times, _), h_path = carve_holdout(name, times, closes)
    windows = wf.split_windows(g_times, g_closes, spec["windows"])
    assert windows[-1][0][-1] < h_times[0], "grid window touches holdout (v4 6.1)"
    record = Record("records", "experiments", f"frequency_grid_{tag}",
                    config={"cell": name, "cost": args.cost,
                            "csv": str(csv_path), "digest": tape_digest(csv_path),
                            "grid_rows": len(g_closes), "holdout": str(h_path),
                            "windows": spec["windows"], "seeds": seeds},
                    seed=seeds)
    lines = [f"cell {name} ({spec['profile']} bars, cost {args.cost}"
             f" taker/spread {COSTS[args.cost][0]}/{COSTS[args.cost][1]} bps):"
             f" {len(g_closes)} grid rows, holdout {len(h_times)} rows carved", ""]
    if args.cost != "base":
        lines.insert(1, "COUNTERFACTUAL — no retail venue offers these costs;"
                        " this arm isolates friction from signal (v4 5.2)")
    entries_all, machinery_ok = [], True
    for seed in seeds:
        lines.append(f"seed {seed}:")
        shim = SimpleNamespace(profile=spec["profile"], min_fills=args.min_fills)
        try:
            v3_verdict, entries = wf.run_seed(
                seed, windows, workdir / f"s{seed}", shim, lines,
                make_cfg=make_factory(spec, name, args.cost))
            verdict, metrics, spx_lines = judge_seed(v3_verdict, entries,
                                                     base_venue())
        except Exception as exc:
            machinery_ok, verdict, entries, metrics, spx_lines = \
                False, "FAIL", [], [], [f"  machinery: {exc}"]
        lines += spx_lines + [f"  seed {seed}: {verdict}", ""]
        print(f"[{tag}] seed {seed}: {verdict}", flush=True)
        entries_all.extend(entries)
        out = Path(args.workdir) / f"result_{tag}_{seed}.json"
        out.write_text(json.dumps({
            "cell": name, "cost": args.cost, "seed": seed, "verdict": verdict,
            "metrics": metrics}, indent=1), encoding="utf-8")
    headline = f"GRID CELL {tag}: " + ", ".join(
        json.loads((Path(args.workdir) / f"result_{tag}_{s}.json")
                   .read_text(encoding="utf-8"))["verdict"] for s in seeds)
    if not machinery_ok:
        headline = f"GRID CELL {tag} MACHINERY FAIL"
    lines.append(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(g_times[0], g_times[-1], entries_all)
    record.finish(headline, level="INFO" if machinery_ok else "CRITICAL")
    return machinery_ok


def summarize(args):
    """The frontier table (v4 4.3): one row per (cell, cost, seed) from the
    incremental JSON results."""
    rows = []
    for f in sorted(Path(args.workdir).glob("result_*.json")):
        rows.append(json.loads(f.read_text(encoding="utf-8")))
    if not rows:
        raise SystemExit(f"no result_*.json under {args.workdir}")
    record = Record("records", "experiments", "frequency_grid_summary",
                    config={"workdir": args.workdir, "results": len(rows)},
                    seed=[r["seed"] for r in rows])
    lines = ["cell        cost   seed  verdict     oos%/yr   tape%/yr   spx%/yr", ""]
    for r in rows:
        ms = [m for m in r["metrics"] if m["spx_cagr"] is not None]
        if ms:
            cell = sum(m["cell_cagr"] for m in ms) / len(ms) * 100
            tape = sum(m["tape_cagr"] for m in ms) / len(ms) * 100
            spx = sum(m["spx_cagr"] for m in ms) / len(ms) * 100
            nums = f"{cell:+9.2f} {tape:+10.2f} {spx:+9.2f}"
        else:
            nums = "  no certified champions"
        lines.append(f"{r['cell']:<11} {r['cost']:<6} {r['seed']:<5}"
                     f" {r['verdict']:<11}{nums}")
    lines += ["", "(%/yr = mean CAGR across that seed's out-of-sample test"
              " windows; sub-year windows are projections — v3 2.3; cheap/free"
              " cost rows are counterfactual — v4 5.2)"]
    best = max((r for r in rows if r["cost"] == "base"
                and any(m["spx_cagr"] is not None for m in r["metrics"])),
               key=lambda r: sum(m["cell_cagr"] - m["spx_cagr"]
                                 for m in r["metrics"]
                                 if m["spx_cagr"] is not None),
               default=None)
    headline = ("FRONTIER: best base-cost cell "
                f"{best['cell']} seed {best['seed']} ({best['verdict']})"
                if best else "FRONTIER: no cell certified any champion")
    lines.append(headline)
    print("\n".join(lines))
    record.section("frontier", "\n".join(lines))
    record.finish(headline, level="INFO")
    return 0


def holdout_shot(args, seeds):
    """v4 6.2: ONE run — evolve on the last pre-holdout window, certify the
    candidates on the holdout, at base costs. Refuses to run twice."""
    name = args.holdout
    spec = CELLS[name]
    guard = ROOT / "data" / "holdout" / f"{name}.SHOT"
    if guard.exists():
        raise SystemExit(f"holdout for {name} already fired"
                         f" ({guard.read_text(encoding='utf-8').strip()}) —"
                         " a second look is data snooping (v4 6.2)")
    times, closes = read_rows(ROOT / spec["csv"])
    (g_times, g_closes), (h_times, h_closes), h_path = \
        carve_holdout(name, times, closes)
    windows = wf.split_windows(g_times, g_closes, spec["windows"])
    record = Record("records", "experiments", f"holdout_{name}",
                    config={"cell": name, "holdout": str(h_path),
                            "holdout_digest": tape_digest(h_path),
                            "seeds": seeds}, seed=seeds)
    lines = [f"HOLDOUT SHOT {name}: evolve on final grid window"
             f" ({len(windows[-1][0])} rows), certify on holdout"
             f" ({len(h_closes)} rows), base costs", ""]
    factory = make_factory(spec, name, "base")
    entries, wins, tests, machinery_ok = [], 0, 0, True
    workdir = Path(args.workdir) / f"holdout_{name}"
    workdir.mkdir(parents=True, exist_ok=True)
    for seed in seeds:
        try:
            bank_file = workdir / f"bank_{seed}.jsonl"
            csv_last = wf.write_window(*windows[-1], workdir / f"last_{seed}.csv")
            cfg = factory(seed, csv_last)
            cfg.update({"bank_path": str(bank_file),
                        "bank_min_fills": args.min_fills})
            from colony.config import validate
            validate(cfg)
            wf.run_window(cfg, workdir / f"evolve_{seed}.db")
            results = bank.certify(bank_file, h_path, cfg["venue"], spec["lot"])
            pooled = sum(bank.PROBE_CAPITAL_U + pnl for _, _, pnl in results)
            initial = len(results) * bank.PROBE_CAPITAL_U
            bench = len(results) * benchmark.buy_and_hold(
                h_closes, bank.PROBE_CAPITAL_U, cfg["venue"], spec["lot"])
            spx_cash, _, cov = spx_over(h_times[0], h_times[-1], initial,
                                        base_venue()) if results else (0, 0, 0)
            beat = bool(results) and cov > 0 and pooled > spx_cash
            wins += beat
            tests += 1
            lines.append(f"seed {seed}: certified {sum(r[1] == 'certify' for r in results)}"
                         f"/{len(results)} | pooled {pooled} u vs tape B&H {bench} u"
                         f" vs SPX {spx_cash} u -> {'BEATS-SPX' if beat else 'NO'}")
            if results:
                entries.append((f"holdout seed {seed}", initial, pooled, bench,
                                h_times[0], h_times[-1]))
                lines.append("  " + spx_line(f"holdout seed {seed}", h_times[0],
                                             h_times[-1], initial, pooled,
                                             base_venue()))
        except Exception as exc:
            machinery_ok = False
            lines.append(f"seed {seed}: FAIL — machinery: {exc}")
        print(lines[-1], flush=True)
    if machinery_ok:
        headline = (f"HOLDOUT {name}: "
                    + ("BEATS-SPX" if wins * 2 > tests else "NO-EDGE")
                    + f" ({wins}/{tests} seeds beat SPY on the holdout)")
    else:
        headline = f"HOLDOUT {name}: MACHINERY FAIL"
    lines += ["", headline]
    record.section("results", "\n".join(lines))
    record.set_replay_terms(h_times[0], h_times[-1], entries)
    record.finish(headline, level="INFO" if machinery_ok else "CRITICAL")
    guard.write_text(headline + "\n", encoding="utf-8")
    print(headline)
    return 0 if machinery_ok else 1


def fan_out(args, cells, seeds):
    procs = []
    for name in cells:
        for seed in seeds:
            cmd = [sys.executable, "-m", "experiments.frequency_grid",
                   "--cells", name, "--seeds", str(seed),
                   "--workdir", args.workdir, "--cost", args.cost,
                   "--min-fills", str(args.min_fills)]
            procs.append((name, seed, subprocess.Popen(cmd)))
    failed = 0
    for name, seed, proc in procs:
        code = proc.wait()
        print(f"[driver] cell {name} seed {seed} exited {code}", flush=True)
        failed += bool(code)
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="frequency x asset grid")
    parser.add_argument("--cells", default=None,
                        help=f"comma list from {list(CELLS)} (default: all)")
    parser.add_argument("--seeds", default=None)
    parser.add_argument("--workdir", default="work_grid")
    parser.add_argument("--cost", choices=tuple(COSTS), default="base")
    parser.add_argument("--min-fills", type=int, default=20)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--summarize", action="store_true")
    parser.add_argument("--holdout", default=None, metavar="CELL")
    args = parser.parse_args(argv)
    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else SEEDS
    Path(args.workdir).mkdir(parents=True, exist_ok=True)
    if args.summarize:
        return summarize(args)
    if args.holdout:
        return holdout_shot(args, seeds)
    cells = args.cells.split(",") if args.cells else list(CELLS)
    unknown = [c for c in cells if c not in CELLS]
    if unknown:
        raise SystemExit(f"unknown cells {unknown}; known: {list(CELLS)}")
    if args.parallel and len(cells) * len(seeds) > 1:
        return fan_out(args, cells, seeds)
    ok = True
    for name in cells:
        ok &= run_cell(name, args, seeds)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""The v3 flagship: walk-forward (spec v3 7.1) — are profitmakers real or
overfit?

Split a digest-pinned tape into K contiguous windows. For k = 1..K-1:
evolve a fresh colony on window k (bank snapshot = everything certified from
windows < k), terminal audit auto-admits the realized top (4.3), then every
candidate is certified by frozen solo probe on window k+1 (4.4 — the
postdating refusal makes leakage a machinery FAIL). Per seed the verdict is
EDGE if the certified champions' pooled out-of-sample cash beats
buy-and-hold on a majority of test windows, else NO-EDGE — both are
machinery-passing outcomes; the measurement is the deliverable.

Usage: python -m experiments.walk_forward [--csv data/spy_d.csv]
       [--windows 4] [--seeds 42,7,2026] [--workdir DIR]
       [--profile second|minute|hourly|daily] (spec v4 3.2: the registry)
       [--digest HEX] [--min-fills N] [--parallel]
"""

import argparse
import datetime
import subprocess
import sys
import tempfile
from pathlib import Path

from colony import bank, benchmark, db, ledger, orchestrator
from colony.arenas.replay import read_rows
from colony.config import validate
from colony.records import Record
from experiments.minute_ladder import tape_digest
from experiments.profiles import PROFILES, daily_config  # noqa: F401 (re-export
# for bank_reuse; daily/minute factories are byte-identical to their v3 forms)

ROOT = Path(__file__).resolve().parent.parent
SEEDS = [42, 7, 2026]


def write_window(times, closes, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("Date,Close\n")
        for t, c in zip(times, closes):
            stamp = datetime.datetime.fromtimestamp(
                t, datetime.timezone.utc).isoformat().replace("+00:00", "")
            f.write(f"{stamp},{c}\n")
    return path


def split_windows(times, closes, k):
    """K contiguous, non-overlapping windows covering the whole tape."""
    n = len(closes)
    size = n // k
    if size < 2:
        raise SystemExit(f"tape has {n} rows: too few for {k} windows")
    bounds = [(i * size, (i + 1) * size if i < k - 1 else n) for i in range(k)]
    return [(times[a:b], closes[a:b]) for a, b in bounds]


def run_window(cfg, db_path):
    """Evolve one window to its terminal audit (resumable, like the ladders).
    wind_down auto-admits into cfg['bank_path'] (spec v3 4.3)."""
    con = db.connect(db_path)
    orch = orchestrator.Orchestrator(con) if db_path.exists() and con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='runs'").fetchone()[0] \
        else orchestrator.init_colony(con, cfg)
    orch.run(10 ** 9)
    already = con.execute("SELECT COUNT(*) FROM agents WHERE death_cause = 'horizon'"
                          ).fetchone()[0] > 0
    if not already:
        orch.wind_down()
    else:  # resumed past the audit: admission is idempotent (dedup by hash)
        bank.admit_from_db(con, cfg["bank_path"],
                           records_root=cfg.get("records_root", "records"))
    treasury = ledger.balance(con, "TREASURY")
    con.close()
    return treasury


def run_seed(seed, windows, workdir, args, lines, make_cfg=None):
    """One seed's full walk. Returns (verdict, footer entries). make_cfg
    lets the v4 grid inject a per-cell factory (lot/venue overrides)."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    bank_file = workdir / f"bank_{seed}.jsonl"
    make_cfg = make_cfg or PROFILES[args.profile]
    entries, wins, tests = [], 0, 0
    for k in range(1, len(windows)):
        w_times, w_closes = windows[k - 1]
        csv_k = write_window(*windows[k - 1], workdir / f"window_{k}.csv")
        cfg = make_cfg(seed, csv_k)
        cfg.update({"bank_path": str(bank_file), "bank_min_fills": args.min_fills})
        validate(cfg)
        candidates_before = sum(
            1 for e in bank.fold(bank_file).values() if e["status"] == "candidate")
        treasury = run_window(cfg, workdir / f"wf_{seed}_w{k}.db")
        state = bank.fold(bank_file)
        admitted = sum(1 for e in state.values()
                       if e["status"] == "candidate") - candidates_before
        # certify every candidate out-of-sample on window k+1 (spec v3 4.4)
        n_times, n_closes = windows[k]
        csv_next = write_window(n_times, n_closes, workdir / f"window_{k + 1}.csv")
        results = bank.certify(bank_file, csv_next, cfg["venue"],
                               cfg["arena"].get("lot_denominator", 1))
        certified = [r for r in results if r[1] == "certify"]
        pooled = sum(bank.PROBE_CAPITAL_U + pnl for _, _, pnl in results)
        bench_one = benchmark.buy_and_hold(
            n_closes, bank.PROBE_CAPITAL_U, cfg["venue"],
            cfg["arena"].get("lot_denominator", 1))
        bench = len(results) * bench_one
        tests += 1
        beat = bool(results) and pooled > bench
        wins += beat
        lines.append(
            f"  window {k} -> test {k + 1}: admitted {admitted},"
            f" certified {len(certified)}/{len(results)}"
            f" | evolve treasury {treasury} u"
            f" | oos pooled {pooled} u vs B&H {bench} u"
            f" ({'beat' if beat else 'did not beat'})")
        if results:
            entries.append((f"seed {seed} oos window {k + 1}",
                            len(results) * bank.PROBE_CAPITAL_U, pooled, bench,
                            n_times[0], n_times[-1]))
    verdict = "EDGE" if wins * 2 > tests else "NO-EDGE"
    lines.append(f"  seed {seed}: {verdict} ({wins}/{tests} test windows beaten)")
    return verdict, entries


def fan_out(args, seeds):
    procs = []
    for seed in seeds:
        cmd = [sys.executable, "-m", "experiments.walk_forward",
               "--csv", str(args.csv), "--windows", str(args.windows),
               "--seeds", str(seed), "--profile", args.profile,
               "--min-fills", str(args.min_fills)]
        if args.workdir:
            cmd += ["--workdir", args.workdir]
        if args.digest:
            cmd += ["--digest", args.digest]
        procs.append((seed, subprocess.Popen(cmd)))
    failed = sum(bool(p.wait()) for _, p in procs)
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="walk-forward certification")
    parser.add_argument("--csv", default=ROOT / "data" / "spy_d.csv")
    parser.add_argument("--windows", type=int, default=4)
    parser.add_argument("--seeds", default=None)
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="daily")
    parser.add_argument("--digest", default=None)
    parser.add_argument("--min-fills", type=int, default=20)
    parser.add_argument("--parallel", action="store_true")
    args = parser.parse_args(argv)
    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else SEEDS
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"missing {csv_path}", file=sys.stderr)
        return 2
    digest = tape_digest(csv_path)
    if args.digest and digest != args.digest:
        print(f"tape digest {digest} != pinned {args.digest} — refusing", file=sys.stderr)
        return 2
    if args.parallel and len(seeds) > 1:
        return fan_out(args, seeds)

    name = "walk_forward" + ("_" + args.seeds.replace(",", "_") if args.seeds else "")
    record = Record("records", "experiments", name,
                    config={"csv": str(csv_path), "digest": digest,
                            "windows": args.windows, "seeds": seeds,
                            "profile": args.profile}, seed=seeds)
    times, closes = read_rows(csv_path)
    windows = split_windows(times, closes, args.windows)
    lines = [f"walk-forward: {csv_path.name} (digest {digest}),"
             f" {args.windows} windows, seeds {seeds}", ""]
    entries, verdicts, machinery_ok = [], [], True
    for seed in seeds:
        lines.append(f"seed {seed}:")
        try:
            if args.workdir:
                Path(args.workdir).mkdir(parents=True, exist_ok=True)
                verdict, seed_entries = run_seed(seed, windows, args.workdir, args, lines)
            else:
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    verdict, seed_entries = run_seed(seed, windows, tmp, args, lines)
        except Exception as exc:  # incl. BankError leakage refusal: machinery
            machinery_ok = False
            verdict, seed_entries = "FAIL", []
            lines.append(f"  seed {seed}: FAIL — machinery: {exc}")
        verdicts.append((seed, verdict))
        entries.extend(seed_entries)
        print(lines[-1], flush=True)
        lines.append("")
    detail = ", ".join(f"seed {s}: {v}" for s, v in verdicts)
    headline = (f"WALK-FORWARD {detail}" if machinery_ok
                else f"WALK-FORWARD MACHINERY FAIL — {detail}")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[-1], entries)
    record.finish(headline, level="INFO" if machinery_ok else "CRITICAL")
    return 0 if machinery_ok else 1


if __name__ == "__main__":
    sys.exit(main())

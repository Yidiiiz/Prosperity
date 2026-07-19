"""The v2 flagship experiment: the minute-bar ladder (spec v2 9.2).

Replays Binance BTCUSDT 1-minute history (fetch first:
`python tools/fetch_binance_klines.py BTCUSDT 1m --days 365 -o data/btcusdt_1m.csv`
— the printed close-series digest pins the tape) down a capitalization
ladder with the venue's spread ON and fill delay 1. Rungs:

  full  -- $200,000 virtual (100 agents seeded $1,000, lot = 1/100,000 BTC)
  small -- $1,000 TOTAL (10 agents seeded $50, lot = 1/1,000,000 BTC)
  dust  -- $10 TOTAL (4 agents seeded $2.50, small_stakes waiver)

Every rung ends with a TERMINAL AUDIT (#29): every estate liquidated at the
last real price, so the verdict is audited CASH vs initial AND vs
buy-and-hold on the same tape at the same venue costs (spec v3 section 2).
Verdict per seed is a v3 2.4 tier: ALPHA (beat buy-and-hold), CASH (real
profit, no edge over holding), EXPECTED-FAIL (machinery sound, economics
negative, per-seed numbers recorded), or FAIL (the machinery itself broke:
crash, invariant, incomplete replay). Machinery failures are the only
failures.

Usage: python -m experiments.minute_ladder [--rung full|small|dust|all]
       [--seeds 42,7,2026] [--workdir DIR] [--csv PATH] [--digest HEX]
       [--parallel]
Each seed prints the moment it finishes; --workdir makes runs resumable;
--parallel fans seeds out as subprocesses (spec v2 9.1).
"""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from colony import benchmark, db, ledger, orchestrator, report
from colony.arenas.replay import read_rows
from colony.config import validate
from colony.records import Record

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data" / "btcusdt_1m.csv"
SEEDS = [42, 7, 2026]

MINUTE_LIFECYCLE = {
    "max_age_days": 30,
    "stagnation_hours": 48,          # > max lookback (100 minutes), by far
    "breed_cooldown_hours": 12,
    "solo_breed_patience_hours": 4,
    "snapshot_every_hours": 6,
    "checkpoint_every_days": 1,
}


def tape_digest(csv_path):
    closes = []
    with open(csv_path, encoding="utf-8") as f:
        next(f)
        for line in f:
            if line.strip():
                closes.append(line.rsplit(",", 1)[1].strip())
    return hashlib.sha256(",".join(closes).encode()).hexdigest()[:16]


def base_config(seed, csv_path):
    with open(ROOT / "config.spy.json", encoding="utf-8") as f:
        cfg = json.load(f)  # venue spread on, fill delay 1, repro 1.08
    cfg["rng_seed"] = seed
    cfg["arena"] = {"kind": "replay", "name": "btc_1m", "csv": str(csv_path),
                    "lot_denominator": 100_000, "tick_seconds": 60}
    cfg["lifecycle"] = dict(MINUTE_LIFECYCLE)
    cfg["min_ticks_for_fitness"] = 300
    return cfg


def full_config(seed, csv_path):
    cfg = base_config(seed, csv_path)
    validate(cfg)
    return cfg


def small_config(seed, csv_path):
    cfg = base_config(seed, csv_path)
    cfg.update({
        "initial_treasury_u": 1_000_000_000,  # $1,000 TOTAL
        "gen0_population": 10,
        "gen0_seed_u": 50_000_000,            # $50 per agent
        "max_population": 40,
        "population_floor": 8,
        "death_floor_u": 5_000_000,
        "reserve_floor_u": 7_500_000,
        "rent_min_u": 0,
        "elitism_top_k": 2,
    })
    cfg["arena"]["lot_denominator"] = 1_000_000
    validate(cfg)
    return cfg


def dust_config(seed, csv_path):
    cfg = base_config(seed, csv_path)
    cfg.update({
        "initial_treasury_u": 10_000_000,     # $10 TOTAL
        "gen0_population": 4,
        "gen0_seed_u": 2_500_000,             # $2.50 per agent
        "max_population": 20,
        "population_floor": 4,
        "death_floor_u": 500_000,
        "reserve_floor_u": 500_000,
        "rent_min_u": 0,
        "elitism_top_k": 2,
        "small_stakes": True,                 # the integer floor dominates here
    })
    cfg["arena"]["lot_denominator"] = 10_000_000
    validate(cfg)
    return cfg


RUNGS = {"full": full_config, "small": small_config, "dust": dust_config}


def money(u):
    return f"${u / 1e6:,.2f}"


def run_seed(cfg, workdir, label):
    """One seed, resumable via a persistent workdir, ending in the terminal
    audit. Returns per-seed economics + a machinery verdict."""
    path = Path(workdir) / f"ladder_{label}_{cfg['rng_seed']}.db"
    con = db.connect(path)
    orch = orchestrator.Orchestrator(con) if path.exists() and con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='runs'").fetchone()[0] \
        else orchestrator.init_colony(con, cfg)
    orch.run(10 ** 9)  # to the end of the tape (resume-safe)
    ticks = orch.tick
    pop = len(orch.agents)
    births, deaths = orch.births_cum, orch.deaths_cum
    already_audited = con.execute(
        "SELECT COUNT(*) FROM agents WHERE death_cause = 'horizon'"
    ).fetchone()[0] > 0
    if not already_audited:
        orch.wind_down()  # terminal audit (#29): verifies invariants
    treasury = ledger.balance(con, "TREASURY")
    trades = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    fees = con.execute(
        "SELECT COALESCE(SUM(fee_u), 0) + COALESCE(SUM(spread_u), 0) FROM trades"
    ).fetchone()[0]
    top = report.profitmakers_text(con, top_k=5)  # spec v3 3.1
    con.close()
    return {
        "ticks": ticks, "pop": pop, "births": births, "deaths": deaths,
        "treasury_liq": treasury, "initial": cfg["initial_treasury_u"],
        "trades": trades, "friction": fees, "profitmakers": top,
    }


def run_rung(lines, label, seeds, csv_path, closes, entries, workdir):
    machinery_ok = True
    for seed in seeds:
        cfg = RUNGS[label](seed, csv_path)
        bench = benchmark.buy_and_hold(
            closes, cfg["initial_treasury_u"], cfg["venue"],
            cfg["arena"]["lot_denominator"],
        )
        try:
            if workdir:
                r = run_seed(cfg, workdir, label)
            else:
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    r = run_seed(cfg, tmp, label)
        except Exception as exc:  # machinery failure: the only real failure
            machinery_ok = False
            line = f"seed {seed}  [FAIL — machinery: {exc}]"
            print(line, flush=True)
            lines.append(line)
            continue
        v = benchmark.tier(r["initial"], r["treasury_liq"], bench)  # spec v3 2.4
        entries.append((f"{label} seed {seed}", r["initial"], r["treasury_liq"], bench))
        gain = (r["treasury_liq"] / r["initial"] - 1) * 100
        block = [
            f"seed {seed}  [{v}]",
            f"  {r['ticks']} minute bars | pop {r['pop']} | births {r['births']}"
            f" deaths {r['deaths']} | {r['trades']} fills"
            f" | friction paid {money(r['friction'])}",
            f"  terminal audit: treasury {money(r['treasury_liq'])} cash"
            f" vs {money(r['initial'])} initial  ({gain:+.2f}%)"
            f" | buy-and-hold same tape {money(bench)}",
            "  " + r["profitmakers"].replace("\n", "\n  "),
            "",
        ]
        print("\n".join(block), flush=True)  # per-seed, the moment it finishes
        lines.extend(block)
    return machinery_ok


def fan_out(args, rungs, seeds):
    """Thin parallel driver (spec v2 9.1): one subprocess per (rung, seed),
    each writing its own record; output streams as children finish."""
    procs = []
    for label in rungs:
        for seed in seeds:
            cmd = [sys.executable, "-m", "experiments.minute_ladder",
                   "--rung", label, "--seeds", str(seed), "--csv", str(args.csv)]
            if args.workdir:
                cmd += ["--workdir", args.workdir]
            if args.digest:
                cmd += ["--digest", args.digest]
            procs.append((label, seed, subprocess.Popen(cmd)))
    failed = 0
    for label, seed, proc in procs:
        code = proc.wait()
        print(f"[driver] rung {label} seed {seed} exited {code}", flush=True)
        failed += bool(code)
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="minute-bar capitalization ladder")
    parser.add_argument("--rung", choices=[*RUNGS, "all"], default="all")
    parser.add_argument("--seeds", default=None,
                        help=f"comma-separated RNG seeds (default {SEEDS})")
    parser.add_argument("--workdir", default=None,
                        help="persistent scratch dir; interrupted runs resume")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="1m Date,Close tape")
    parser.add_argument("--digest", default=None,
                        help="expected close-series digest (refuse a changed tape)")
    parser.add_argument("--parallel", action="store_true",
                        help="fan seeds out as subprocesses")
    args = parser.parse_args(argv)
    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else SEEDS
    rungs = list(RUNGS) if args.rung == "all" else [args.rung]
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"missing {csv_path}; run: python tools/fetch_binance_klines.py"
              " BTCUSDT 1m --days 365 -o data/btcusdt_1m.csv", file=sys.stderr)
        return 2
    digest = tape_digest(csv_path)
    if args.digest and digest != args.digest:
        print(f"tape digest {digest} != pinned {args.digest} — refusing to run"
              " on a changed tape", file=sys.stderr)
        return 2
    if args.parallel and (len(seeds) > 1 or len(rungs) > 1):
        return fan_out(args, rungs, seeds)

    name = "minute_ladder" if args.rung == "all" else f"minute_ladder_{args.rung}"
    if args.seeds:
        name += "_" + args.seeds.replace(",", "_")
    record = Record("records", "experiments", name,
                    config={"csv": str(csv_path), "digest": digest,
                            "seeds": seeds, "rungs": rungs}, seed=seeds)
    times, closes = read_rows(csv_path)
    lines = [f"minute-bar ladder: {csv_path.name} (digest {digest}), seeds {seeds}", ""]
    machinery_ok = True
    entries = []
    for label in rungs:
        header = f"== rung: {label} =="
        print(header, flush=True)
        lines += [header, ""]
        machinery_ok &= run_rung(lines, label, seeds, csv_path, closes, entries,
                                 args.workdir)
    headline = ("LADDER MACHINERY OK — v3 tiers recorded per seed (v2 7.4, v3 2.4)"
                if machinery_ok else "LADDER MACHINERY FAIL")
    lines.append(headline)
    print(headline)
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[-1], entries)
    record.finish(headline, level="INFO" if machinery_ok else "CRITICAL")
    return 0 if machinery_ok else 1


if __name__ == "__main__":
    sys.exit(main())

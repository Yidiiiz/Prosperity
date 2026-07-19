"""The reuse A/B (spec v3 7.2): does banking champions actually help?

On a HELD-OUT tape window — one that no banked genome's provenance touches,
enforced before anything runs — seed-matched pairs of colonies race:
  A: random gen-0 (bank_immigrant_share_bps 0)
  B: bank-enabled (share 5,000, the walk-forward's certified bank)
Recorded per pair: audited cash, CAGR, benchmark delta, and time to first
treasury surplus. Direction expected: B >= A. Direction demanded: none —
the measurement is the deliverable, and a B < A result is recorded with the
same prominence (spec v3 10.3).

Usage: python -m experiments.bank_reuse --bank BANK.jsonl --csv TAPE
       [--from DATE] [--seeds 42,7,2026] [--workdir DIR]
       [--profile daily|minute]
"""

import argparse
import datetime
import json
import sys
import tempfile
from pathlib import Path

from colony import bank, benchmark, db, ledger, orchestrator
from colony.arenas.replay import parse_utc, read_rows
from colony.config import validate
from colony.records import Record
from experiments.minute_ladder import base_config as minute_config, tape_digest
from experiments.walk_forward import daily_config, write_window

ROOT = Path(__file__).resolve().parent.parent
SEEDS = [42, 7, 2026]


def check_held_out(bank_file, start_utc, end_utc):
    """Refuse any overlap between the A/B window and ANY banked genome's
    provenance (admission or probe windows) — held-out means held out."""
    for h, entry in sorted(bank.fold(bank_file).items()):
        windows = []
        if "admit" in entry:
            windows.append(entry["admit"]["source"]["window"])
        for kind in ("certify", "lapse"):
            if kind in entry and "probe" in entry[kind]:
                windows.append(entry[kind]["probe"]["window"])
        for w_start, w_end in windows:
            if not (end_utc < parse_utc(w_start) or start_utc > parse_utc(w_end)):
                raise SystemExit(
                    f"held-out violation: {h[:12]} provenance {w_start[:10]}.."
                    f"{w_end[:10]} touches the A/B window — pick a window no"
                    " banked genome has seen (spec v3 7.2)")


def run_arm(cfg, db_path):
    con = db.connect(db_path)
    orch = orchestrator.Orchestrator(con) if db_path.exists() and con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='runs'").fetchone()[0] \
        else orchestrator.init_colony(con, cfg)
    orch.run(10 ** 9)
    if not con.execute("SELECT COUNT(*) FROM agents WHERE death_cause = 'horizon'"
                       ).fetchone()[0]:
        orch.wind_down()
    treasury = ledger.balance(con, "TREASURY")
    surplus = con.execute(
        "SELECT MIN(tick) FROM colony_metrics WHERE treasury_u > ?",
        (cfg["initial_treasury_u"],)).fetchone()[0]
    n_bank = con.execute("SELECT COUNT(*) FROM agents WHERE origin LIKE 'bank:%'"
                         ).fetchone()[0]
    con.close()
    return {"treasury": treasury, "initial": cfg["initial_treasury_u"],
            "surplus_tick": surplus, "bank_agents": n_bank}


def main(argv=None):
    parser = argparse.ArgumentParser(description="bank reuse A/B")
    parser.add_argument("--bank", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--from", dest="from_date", default=None,
                        help="start the held-out window at this UTC date")
    parser.add_argument("--seeds", default=None)
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--profile", choices=("daily", "minute"), default="daily")
    args = parser.parse_args(argv)
    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else SEEDS
    csv_path = Path(args.csv)
    if not csv_path.exists() or not Path(args.bank).exists():
        print(f"missing {csv_path} or {args.bank}", file=sys.stderr)
        return 2
    times, closes = read_rows(csv_path)
    if args.from_date:
        start = parse_utc(args.from_date)
        keep = [i for i, t in enumerate(times) if t >= start]
        times = [times[i] for i in keep]
        closes = [closes[i] for i in keep]
    if len(times) < 2:
        print("held-out window has fewer than 2 bars", file=sys.stderr)
        return 2
    check_held_out(args.bank, times[0], times[-1])  # enforced up front

    certified = sum(1 for e in bank.fold(args.bank).values()
                    if e["status"] == "certified")
    name = "bank_reuse" + ("_" + args.seeds.replace(",", "_") if args.seeds else "")
    record = Record("records", "experiments", name,
                    config={"csv": str(csv_path), "digest": tape_digest(csv_path),
                            "bank": str(args.bank), "certified": certified,
                            "from": args.from_date, "seeds": seeds}, seed=seeds)
    lines = [f"bank reuse A/B: {csv_path.name}, held-out"
             f" {datetime.datetime.fromtimestamp(times[0], datetime.timezone.utc):%Y-%m-%d}"
             f"..{datetime.datetime.fromtimestamp(times[-1], datetime.timezone.utc):%Y-%m-%d},"
             f" bank {args.bank} ({certified} certified), seeds {seeds}", ""]
    entries, machinery_ok = [], True

    def pair(seed, workdir):
        workdir = Path(workdir)
        tape = write_window(times, closes, workdir / "held_out.csv")
        make_cfg = minute_config if args.profile == "minute" else daily_config
        out = {}
        for arm, overrides in (("A", {"bank_immigrant_share_bps": 0}),
                               ("B", {"bank_immigrant_share_bps": 5_000,
                                      "bank_path": str(args.bank)})):
            cfg = make_cfg(seed, tape)
            cfg.update(overrides)
            cfg["bank_admit_top_k"] = 1  # keep the A/B from feeding the bank:
            cfg["bank_min_fills"] = 10 ** 9  # admission bar unreachable
            validate(cfg)
            out[arm] = run_arm(cfg, workdir / f"reuse_{arm}_{seed}.db")
            out[arm]["bench"] = benchmark.buy_and_hold(
                closes, cfg["initial_treasury_u"], cfg["venue"],
                cfg["arena"].get("lot_denominator", 1))
        return out

    for seed in seeds:
        try:
            if args.workdir:
                Path(args.workdir).mkdir(parents=True, exist_ok=True)
                out = pair(seed, args.workdir)
            else:
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    out = pair(seed, tmp)
        except Exception as exc:
            machinery_ok = False
            lines.append(f"seed {seed}  [FAIL — machinery: {exc}]")
            continue
        a, b = out["A"], out["B"]
        delta = b["treasury"] - a["treasury"]
        for arm, r in (("A random", a), ("B bank", b)):
            entries.append((f"seed {seed} {arm}", r["initial"], r["treasury"],
                            r["bench"]))
        lines.append(
            f"seed {seed}: A {a['treasury']} u vs B {b['treasury']} u"
            f" (B-A {delta:+} u, {'B >= A' if delta >= 0 else 'B < A'})"
            f" | bank agents in B: {b['bank_agents']}"
            f" | first surplus tick A {a['surplus_tick']} B {b['surplus_tick']}")
        print(lines[-1], flush=True)

    headline = ("REUSE A/B measured — direction reported, not demanded"
                if machinery_ok else "REUSE A/B MACHINERY FAIL")
    lines += ["", headline]
    record.section("results", "\n".join(lines))
    record.set_replay_terms(times[0], times[-1], entries)
    record.finish(headline, level="INFO" if machinery_ok else "CRITICAL")
    print(headline)
    return 0 if machinery_ok else 1


if __name__ == "__main__":
    sys.exit(main())

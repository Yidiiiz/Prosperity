"""CLI entry point: python -m colony <command>."""

import argparse
import json
import os
import subprocess
import sys

from . import db, ledger, orchestrator, report, records
from .config import ConfigError, load_config

RECORDS_ROOT = "records"


def cmd_init(args):
    if os.path.exists(args.db):
        print(f"refusing to init: {args.db} already exists (no silent resets)", file=sys.stderr)
        return 1
    cfg = load_config(args.config)
    con = db.connect(args.db)
    orch = orchestrator.init_colony(con, cfg)
    print(f"initialized {args.db}: {len(orch.agents)} gen-0 agents seeded from treasury")
    print(f"treasury {report.money(ledger.balance(con, 'TREASURY'))}")
    return 0


def _open(args):
    if not os.path.exists(args.db):
        print(f"no database at {args.db} — run `colony init` first", file=sys.stderr)
        raise SystemExit(1)
    return db.connect(args.db)


def cmd_run(args):
    con = _open(args)
    cfg = load_config(args.config) if args.config else None
    orch = orchestrator.Orchestrator(con, cfg)
    record = records.Record(
        RECORDS_ROOT, "runs", f"run_{orch.run_id}",
        config=orch.cfg, seed=orch.cfg["rng_seed"],
        extra_header=f"resume_from_tick: {orch.tick} | ticks_requested: {args.ticks}",
    )

    def checkpoint(tick):
        record.section(f"checkpoint @ tick {tick}", report.summary_text(con))

    interrupted = False
    executed = 0
    try:
        executed = orch.run(args.ticks, checkpoint_cb=checkpoint)
    except KeyboardInterrupt:
        interrupted = True
    ledger.verify_invariants(con, orch.cfg["initial_treasury_u"])
    summary = report.summary_text(con)
    record.section("final state", summary)
    metrics = report.latest_metrics(con)
    treasury = metrics["treasury_u"] if metrics else 0
    if orch.cfg["arena"].get("kind") == "replay" and orch.tick > 0:
        # spec v3 2.2: every replay record states span, wall, CAGR, benchmark.
        # Mid-run the colony figure is marked-to-market, not audited — labelled.
        from . import benchmark
        from .arenas.replay import read_rows

        times, closes = read_rows(orch.cfg["arena"]["csv"])
        initial = orch.cfg["initial_treasury_u"]
        marked = treasury + (metrics["colony_wealth_u"] if metrics else 0)
        bench = benchmark.buy_and_hold(
            closes[: orch.tick + 1], initial, orch.cfg["venue"],
            orch.cfg["arena"].get("lot_denominator", 1),
        )
        record.set_replay_terms(
            times[0], times[orch.tick],
            [("system total (marked)", initial, marked, bench)],
        )
    if interrupted:
        status = "INTERRUPTED"
    elif executed < args.ticks:
        status = f"completed (arena out of data after {executed} ticks)"
    else:
        status = "completed"
    record.finish(
        f"{status} @ tick {orch.tick} | population {len(orch.agents)}"
        f" | treasury {report.money(treasury)} | invariants OK"
    )
    print(summary)
    if interrupted:
        print(f"\ninterrupted at tick {orch.tick}; state saved, run again to continue")
    elif executed < args.ticks:
        print(f"\narena out of data after {executed} ticks"
              " (history fully replayed, or the live feed went stale)")
    return 0


def cmd_report(args):
    con = _open(args)
    print(report.summary_text(con, last_n=args.last))
    return 0


def cmd_tree(args):
    con = _open(args)
    print(report.tree_dot(con))
    return 0


def cmd_inspect(args):
    con = _open(args)
    print(report.inspect_text(con, args.agent_id))
    return 0


def cmd_verify(args):
    con = _open(args)
    cfg = json.loads(
        con.execute("SELECT config_json FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    )
    try:
        ledger.verify_invariants(con, cfg["initial_treasury_u"])
    except ledger.AccountingError as exc:
        print(f"INVARIANT VIOLATION: {exc}", file=sys.stderr)
        return 1
    total = con.execute("SELECT SUM(balance_u) FROM balances").fetchone()[0]
    print(f"invariants OK: all balances match the ledger;"
          f" system total {report.money(total)} == initial capitalization")
    return 0


def cmd_serve(args):
    from . import server  # imported lazily; the sim core never needs http

    server.serve(args.db, args.port)
    return 0


def cmd_daemon(args):
    from . import audit as audit_mod
    from . import daemon as daemon_mod

    if args.action == "status":
        return daemon_mod.status(args.port)
    if args.action == "clear-audit":
        audit_mod.clear_critical(RECORDS_ROOT)
        print("audit CRITICAL flag cleared")
        return 0
    cfg = load_config(args.config)
    d = daemon_mod.Daemon(args.db, cfg, records_root=RECORDS_ROOT, port=args.port)
    print(f"colony daemon starting (db {args.db}, journal"
          f" {cfg['arena']['journal']}); Ctrl-C or SIGTERM to stop")
    ticks = d.run(max_ticks=args.max_ticks)
    print(f"daemon stopped cleanly after {ticks} ticks")
    return 0


def cmd_audit(args):
    from . import audit as audit_mod

    ok, detail = audit_mod.audit(args.db, records_root=args.records)
    print(detail)
    return 0 if ok else 1


def cmd_bank(args):
    from . import bank as bank_mod

    if args.action == "admit":
        con = _open(args)
        admitted = bank_mod.admit_from_db(con, args.bank)
        for aid, h, bps in admitted:
            print(f"admitted {h[:12]} (agent {aid}, {bps:+.2f} bps/day in-sample)")
        print(f"{len(admitted)} candidate(s) admitted -> {args.bank}")
        return 0
    if args.action == "certify":
        if not args.tape:
            print("certify needs --tape CSV", file=sys.stderr)
            return 2
        cfg = load_config(args.config)
        results = bank_mod.certify(
            args.bank, args.tape, cfg["venue"],
            cfg["arena"].get("lot_denominator", 1),
            from_date=args.from_date, recertify=args.recertify,
        )
        for h, verdict, pnl in results:
            print(f"{verdict:<8} {h[:12]} probe pnl {report.money(pnl)}")
        print(f"{len(results)} probe(s) run against {args.tape}")
        return 0
    state = bank_mod.fold(args.bank)
    if args.action == "show":
        matches = [h for h in state if h.startswith(args.arg or "")]
        if not args.arg or len(matches) != 1:
            print(f"need a unique hash prefix ({len(matches)} match(es))", file=sys.stderr)
            return 1
        print(json.dumps(state[matches[0]], indent=2, sort_keys=True))
        return 0
    if not state:  # list
        print(f"bank {args.bank} is empty")
        return 0
    for h, entry in sorted(state.items(), key=lambda kv: kv[1]["status"] + kv[0]):
        admit = entry.get("admit", {})
        probe = entry.get("certify") or entry.get("lapse")
        ins = admit.get("audited", {}).get("realized_bps_per_day", 0.0)
        outs = (f"{probe['audited']['realized_bps_per_day']:+8.2f}" if probe
                else "       -")
        src = admit.get("source", {})
        window = "..".join(w[:10] for w in src.get("window", ["?", "?"]))
        print(f"{h[:12]}  {entry['status']:<9} in {ins:+8.2f} out {outs} bps/day"
              f"  {src.get('arena', '?')} {window} seed {src.get('config_seed', '?')}"
              f" agent {src.get('agent_id', '?')}")
    return 0


def cmd_test(args):
    record = records.Record(RECORDS_ROOT, "tests", "pytest")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-v"], capture_output=True, text=True
    )
    record.append(proc.stdout)
    if proc.stderr:
        record.section("stderr", proc.stderr)
    verdict = "PASS" if proc.returncode == 0 else f"FAIL (exit {proc.returncode})"
    record.finish(f"pytest {verdict}")
    print(proc.stdout[-4000:])
    print(f"full output -> {record.path}")
    return proc.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="colony", description="darwin-wallet: evolutionary colony simulation"
    )
    parser.add_argument("--db", default="colony.db", help="path to the colony database")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create db, accounts, gen-0 agents")
    p.add_argument("--config", default="config.default.json")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("run", help="run/continue the simulation")
    p.add_argument("--ticks", type=int, required=True)
    p.add_argument("--config", default=None,
                   help="override the stored config (breaks reproducibility; default: stored)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("report", help="colony summary report")
    p.add_argument("--last", type=int, default=None, help="restrict flux stats to last N ticks")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("tree", help="family tree as Graphviz DOT on stdout")
    p.set_defaults(fn=cmd_tree)

    p = sub.add_parser("inspect", help="genome, P&L, trades, fitness of one agent")
    p.add_argument("agent_id")
    p.set_defaults(fn=cmd_inspect)

    p = sub.add_parser("verify", help="verify all ledger invariants")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("serve", help="read-only Observatory dashboard")
    p.add_argument("--port", type=int, default=8477)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("daemon", help="always-on colony: supervise feed, tick, audit")
    p.add_argument("action", nargs="?", default="run",
                   choices=("run", "status", "clear-audit"))
    p.add_argument("--config", default="config.live.json")
    p.add_argument("--port", type=int, default=8477)
    p.add_argument("--max-ticks", type=int, default=None,
                   help="stop after N ticks (soaks/tests; default: run forever)")
    p.set_defaults(fn=cmd_daemon)

    p = sub.add_parser("bank", help="genome bank: list, show, admit, certify")
    p.add_argument("action", choices=("list", "show", "admit", "certify"))
    p.add_argument("arg", nargs="?", help="hash prefix for `show`")
    p.add_argument("--bank", default="bank/bank.jsonl", help="bank event log path")
    p.add_argument("--tape", default=None, help="certify: out-of-sample Date,Close CSV")
    p.add_argument("--from", dest="from_date", default=None,
                   help="certify: probe only bars at or after this UTC date")
    p.add_argument("--config", default="config.spy.json",
                   help="certify: config supplying the venue + lot size")
    p.add_argument("--recertify", action="store_true",
                   help="certify: re-probe certified genomes too (failures lapse)")
    p.set_defaults(fn=cmd_bank)

    p = sub.add_parser("audit", help="replay-twin audit of closed journal segments")
    p.add_argument("--records", default=RECORDS_ROOT)
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("test", help="run pytest, tee output into records/tests/")
    p.set_defaults(fn=cmd_test)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

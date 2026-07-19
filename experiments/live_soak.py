"""The 24-hour live soak (spec v2 9.3, acceptance 11.2).

Orchestrates the whole acceptance scenario as subprocesses:
  1. starts `colony daemon` against the live 1-second feed (paper only)
  2. at a random point, hard-kills it (proc.kill(), no warning, no cleanup)
  3. restarts it and verifies the colony resumed and kept ticking
  4. at the end, stops it cleanly, runs the replay-twin audit, and collects
     the evidence — health, gaps, audit verdicts, kill/resume timeline —
     into one record.

Zero invariant violations is enforced by the daemon itself (verify_fast
every tick crashes the process on drift); the audit proves byte-identical
history; gaps are counted, matched against the feed log, never excused.

Usage: python -m experiments.live_soak [--hours 24] [--config config.live.json]
       [--db soak.db] [--port 8477] [--kill-after-s N]
A short smoke run: --hours 0.05 (3 minutes) with the feed running.
"""

import argparse
import datetime
import json
import random
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from colony import audit
from colony.records import Record

UTC = datetime.timezone.utc


def now():
    return datetime.datetime.now(UTC).isoformat(timespec="seconds")


def read_health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
            return json.load(r)
    except OSError:
        return None


def start_daemon(args):
    return subprocess.Popen(
        [sys.executable, "-m", "colony", "--db", args.db, "daemon",
         "--config", args.config, "--port", str(args.port)],
    )


def wait_for_tick(port, minimum, timeout_s, poll=2.0):
    """Block until /api/health reports tick >= minimum (or time out)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        health = read_health(port)
        if health and health.get("tick", 0) >= minimum:
            return health
        time.sleep(poll)
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="24h live soak orchestrator")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--config", default="config.live.json")
    parser.add_argument("--db", default="soak.db")
    parser.add_argument("--port", type=int, default=8477)
    parser.add_argument("--kill-after-s", type=int, default=None,
                        help="hard-kill moment (default: random inside the run)")
    args = parser.parse_args(argv)

    total_s = int(args.hours * 3600)
    kill_after = args.kill_after_s or random.randint(total_s // 4, 3 * total_s // 4)
    record = Record("records", "experiments", "live_soak",
                    config={"hours": args.hours, "config": args.config,
                            "kill_after_s": kill_after})
    timeline = [f"{now()} soak start ({args.hours}h, kill scheduled at +{kill_after}s)"]
    ok = True

    proc = start_daemon(args)
    health = wait_for_tick(args.port, 1, timeout_s=300)
    if health is None:
        record.section("timeline", "\n".join(timeline + ["daemon never ticked"]))
        record.finish("SOAK FAIL — daemon never reached tick 1", level="CRITICAL")
        proc.kill()
        return 1
    timeline.append(f"{now()} daemon ticking (tick {health['tick']})")

    time.sleep(kill_after)
    pre_kill = read_health(args.port) or {}
    proc.kill()  # the induced catastrophe: no warning, no cleanup
    proc.wait(timeout=60)
    timeline.append(f"{now()} HARD KILL at tick {pre_kill.get('tick', '?')}")
    Path(f"{Path(args.db).parent}/daemon.pid").unlink(missing_ok=True)

    proc = start_daemon(args)
    resumed = wait_for_tick(args.port, pre_kill.get("tick", 0) + 1, timeout_s=300)
    if resumed is None:
        ok = False
        timeline.append(f"{now()} RESUME FAILED — daemon did not pass the kill tick")
    else:
        timeline.append(f"{now()} resumed and ticking (tick {resumed['tick']})")

    remaining = total_s - kill_after
    end = time.monotonic() + remaining
    worst_gaps = 0
    while time.monotonic() < end:
        time.sleep(min(60, max(1, end - time.monotonic())))
        health = read_health(args.port)
        if health:
            worst_gaps = max(worst_gaps, health.get("feed", {}).get("gap_count", 0))
            if health.get("audit_critical"):
                ok = False
                timeline.append(f"{now()} AUDIT CRITICAL latched")

    final = read_health(args.port) or {}
    proc.terminate()
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
    timeline.append(f"{now()} daemon stopped (final tick {final.get('tick', '?')})")

    audit_ok, detail = audit.audit(args.db, records_root="records")
    timeline.append(f"{now()} final audit: {detail}")
    ok = ok and audit_ok

    state = audit.load_state("records")
    evidence = [
        f"final health: {json.dumps(final, indent=1)}",
        f"feed gaps observed: {worst_gaps} (each is a counted outage, not an error)",
        f"audited segments: {sorted(state['segments'])}",
        f"audit critical: {state['critical']}",
    ]
    record.section("timeline", "\n".join(timeline))
    record.section("evidence", "\n".join(evidence))
    headline = ("SOAK PASS — survived a hard kill, audit byte-identical"
                if ok else "SOAK FAIL")
    record.finish(headline, level="INFO" if ok else "CRITICAL")
    print(headline)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

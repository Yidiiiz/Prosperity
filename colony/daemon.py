"""The always-on colony (spec v2 section 6): one process, stdlib only, a
supervisor loop around the existing orchestrator.

- spawns the feed subprocess and restarts it with 1s->60s backoff, teeing
  its output into records/feed/
- consumes the journal exactly as live mode always has: the wall clock
  paces, the journal decides (#30); stale pauses, only operators stop it
- flush_every is pinned to 1 (validator), so a hard kill at any instant
  resumes byte-identically (#4)
- after each segment rotation (and at startup for unaudited closed
  segments) it runs the replay-twin audit in a subprocess (spec v2 6.5);
  a mismatch is CRITICAL and latches until an operator clears it, but the
  daemon keeps running — an audit failure is an alarm about the past
- health is written to a sidecar JSON the read-only web layer serves as
  /api/health; `colony daemon status` exits non-zero when unhealthy
"""

import datetime
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import audit, db, ledger, orchestrator

BACKOFF_START, BACKOFF_CAP = 1.0, 60.0
UTC = datetime.timezone.utc


def health_path(db_path):
    return Path(f"{db_path}.health.json")


def pid_path(db_path):
    return Path(db_path).parent / "daemon.pid"


def _pid_alive(pid):
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Daemon:
    def __init__(self, db_path, cfg, records_root="records", port=None):
        if cfg["arena"].get("kind") != "live" or not cfg["arena"].get("journal"):
            raise ValueError("colony daemon needs a live arena with a 'journal' directory")
        if cfg.get("flush_every", 1) != 1:
            raise ValueError("daemon configs pin flush_every 1")
        self.db_path = db_path
        self.cfg = cfg
        self.records_root = records_root
        self.port = port
        self.stop = threading.Event()
        self.started = time.monotonic()
        self.feed_proc = None
        self.feed_log = None
        self.feed_restarts = 0
        self.feed_backoff = BACKOFF_START
        self.feed_next_start = 0.0
        self.audit_proc = None
        self.gap_count = 0
        self.last_row_wall = time.monotonic()
        self.last_invariant_utc = None
        self._last_bar_utc = None

    # ----------------------------------------------------------- pid guard

    def _acquire_pid(self):
        path = pid_path(self.db_path)
        if path.exists():
            try:
                old = int(path.read_text().strip())
            except ValueError:
                old = -1
            if old > 0 and _pid_alive(old):
                raise RuntimeError(f"daemon already running (pid {old}, {path})")
            print(f"reclaiming stale pid file {path} (pid {old} is dead)", flush=True)
        path.write_text(str(os.getpid()), encoding="utf-8")
        return path

    # ---------------------------------------------------------------- feed

    def _feed_cmd(self):
        feed = self.cfg.get("feed")
        if not feed:
            return None
        if feed.get("cmd"):
            return list(feed["cmd"])
        tools = Path(__file__).resolve().parent.parent / "tools" / "live_feed.py"
        cmd = [sys.executable, str(tools), feed["symbol"],
               "--journal", self.cfg["arena"]["journal"],
               "--mode", feed.get("mode", "ws")]
        if feed.get("interval"):
            cmd += ["--interval", str(feed["interval"])]
        if feed.get("ws_host"):
            cmd += ["--ws-host", feed["ws_host"]]
        return cmd

    def _supervise_feed(self):
        cmd = self._feed_cmd()
        if cmd is None or self.stop.is_set():
            return
        if self.feed_proc is not None and self.feed_proc.poll() is None:
            return
        if time.monotonic() < self.feed_next_start:
            return
        if self.feed_proc is not None:  # it exited: restart with backoff
            self.feed_restarts += 1
            self.feed_backoff = min(BACKOFF_CAP, self.feed_backoff * 2)
        if self.feed_log is None:
            log_dir = Path(self.records_root) / "feed"
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            self.feed_log = open(log_dir / f"feed_{stamp}_p{os.getpid()}.log", "a",
                                 encoding="utf-8")
        self.feed_proc = subprocess.Popen(cmd, stdout=self.feed_log, stderr=self.feed_log)
        self.feed_next_start = time.monotonic() + self.feed_backoff

    def _stop_feed(self):
        if self.feed_proc is not None and self.feed_proc.poll() is None:
            self.feed_proc.terminate()
            try:
                self.feed_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.feed_proc.kill()
        if self.feed_log is not None:
            self.feed_log.close()
            self.feed_log = None

    # --------------------------------------------------------------- audit

    def _audited(self):
        return set(audit.load_state(self.records_root)["segments"])

    def _maybe_launch_audit(self, arena, consumed_rows):
        if self.audit_proc is not None and self.audit_proc.poll() is None:
            return
        self.audit_proc = None
        names = sorted(p.name for p in Path(self.cfg["arena"]["journal"]).glob("*.csv"))
        closed = names[:-1]
        rows = 0
        fully_consumed = []
        for name, count in arena._segments:
            rows += count
            if name in closed and rows <= consumed_rows:
                fully_consumed.append(name)
        if set(fully_consumed) - self._audited():
            self.audit_proc = subprocess.Popen(
                [sys.executable, "-m", "colony", "--db", str(self.db_path), "audit",
                 "--records", str(self.records_root)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    # -------------------------------------------------------------- health

    def _health(self, orch, arena, state):
        audit_state = audit.load_state(self.records_root)
        last_audit = None
        if audit_state["segments"]:
            name = sorted(audit_state["segments"])[-1]
            entry = audit_state["segments"][name]
            last_audit = {"segment": name, "ok": entry["ok"], "utc": entry["utc"]}
        feed_connected = self.feed_proc is not None and self.feed_proc.poll() is None
        return {
            "state": state,
            "uptime_s": round(time.monotonic() - self.started, 1),
            "tick": orch.tick,
            "ticks_behind_feed": max(0, len(arena._prices) - 1 - orch.tick),
            "feed": {
                "connected": feed_connected,
                "last_row_utc": arena._times[-1] if arena._times else None,
                "reconnects": self.feed_restarts,
                "gap_count": self.gap_count,
            },
            "last_invariant_check_utc": self.last_invariant_utc,
            "last_audit": last_audit,
            "audit_critical": audit_state["critical"],
            "flush_every": 1,
        }

    def _write_health(self, payload):
        path = health_path(self.db_path)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(path)

    # ----------------------------------------------------------- main loop

    def _install_signals(self):
        def handler(signum, frame):
            self.stop.set()
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                try:
                    signal.signal(getattr(signal, name), handler)
                except (ValueError, OSError):
                    pass  # not the main thread (tests)

    def run(self, max_ticks=None, install_signals=True):
        """Supervise forever (or for max_ticks rows, for tests/soaks).
        Returns the number of ticks executed."""
        pid_file = self._acquire_pid()
        if install_signals:
            self._install_signals()
        con = db.connect(self.db_path)
        server_httpd = None
        ticks = 0
        try:
            fresh = not con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone()[0]
            if fresh:
                # first boot: bring the feed up, wait for the first journal
                # row, then init the colony against real data
                journal = Path(self.cfg["arena"]["journal"])
                deadline = time.monotonic() + self.cfg["arena"].get(
                    "poll_timeout_seconds", 120)
                while not self.stop.is_set():
                    self._supervise_feed()
                    if any(journal.glob("*.csv")):
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError(f"no journal rows in {journal} — is the feed up?")
                    time.sleep(0.5)
                orchestrator.init_colony(con, self.cfg)
                print(f"initialized {self.db_path} against the live journal", flush=True)
            orch = orchestrator.Orchestrator(con)
            arena = orch.arena
            arena.timeout = 0.5  # short wait slices; the daemon owns staleness
            poll_timeout = self.cfg["arena"].get("poll_timeout_seconds", 120)
            if self.port is not None:
                from . import server
                server_httpd = server.make_server(
                    str(self.db_path), self.port, records_root=self.records_root)
                threading.Thread(target=server_httpd.serve_forever, daemon=True).start()
                print(f"observatory on http://127.0.0.1:{server_httpd.server_address[1]}/",
                      flush=True)
            self._last_bar_utc = arena.utc() if arena._times else None
            state = "running"
            last_health = 0.0
            while not self.stop.is_set():
                self._supervise_feed()
                self._maybe_launch_audit(arena, orch.tick + 1)
                if arena.wait_for_data():
                    orch.step()
                    ticks += 1
                    bar = arena.utc()
                    ts = self.cfg["_tick_seconds"]
                    if self._last_bar_utc is not None and bar - self._last_bar_utc > ts:
                        # a feed gap is NOT an error: the colony simply didn't tick
                        self.gap_count += 1
                    self._last_bar_utc = bar
                    self.last_row_wall = time.monotonic()
                    ledger.verify_fast(con, self.cfg["initial_treasury_u"])
                    self.last_invariant_utc = datetime.datetime.now(UTC).isoformat(
                        timespec="seconds")
                    state = "auditing" if (
                        self.audit_proc is not None and self.audit_proc.poll() is None
                    ) else "running"
                    if max_ticks is not None and ticks >= max_ticks:
                        break
                elif time.monotonic() - self.last_row_wall > poll_timeout:
                    state = "stale"  # pause and keep serving; never exit...
                    if max_ticks is not None:
                        break  # ...except in bounded runs (tests/soaks)
                now = time.monotonic()
                if now - last_health >= 1.0:
                    self._write_health(self._health(orch, arena, state))
                    last_health = now
            # graceful exit: the current tick's transaction already committed
            self._write_health(self._health(orch, arena, "stopped"))
            return ticks
        finally:
            self._stop_feed()
            if self.audit_proc is not None and self.audit_proc.poll() is None:
                self.audit_proc.wait(timeout=60)
            if server_httpd is not None:
                server_httpd.shutdown()
            con.close()
            pid_file.unlink(missing_ok=True)


def status(port, db_path=None):
    """`colony daemon status`: 0 healthy, 1 unhealthy/critical, 2 unreachable."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
            health = json.load(r)
    except OSError as exc:
        print(f"daemon unreachable on port {port}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(health, indent=1))
    if health.get("state") not in ("running", "stale", "auditing"):
        return 1
    if health.get("audit_critical") or (
        health.get("last_audit") and not health["last_audit"]["ok"]
    ):
        return 1
    return 0

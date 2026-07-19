"""The Observatory: read-only web monitor (spec 9.1).

Security rule: the web layer opens SQLite in read-only mode and has no write
path of any kind — no start/stop/config endpoints, GET only. Control stays in
the CLI. Binds to 127.0.0.1 by default.
"""

import hashlib
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import evolution

DASHBOARD = Path(__file__).resolve().parent / "web" / "dashboard.html"
RECORDS_ROOT = Path("records")


def open_readonly(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _config(con):
    return json.loads(
        con.execute("SELECT config_json FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    )


def _latest_metrics(con):
    return con.execute("SELECT * FROM colony_metrics ORDER BY tick DESC LIMIT 1").fetchone()


def api_summary(con, _query):
    run = con.execute("SELECT id, config_json FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    cfg = json.loads(run["config_json"])
    m = _latest_metrics(con)
    if m is None:
        return {"run_id": run["id"], "tick": 0, "config": cfg}
    total = con.execute("SELECT COALESCE(SUM(balance_u), 0) FROM balances").fetchone()[0]
    debt = con.execute(
        "SELECT COALESCE(SUM(debt_u), 0) FROM agents WHERE died_tick IS NULL"
    ).fetchone()[0]
    return {
        "run_id": run["id"],
        "tick": m["tick"],
        "regime_kind": m["regime_kind"],
        "treasury_u": m["treasury_u"],
        "colony_wealth_u": m["colony_wealth_u"],
        "system_total_u": m["treasury_u"] + m["colony_wealth_u"],
        "arena_extracted_u": -m["arena_u"],
        "population": m["population"],
        "births_cum": m["births_cum"],
        "deaths_cum": m["deaths_cum"],
        "outstanding_debt_u": debt,
        "invariant_ok": total == cfg["initial_treasury_u"],
        "config": cfg,
    }


def api_timeseries(con, query):
    after = int(query.get("after_tick", ["0"])[0])
    rows = con.execute(
        "SELECT * FROM colony_metrics WHERE tick > ? ORDER BY tick", (after,)
    ).fetchall()
    columns = ["tick", "utc", "treasury_u", "colony_wealth_u", "population", "price_u",
               "regime_kind", "share_momentum", "share_mean_revert", "share_sitter",
               "diversity", "births_cum", "deaths_cum"]
    return {c: [row[c] for row in rows] for c in columns}


def api_deaths(con, _query):
    return dict(
        con.execute(
            "SELECT death_cause, COUNT(*) FROM agents WHERE died_tick IS NOT NULL GROUP BY 1"
        ).fetchall()
    )


def _lineage_depth(con, agent_id):
    depth = 0
    current = agent_id
    while True:
        row = con.execute("SELECT parent_a FROM agents WHERE id = ?", (current,)).fetchone()
        if row is None or row[0] is None:
            return depth
        depth += 1
        current = row[0]


def api_leaderboard(con, query):
    limit = min(int(query.get("limit", ["20"])[0]), 100)
    cfg = _config(con)
    m = _latest_metrics(con)
    price = m["price_u"] if m else cfg["arena"].get("start_price_u", 0)
    tick = m["tick"] if m else 0
    min_age = max(cfg["min_ticks_for_fitness"], 3 * cfg["snapshot_every"])
    rows = con.execute(
        """
        SELECT a.id, a.generation, a.genome_json, a.born_tick,
               b.balance_u + COALESCE(p.lots, 0) * ? AS equity_u,
               s.first_snap_equity_u, s.peak_equity_u
        FROM agents a
        JOIN balances b ON b.account_id = 'AGENT:' || a.id
        LEFT JOIN positions p ON p.agent_id = a.id AND p.asset = 'SIM'
        JOIN agent_state s ON s.agent_id = a.id
        WHERE a.died_tick IS NULL
        ORDER BY equity_u DESC, a.id
        LIMIT ?
        """,
        (price, limit),
    ).fetchall()
    board = []
    for row in rows:
        board.append({
            "id": row["id"],
            "generation": row["generation"],
            "archetype": json.loads(row["genome_json"])["archetype"],
            "equity_u": row["equity_u"],
            "fitness": evolution.fitness(
                row["equity_u"], row["first_snap_equity_u"],
                tick - row["born_tick"], row["peak_equity_u"], min_age,
            ),
            "age": tick - row["born_tick"],
            "lineage_depth": _lineage_depth(con, row["id"]),
        })
    return board


def api_agent(con, agent_id):
    row = con.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if row is None:
        return None
    state = con.execute("SELECT * FROM agent_state WHERE agent_id = ?", (agent_id,)).fetchone()
    trades = [
        dict(t)
        for t in con.execute(
            "SELECT tick, side, lots, price_u, fee_u FROM trades"
            " WHERE agent_id = ? ORDER BY seq DESC LIMIT 50",
            (agent_id,),
        )
    ]
    last_snap = con.execute(
        "SELECT tick, cash_u, equity_u FROM snapshots WHERE agent_id = ?"
        " ORDER BY tick DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    return {
        "id": row["id"],
        "genome": json.loads(row["genome_json"]),
        "generation": row["generation"],
        "parents": [row["parent_a"], row["parent_b"]],
        "born_tick": row["born_tick"],
        "died_tick": row["died_tick"],
        "death_cause": row["death_cause"],
        "debt_u": row["debt_u"],
        "birth_seed_u": state["birth_seed_u"] if state else None,
        "peak_equity_u": state["peak_equity_u"] if state else None,
        "last_snapshot": dict(last_snap) if last_snap else None,
        "trades": trades,
    }


def api_health(db_path):
    """Daemon health (spec v2 6.4): served from the sidecar JSON the daemon
    writes atomically each second; the web layer stays read-only."""
    sidecar = Path(f"{db_path}.health.json")
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"state": "no-daemon"}


def api_runs(con, _query):
    return [
        {
            "id": row["id"],
            "started_at": row["started_at"],
            "config_hash": hashlib.sha256(row["config_json"].encode()).hexdigest()[:12],
            "rng_seed": json.loads(row["config_json"])["rng_seed"],
            "last_tick": row["last_tick"],
        }
        for row in con.execute("SELECT * FROM runs ORDER BY id")
    ]


class Handler(BaseHTTPRequestHandler):
    server_version = "Observatory/1.0"
    db_path = "colony.db"
    records_root = RECORDS_ROOT

    def log_message(self, fmt, *args):  # quiet by default; this is a monitor
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj, status=200):
        self._send(status, json.dumps(obj))

    def do_GET(self):  # the ONLY verb; everything else auto-501s
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/":
                self._send(200, DASHBOARD.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/summary":
                self._with_db(api_summary, query)
            elif path == "/api/timeseries":
                self._with_db(api_timeseries, query)
            elif path == "/api/deaths":
                self._with_db(api_deaths, query)
            elif path == "/api/leaderboard":
                self._with_db(api_leaderboard, query)
            elif path.startswith("/api/agent/"):
                agent_id = path.removeprefix("/api/agent/")
                con = open_readonly(self.db_path)
                try:
                    result = api_agent(con, agent_id)
                finally:
                    con.close()
                if result is None:
                    self._send_json({"error": "no such agent"}, 404)
                else:
                    self._send_json(result)
            elif path == "/api/health":
                self._send_json(api_health(self.db_path))
            elif path == "/api/runs":
                self._with_db(api_runs, query)
            elif path.startswith("/records"):
                self._serve_record(path.removeprefix("/records").lstrip("/"))
            else:
                self._send_json({"error": "not found"}, 404)
        except sqlite3.Error as exc:
            self._send_json({"error": f"db: {exc}"}, 500)

    def _with_db(self, fn, query):
        con = open_readonly(self.db_path)
        try:
            self._send_json(fn(con, query))
        finally:
            con.close()

    def _serve_record(self, rel):
        """Read-only static serving of the records folder, traversal-proof."""
        root = Path(self.records_root).resolve()
        if not root.is_dir():
            self._send_json({"error": "no records directory"}, 404)
            return
        target = (root / rel).resolve() if rel else root
        if root != target and root not in target.parents:
            self._send_json({"error": "not found"}, 404)
            return
        if target.is_dir():
            entries = sorted(
                p.relative_to(root).as_posix() + ("/" if p.is_dir() else "")
                for p in target.iterdir()
            )
            self._send(200, "\n".join(entries) + "\n", "text/plain; charset=utf-8")
        elif target.is_file():
            self._send(200, target.read_bytes(), "text/plain; charset=utf-8")
        else:
            self._send_json({"error": "not found"}, 404)


def make_server(db_path, port, host="127.0.0.1", records_root=RECORDS_ROOT):
    handler = type("BoundHandler", (Handler,), {
        "db_path": db_path, "records_root": records_root,
    })
    return ThreadingHTTPServer((host, port), handler)


def serve(db_path, port):
    httpd = make_server(db_path, port)
    print(f"Observatory on http://127.0.0.1:{port}/  (read-only; Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()

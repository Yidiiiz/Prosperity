"""The Observatory: read-only web monitor (spec 9.1).

Security rule: the web layer opens SQLite in read-only mode and has no write
path of any kind — no start/stop/config endpoints, GET only. Control stays in
the CLI. Binds to 127.0.0.1 by default.
"""

import hashlib
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import evolution

DASHBOARD = Path(__file__).resolve().parent / "web" / "dashboard.html"
RECORDS_ROOT = Path("records")
SSE_INTERVAL = 1.0  # spec v2 8.1: at most one summary event per second


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


_bh_cache = {}  # (db_path, tick) -> vs-B&H block; one entry, recomputed on tick


def _vs_buy_and_hold(cfg, tick):
    """The benchmark block for the Money Strip (spec v3 8.1): buy-and-hold
    of the initial capitalization over the span consumed so far — replay
    reads its tape, live concatenates its journal. Petri has no real tape,
    so the block is None there."""
    kind = cfg["arena"].get("kind", "petri")
    if kind == "petri" or tick < 1:
        return None
    key = (cfg["arena"].get("csv") or cfg["arena"].get("journal"), tick)
    if key in _bh_cache:
        return _bh_cache[key]
    from . import benchmark
    if kind == "replay":
        from .arenas.replay import read_rows
        times, closes = read_rows(cfg["arena"]["csv"])
    else:  # live: the journal is the tape (#30)
        from .arenas.live import _read_file
        times, closes = [], []
        for seg in sorted(Path(cfg["arena"]["journal"]).glob("*.csv")):
            t, c = _read_file(seg)
            times.extend(t)
            closes.extend(c)
    times, closes = times[: tick + 1], closes[: tick + 1]
    if len(closes) < 2:
        return None
    initial = cfg["initial_treasury_u"]
    bench = benchmark.buy_and_hold(closes, initial, cfg["venue"],
                                   cfg["arena"].get("lot_denominator", 1))
    years = benchmark.span_years(times[0], times[-1])
    block = {"bh_u": bench, "span_years": years,
             "bh_cagr": benchmark.cagr(initial, bench, years)}
    _bh_cache.clear()
    _bh_cache[key] = block
    return block


def _extracted_since(con, arena_now, boundary_utc):
    """-(delta ARENA) since the last metric at or before boundary_utc. The
    arena account starts at 0, so a missing base row means 'since genesis'."""
    row = con.execute(
        "SELECT arena_u FROM colony_metrics WHERE utc <= ? ORDER BY tick DESC LIMIT 1",
        (boundary_utc,),
    ).fetchone()
    return -(arena_now - (row["arena_u"] if row else 0))


def api_summary(con, _query):
    run = con.execute(
        "SELECT id, config_json, state_json FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    cfg = json.loads(run["config_json"])
    m = _latest_metrics(con)
    if m is None:
        return {"run_id": run["id"], "tick": 0, "config": cfg}
    total = con.execute("SELECT COALESCE(SUM(balance_u), 0) FROM balances").fetchone()[0]
    debt = con.execute(
        "SELECT COALESCE(SUM(debt_u), 0) FROM agents WHERE died_tick IS NULL"
    ).fetchone()[0]
    # the Money Strip (spec v2 8.2): realized cash vs marked-to-market value
    # are NEVER summed into one on-screen figure
    colony_cash = con.execute(
        "SELECT COALESCE(SUM(b.balance_u), 0) FROM balances b"
        " JOIN accounts a ON a.id = b.account_id JOIN agents ag ON a.id = 'AGENT:' || ag.id"
        " WHERE a.kind = 'AGENT' AND ag.died_tick IS NULL"
    ).fetchone()[0]
    open_lots = con.execute(
        "SELECT COALESCE(SUM(p.lots), 0) FROM positions p"
        " JOIN agents a ON a.id = p.agent_id WHERE a.died_tick IS NULL"
    ).fetchone()[0]
    utc = m["utc"]
    prev = con.execute(
        "SELECT utc, arena_u FROM colony_metrics WHERE utc <= ? ORDER BY tick DESC LIMIT 1",
        (utc - 60,),
    ).fetchone()
    per_second = 0.0
    if prev and utc > prev["utc"]:
        per_second = -(m["arena_u"] - prev["arena_u"]) / (utc - prev["utc"])
    state = json.loads(run["state_json"])
    imm_capacity = cfg["initial_treasury_u"] * cfg.get("immigration_budget_apr_bps", 2_000) // 10_000
    system_total = m["treasury_u"] + m["colony_wealth_u"]
    vs_bh = _vs_buy_and_hold(cfg, m["tick"])
    if vs_bh is not None:
        # signed, colored, allowed to be red on the client (spec v3 8.1):
        # a red delta on a green treasury is the honest CASH-tier picture
        from . import benchmark
        vs_bh = dict(vs_bh, vs_bh_u=system_total - vs_bh["bh_u"],
                     system_cagr=benchmark.cagr(cfg["initial_treasury_u"],
                                                system_total,
                                                vs_bh["span_years"]))
    return {
        "run_id": run["id"],
        "tick": m["tick"],
        "utc": utc,
        "regime_kind": m["regime_kind"],
        "treasury_u": m["treasury_u"],
        "colony_wealth_u": m["colony_wealth_u"],
        "colony_cash_u": colony_cash,
        "marked_u": open_lots * m["price_u"],
        "system_total_u": system_total,
        "vs_bh": vs_bh,
        "arena_extracted_u": -m["arena_u"],
        "extracted_today_u": _extracted_since(con, m["arena_u"], utc - (utc % 86_400)),
        "extracted_hour_u": _extracted_since(con, m["arena_u"], utc - (utc % 3_600)),
        "extracted_per_second_u": per_second,
        "population": m["population"],
        "births_cum": m["births_cum"],
        "deaths_cum": m["deaths_cum"],
        "outstanding_debt_u": debt,
        "invariant_ok": total == cfg["initial_treasury_u"],
        "immigration_tokens_u": state.get("immigration_tokens_u"),
        "immigration_capacity_u": imm_capacity,
        "population_floor": cfg["population_floor"],
        "config": cfg,
    }


TIMESERIES_COLUMNS = ["tick", "utc", "treasury_u", "colony_wealth_u", "population",
                      "price_u", "regime_kind", "share_momentum", "share_mean_revert",
                      "share_sitter", "diversity", "births_cum", "deaths_cum"]
BUCKET_MINMAX = ("price_u", "treasury_u", "colony_wealth_u", "population")


def api_timeseries(con, query):
    """Incremental series; with more rows than max_points (default 2,000,
    spec v2 8.4) they are bucketed server-side: last-of-bucket for every
    column plus min/max envelopes for the charted money/price series. A full
    86,400-tick day renders from under 100 KB."""
    after = int(query.get("after_tick", ["0"])[0])
    max_points = max(1, int(query.get("max_points", ["2000"])[0]))
    rows = con.execute(
        "SELECT * FROM colony_metrics WHERE tick > ? ORDER BY tick", (after,)
    ).fetchall()
    if len(rows) <= max_points:
        out = {c: [row[c] for row in rows] for c in TIMESERIES_COLUMNS}
        out["bucketed"] = False
        return out
    lo, hi = rows[0]["tick"], rows[-1]["tick"]
    span = hi - lo + 1
    buckets = []
    current = []
    for row in rows:
        idx = (row["tick"] - lo) * max_points // span
        if current and idx != current[0]:
            buckets.append(current[1])
            current = []
        if not current:
            current = [idx, []]
        current[1].append(row)
    buckets.append(current[1])
    out = {c: [b[-1][c] for b in buckets] for c in TIMESERIES_COLUMNS}
    for col in BUCKET_MINMAX:
        out[col + "_min"] = [min(r[col] for r in b) for b in buckets]
        out[col + "_max"] = [max(r[col] for r in b) for b in buckets]
    out["bucketed"] = True
    return out


def api_tape(con, query):
    """The trade tape (spec v2 8.5): the last N fills, newest first."""
    limit = min(int(query.get("limit", ["50"])[0]), 200)
    after_seq = int(query.get("after_seq", ["0"])[0])
    rows = con.execute(
        "SELECT seq, tick, utc, agent_id, side, lots, price_u, fee_u, spread_u"
        " FROM trades WHERE seq > ? ORDER BY seq DESC LIMIT ?",
        (after_seq, limit),
    ).fetchall()
    return [dict(r) for r in rows]


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
    has_origin = any(r[1] == "origin"
                     for r in con.execute("PRAGMA table_info(agents)"))
    rows = con.execute(
        f"""
        SELECT a.id, a.generation, a.genome_json, a.born_tick,
               {"a.origin" if has_origin else "NULL AS origin"},
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
            "origin": row["origin"],
            "equity_u": row["equity_u"],
            "fitness": evolution.fitness(
                row["equity_u"], row["first_snap_equity_u"],
                tick - row["born_tick"], row["peak_equity_u"], min_age,
            ),
            "age": tick - row["born_tick"],
            "lineage_depth": _lineage_depth(con, row["id"]),
        })
    return board


def _lineage_chain(con, agent_id, limit=64):
    """Ancestor chain following parent_a (spec v2 8.7): id, generation,
    archetype, peak equity, fate — rendered inline, no graph library."""
    chain = []
    current = agent_id
    while current is not None and len(chain) < limit:
        row = con.execute(
            "SELECT a.id, a.generation, a.genome_json, a.parent_a, a.died_tick,"
            " a.death_cause, s.peak_equity_u FROM agents a"
            " LEFT JOIN agent_state s ON s.agent_id = a.id WHERE a.id = ?",
            (current,),
        ).fetchone()
        if row is None:
            break
        chain.append({
            "id": row["id"],
            "generation": row["generation"],
            "archetype": json.loads(row["genome_json"])["archetype"],
            "peak_equity_u": row["peak_equity_u"],
            "fate": row["death_cause"] if row["died_tick"] is not None else "alive",
        })
        current = row["parent_a"]
    return chain


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
    keys = row.keys()
    return {
        "id": row["id"],
        "genome": json.loads(row["genome_json"]),
        "generation": row["generation"],
        "origin": row["origin"] if "origin" in keys else None,
        "parents": [row["parent_a"], row["parent_b"]],
        "born_tick": row["born_tick"],
        "died_tick": row["died_tick"],
        "death_cause": row["death_cause"],
        "debt_u": row["debt_u"],
        "birth_seed_u": state["birth_seed_u"] if state else None,
        "peak_equity_u": state["peak_equity_u"] if state else None,
        "last_snapshot": dict(last_snap) if last_snap else None,
        "trades": trades,
        "lineage": _lineage_chain(con, agent_id),
    }


def api_bank(con, _query):
    """The Champions panel (spec v3 8.2): the snapshot this colony was
    initialized with, plus how many living agents descend from each champion
    (the bank immigrant and its whole lineage, via origin)."""
    try:
        rows = con.execute("SELECT genome_hash, genome_json, provenance"
                           " FROM bank_snapshot ORDER BY genome_hash").fetchall()
    except sqlite3.OperationalError:
        return []  # pre-v3 database: no snapshot table
    out = []
    for row in rows:
        prefix = row["genome_hash"][:12]
        living = con.execute(
            """
            WITH RECURSIVE clan(id) AS (
              SELECT id FROM agents WHERE origin = ?
              UNION
              SELECT a.id FROM agents a JOIN clan c
                ON a.parent_a = c.id OR a.parent_b = c.id
            )
            SELECT COUNT(*) FROM clan JOIN agents ag ON ag.id = clan.id
            WHERE ag.died_tick IS NULL
            """,
            (f"bank:{prefix}",),
        ).fetchone()[0]
        out.append({
            "hash_prefix": prefix,
            "archetype": json.loads(row["genome_json"])["archetype"],
            "provenance": row["provenance"],
            "living_descendants": living,
        })
    return out


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
    server_version = "Observatory/3.0"
    db_path = "colony.db"
    records_root = RECORDS_ROOT
    sse_interval = SSE_INTERVAL

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
            elif path == "/api/tape":
                self._with_db(api_tape, query)
            elif path == "/api/bank":
                self._with_db(api_bank, query)
            elif path == "/api/events":
                self._serve_events()
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

    def _serve_events(self):
        """Server-Sent Events (spec v2 8.1): push, not poll — a one-way
        stream, which is the read-only guarantee expressed as a protocol.
        Emits `summary` on tick advance (coalesced, at most one per
        sse_interval), `fill` per new trade, `health` on state change."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def emit(event, obj):
            self.wfile.write(
                f"event: {event}\ndata: {json.dumps(obj)}\n\n".encode("utf-8"))
            self.wfile.flush()

        last_tick = -1
        last_seq = None
        last_health = None
        try:
            while True:
                con = open_readonly(self.db_path)
                try:
                    summary = api_summary(con, {})
                    if last_seq is None:  # first pass: only stream NEW fills
                        row = con.execute("SELECT COALESCE(MAX(seq), 0) FROM trades").fetchone()
                        last_seq = row[0]
                    if summary.get("tick", 0) != last_tick:
                        last_tick = summary.get("tick", 0)
                        emit("summary", summary)  # coalesced: latest wins
                    for fill in reversed(api_tape(con, {"after_seq": [str(last_seq)]})):
                        last_seq = max(last_seq, fill["seq"])
                        emit("fill", fill)
                finally:
                    con.close()
                health = api_health(self.db_path)
                if health != last_health:
                    last_health = health
                    emit("health", health)
                emit("ping", {"t": time.time()})  # liveness for the client
                time.sleep(self.sse_interval)
        except OSError:
            return  # client went away; the stream simply ends

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

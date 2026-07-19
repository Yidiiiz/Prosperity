import json
import sqlite3
import threading
import urllib.error
import urllib.request

import pytest

from colony import server
from tests.conftest import make_cfg, make_colony


@pytest.fixture
def live(tmp_path):
    cfg = make_cfg()
    con, orch = make_colony(tmp_path, cfg)
    orch.run(60)
    con.close()
    records_root = tmp_path / "records"
    (records_root / "runs").mkdir(parents=True)
    (records_root / "runs" / "run_demo.txt").write_text("demo record", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the records root", encoding="utf-8")
    httpd = server.make_server(str(tmp_path / "colony.db"), 0, records_root=records_root)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url, expect=200):
    try:
        with urllib.request.urlopen(url) as resp:
            assert resp.status == expect
            return resp.read()
    except urllib.error.HTTPError as exc:
        assert exc.code == expect
        return exc.read()


def test_dashboard_served_at_root(live):
    body = get(live + "/")
    assert b"Observatory" in body and b"/api/summary" in body


def test_summary_shape(live):
    s = json.loads(get(live + "/api/summary"))
    for key in ("run_id", "tick", "regime_kind", "treasury_u", "colony_wealth_u",
                "system_total_u", "arena_extracted_u", "population", "births_cum",
                "deaths_cum", "outstanding_debt_u", "invariant_ok", "config"):
        assert key in s
    assert s["invariant_ok"] is True
    assert s["system_total_u"] == s["treasury_u"] + s["colony_wealth_u"]


def test_timeseries_incremental(live):
    ts = json.loads(get(live + "/api/timeseries?after_tick=0"))
    assert ts["tick"] and len(ts["tick"]) == len(ts["treasury_u"]) == len(ts["regime_kind"])
    last = ts["tick"][-1]
    tail = json.loads(get(live + f"/api/timeseries?after_tick={last}"))
    assert tail["tick"] == []


def test_deaths_leaderboard_agent_runs(live):
    json.loads(get(live + "/api/deaths"))
    board = json.loads(get(live + "/api/leaderboard?limit=5"))
    assert 0 < len(board) <= 5
    for key in ("id", "generation", "archetype", "equity_u", "fitness", "age",
                "lineage_depth"):
        assert key in board[0]
    agent = json.loads(get(live + f"/api/agent/{board[0]['id']}"))
    assert agent["genome"]["archetype"] == board[0]["archetype"]
    get(live + "/api/agent/999999", expect=404)
    runs = json.loads(get(live + "/api/runs"))
    assert runs and {"id", "config_hash", "rng_seed", "last_tick"} <= set(runs[0])


def test_records_listing_and_traversal_guard(live):
    listing = get(live + "/records/").decode()
    assert "runs/" in listing
    body = get(live + "/records/runs/run_demo.txt")
    assert b"demo record" in body
    get(live + "/records/../secret.txt", expect=404)


def test_summary_money_strip_fields(live):
    s = json.loads(get(live + "/api/summary"))
    for key in ("utc", "colony_cash_u", "marked_u", "extracted_today_u",
                "extracted_hour_u", "extracted_per_second_u",
                "immigration_tokens_u", "immigration_capacity_u", "population_floor"):
        assert key in s, key
    assert s["marked_u"] >= 0
    assert s["immigration_capacity_u"] > 0
    assert 0 <= s["immigration_tokens_u"] <= s["immigration_capacity_u"]


def test_timeseries_bucketing(live):
    full = json.loads(get(live + "/api/timeseries?after_tick=0"))
    assert full["bucketed"] is False
    n = len(full["tick"])
    assert n >= 2
    ts = json.loads(get(live + "/api/timeseries?after_tick=0&max_points=1"))
    assert ts["bucketed"] is True
    assert len(ts["tick"]) == 1
    assert ts["tick"][-1] == full["tick"][-1]  # last-of-bucket wins
    assert ts["price_u_min"][0] <= ts["price_u"][0] <= ts["price_u_max"][0]
    assert ts["price_u_min"][0] == min(full["price_u"])
    assert ts["price_u_max"][0] == max(full["price_u"])


def test_tape_endpoint(live):
    tape = json.loads(get(live + "/api/tape?limit=10"))
    assert isinstance(tape, list)
    for fill in tape:
        assert {"seq", "tick", "utc", "agent_id", "side", "lots",
                "price_u", "fee_u", "spread_u"} <= set(fill)


def test_agent_lineage_inline(live):
    board = json.loads(get(live + "/api/leaderboard?limit=1"))
    agent = json.loads(get(live + f"/api/agent/{board[0]['id']}"))
    chain = agent["lineage"]
    assert chain and chain[0]["id"] == board[0]["id"]
    for link in chain:
        assert {"id", "generation", "archetype", "peak_equity_u", "fate"} <= set(link)
    assert chain[0]["fate"] == "alive"


def test_sse_stream_emits_summary_fill_and_ping(live):
    import http.client
    host, port = live.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        assert resp.getheader("Content-Type").startswith("text/event-stream")
        seen = set()
        body = b""
        while not {"summary", "ping"} <= seen:
            line = resp.fp.readline()
            assert line, "stream ended before summary+ping arrived"
            body += line
            if line.startswith(b"event: "):
                seen.add(line.split(b": ")[1].strip().decode())
        payload = [l for l in body.split(b"\n") if l.startswith(b"data: ")][0]
        assert b"treasury_u" in payload  # the summary event carries the summary
    finally:
        conn.close()


def test_dashboard_is_v2(live):
    body = get(live + "/")
    for marker in (b'id="money"', b'id="tape"', b"EventSource", b"/api/events",
                   b'id="chips"', b"max-width: 720px"):
        assert marker in body, marker


def test_non_get_rejected(live):
    req = urllib.request.Request(live + "/api/summary", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 501  # no do_POST exists anywhere


def test_server_connection_is_readonly(live, tmp_path):
    con = server.open_readonly(tmp_path / "colony.db")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO accounts (id, kind) VALUES ('X', 'AGENT')")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("UPDATE balances SET balance_u = 0")


def test_handler_exposes_no_write_routes():
    verbs = [m for m in dir(server.Handler) if m.startswith("do_")]
    assert verbs == ["do_GET"]

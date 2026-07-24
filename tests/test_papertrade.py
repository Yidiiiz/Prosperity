"""paper-trader: pure_mom decision logic, monthly-hold cadence, dry-run writes
nothing, book opens at $10,000. Reads the committed local daily tapes (offline;
no network — the repo red line)."""

import json

from experiments import papertrade
from experiments.papertrade import decide
from experiments.allocation import CAPITAL_U
from experiments.allocation12 import RISK


def test_decide_picks_top_positive_momentum():
    n = 300
    closes = {x: [100.0] * n for x in RISK}
    closes["qqq"] = [100.0 * (1.002 ** k) for k in range(n)]
    t, hold, ranked = decide(closes, 290, 252)
    assert hold == "qqq" and t == {"qqq": 1.0}
    assert ranked[0][1] == "qqq"


def test_decide_cash_when_all_negative():
    n = 300
    closes = {x: [100.0 * (0.999 ** k) for k in range(n)] for x in RISK}
    t, hold, _ = decide(closes, 290, 252)
    assert hold == "cash" and t == {}


def test_run_opens_book_and_rebalances(tmp_path):
    s, l = tmp_path / "s.json", tmp_path / "l.csv"
    rc = papertrade.run([], state_path=str(s), ledger_path=str(l))
    assert rc == 0 and s.exists() and l.exists()
    st = json.loads(s.read_text(encoding="utf-8"))
    assert st["strategy"] == "pure_mom"
    assert st["last_rebal_month"] is not None          # a rotation was booked
    assert st["cash_u"] <= CAPITAL_U                    # entry cannot mint money
    lines = l.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].startswith("date,action") and len(lines) == 2
    assert lines[1].split(",")[1] == "rebalance"


def test_holds_within_the_same_month(tmp_path):
    s, l = tmp_path / "s.json", tmp_path / "l.csv"
    papertrade.run([], state_path=str(s), ledger_path=str(l))
    st1 = json.loads(s.read_text(encoding="utf-8"))
    papertrade.run([], state_path=str(s), ledger_path=str(l))
    st2 = json.loads(s.read_text(encoding="utf-8"))
    assert st1["cash_u"] == st2["cash_u"] and st1["lots"] == st2["lots"]
    last = l.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert last.split(",")[1] == "hold"


def test_dry_run_writes_nothing(tmp_path):
    s, l = tmp_path / "s.json", tmp_path / "l.csv"
    papertrade.run(["--dry-run"], state_path=str(s), ledger_path=str(l))
    assert not s.exists() and not l.exists()

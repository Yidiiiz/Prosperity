"""spec v3 section 3: realized per-agent stats derive from the ledger and
reconcile exactly with conservation."""

from colony import ledger, report
from tests.conftest import make_cfg, make_colony


def _all_agent_ids(con):
    return [r[0] for r in con.execute("SELECT id FROM agents ORDER BY id")]


def test_realized_pnl_reconciles_with_conservation(tmp_path):
    cfg = make_cfg(rng_seed=7)
    con, orch = make_colony(tmp_path, cfg)
    orch.run(400)
    # every micro-dollar extracted from (or lost to) the market is the sum of
    # per-agent realized P&L: agents are the only accounts trading the arena
    total = sum(report.agent_realized(con, aid)["realized_pnl_u"]
                for aid in _all_agent_ids(con))
    assert total == -ledger.balance(con, orch.arena_account)
    orch.wind_down()
    # after the terminal audit all cash is in the treasury: initial + extraction
    total = sum(report.agent_realized(con, aid)["realized_pnl_u"]
                for aid in _all_agent_ids(con))
    assert ledger.balance(con, "TREASURY") == cfg["initial_treasury_u"] + total


def test_realized_includes_fees_and_spread(tmp_path):
    cfg = make_cfg(rng_seed=42, venue={"spread_bps": 10})
    con, orch = make_colony(tmp_path, cfg)
    orch.run(300)
    orch.wind_down()
    for aid in _all_agent_ids(con):
        stats = report.agent_realized(con, aid)
        buys, sells = con.execute(
            "SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN lots * price_u + fee_u END), 0),"
            " COALESCE(SUM(CASE WHEN side='SELL' THEN lots * price_u - fee_u END), 0)"
            " FROM trades WHERE agent_id = ?", (aid,),
        ).fetchone()
        assert stats["realized_pnl_u"] == sells - buys
        if stats["fills"] == 0:
            assert stats["realized_pnl_u"] == 0
            assert stats["realized_bps_per_day"] == 0.0


def test_leaders_ranked_by_bps_per_day_and_filtered(tmp_path):
    cfg = make_cfg(rng_seed=2026)
    con, orch = make_colony(tmp_path, cfg)
    orch.run(400)
    orch.wind_down()
    leaders = report.realized_leaders(con, min_fills=2)
    assert all(s["realized_pnl_u"] > 0 and s["fills"] >= 2 for _, s in leaders)
    ranks = [s["realized_bps_per_day"] for _, s in leaders]
    assert ranks == sorted(ranks, reverse=True)


def test_profitmakers_text_and_inspect_surface_realized(tmp_path):
    cfg = make_cfg(rng_seed=7)
    con, orch = make_colony(tmp_path, cfg)
    orch.run(300)
    orch.wind_down()
    assert "PROFITMAKERS" in report.profitmakers_text(con)
    text = report.inspect_text(con, "000001")
    assert "realized (ledger" in text

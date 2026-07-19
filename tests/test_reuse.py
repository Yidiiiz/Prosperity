"""spec v3 section 5: reuse — bank snapshot at init, bank-sourced
immigration, champion funding, determinism with a bank."""

import json

from colony import audit, bank, db
from tests.conftest import make_cfg, make_colony
from tests.test_bank import MOMENTUM, admit_event
from tests.test_determinism import ledger_hash


def certified_bank(path, n=3, n_candidates=0, n_lapsed=0, start=10):
    """A bank log with n certified genomes (plus optional other statuses)."""
    hashes = []
    for i in range(n + n_candidates + n_lapsed):
        genome = json.loads(json.dumps(MOMENTUM))
        genome["params"]["lookback"] = start + i
        h = bank.genome_hash(genome)
        bank.append_event(path, admit_event(h=h, genome=genome))
        if i < n:
            bank.append_event(path, {
                "event": "certify", "utc": "x", "genome_hash": h,
                "probe": {}, "audited": {"realized_pnl_u": 1, "fills": 5,
                                         "realized_bps_per_day": 2.0}})
            hashes.append(h)
        elif i >= n + n_candidates:
            bank.append_event(path, {"event": "lapse", "utc": "x",
                                     "genome_hash": h, "audited": {}})
    return hashes


def test_init_copies_only_certified_into_snapshot(tmp_path):
    log = tmp_path / "bank.jsonl"
    hashes = certified_bank(log, n=2, n_candidates=1, n_lapsed=1)
    cfg = make_cfg(bank_path=str(log))
    con, orch = make_colony(tmp_path, cfg)
    rows = con.execute("SELECT genome_hash, provenance FROM bank_snapshot"
                       " ORDER BY genome_hash").fetchall()
    assert [r[0] for r in rows] == sorted(hashes)
    assert all("oos" in r[1] for r in rows)
    assert len(orch.bank_pool) == 2


def test_bank_immigrants_have_origin_and_champion_seed(tmp_path):
    log = tmp_path / "bank.jsonl"
    certified_bank(log, n=3)
    cfg = make_cfg(bank_path=str(log), bank_immigrant_share_bps=10_000,
                   champion_seed_multiple=3, immigration_budget_apr_bps=10_000,
                   gen0_population=4, population_floor=8)
    con, orch = make_colony(tmp_path, cfg)
    tokens_before = orch.imm_tokens
    orch.step()  # immigration fills the floor on the first tick
    rows = con.execute(
        "SELECT a.id, a.origin, a.generation, s.birth_seed_u FROM agents a"
        " JOIN agent_state s ON s.agent_id = a.id WHERE a.origin IS NOT NULL"
    ).fetchall()
    assert len(rows) == 4  # every immigrant drawn from the bank at share 100%
    for _, origin, generation, birth_seed in rows:
        assert origin.startswith("bank:")
        assert generation == 0
        assert birth_seed == 3 * cfg["gen0_seed_u"]  # spec v3 5.3
    assert orch.imm_tokens == tokens_before - 4 * 3 * cfg["gen0_seed_u"]
    # the clone is unmutated: genome matches a snapshot genome exactly
    pool = {h: g for h, g in orch.bank_pool}
    for row in con.execute("SELECT genome_json, origin FROM agents"
                           " WHERE origin IS NOT NULL"):
        prefix = row[1].removeprefix("bank:")
        match = next(g for h, g in pool.items() if h.startswith(prefix))
        assert json.loads(row[0]) == match


def test_empty_snapshot_always_random(tmp_path):
    cfg = make_cfg(gen0_population=4, population_floor=8,
                   bank_immigrant_share_bps=10_000)
    con, orch = make_colony(tmp_path, cfg)
    orch.step()
    assert con.execute("SELECT COUNT(*) FROM agents WHERE origin IS NOT NULL"
                       ).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 8


def test_same_snapshot_and_seed_is_byte_identical(tmp_path):
    """spec v3 10.5: same config + seed + bank snapshot => identical ledger,
    across an interrupt/resume — even when the live bank file moves on."""
    from colony.orchestrator import Orchestrator

    log = tmp_path / "bank.jsonl"
    certified_bank(log, n=3)
    kw = dict(bank_path=str(log), bank_immigrant_share_bps=10_000,
              gen0_population=4, population_floor=8,
              immigration_budget_apr_bps=10_000)

    con, orch = make_colony(tmp_path, make_cfg(**kw), name="a.db")
    orch.run(120)
    a = ledger_hash(con)
    con.close()

    con, orch = make_colony(tmp_path, make_cfg(**kw), name="b.db")
    orch.run(60)
    con.close()
    certified_bank(log, n=2, start=50)  # the live bank grows mid-run
    con = db.connect(tmp_path / "b.db")
    orch = Orchestrator(con)  # resume: draws come from the SNAPSHOT (v3 5.1)
    orch.run(60)
    assert ledger_hash(con) == a
    con.close()


def test_replay_twin_audit_passes_with_bank_immigrants(tmp_path):
    """spec v3 10.5: the daemon's replay-twin audit on a session that
    included bank immigrants."""
    from colony.daemon import Daemon
    from tests.test_daemon import init_db, live_cfg
    from tests.test_feeds import seg_rows, write_segment

    log = tmp_path / "bank.jsonl"
    certified_bank(log, n=3)
    journal = tmp_path / "journal"
    write_segment(journal, "2026-07-16", seg_rows(30, base=100))
    write_segment(journal, "2026-07-17", seg_rows(30, base=130))
    write_segment(journal, "2026-07-18", seg_rows(10, base=160))
    cfg = live_cfg(journal, bank_path=str(log), bank_immigrant_share_bps=10_000,
                   gen0_population=4, population_floor=8,
                   immigration_budget_apr_bps=10_000,
                   records_root=str(tmp_path / "records"))
    db_path = init_db(tmp_path, cfg)
    d = Daemon(db_path, cfg, records_root=str(tmp_path / "records"))
    d.run(max_ticks=65, install_signals=False)

    con = db.connect(db_path, readonly=True)
    n_bank = con.execute("SELECT COUNT(*) FROM agents"
                         " WHERE origin LIKE 'bank:%'").fetchone()[0]
    con.close()
    assert n_bank > 0  # the session really did include bank immigrants
    # the live bank file changing afterwards must not break the audit
    certified_bank(log, n=1)
    ok, detail = audit.audit(db_path, records_root=str(tmp_path / "records"))
    assert ok, detail

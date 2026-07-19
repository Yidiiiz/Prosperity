"""Performance (spec v2 section 4): the >=500 ticks/s requirement.

The benchmark is an acceptance criterion (11.3): population 100, replay
arena, flush_every 100. CI enforces a generous >=250/s margin; the >=500/s
reference-hardware number is recorded in records/ (stage 10). Alongside it:
flush-cadence equivalence — flush_every 1 and 100 yield byte-identical
final ledgers, and a crash mid-window resumes to the identical ledger.
"""

import time

from colony import db, orchestrator
from tests.conftest import make_cfg, make_colony
from tests.test_determinism import ledger_hash
from tests.test_replay import write_csv

CI_FLOOR_TICKS_PER_S = 250


def bench_closes(n):
    """A lively but deterministic tape: trend + wiggle keeps agents trading."""
    closes = []
    price = 2.0
    for i in range(n):
        price *= 1.0006 if (i * 13) % 7 < 4 else 0.9996
        closes.append(round(price * (1 + 0.004 * ((i * 7) % 11 - 5) / 5), 6))
    return closes


def bench_cfg(csv_path, flush_every):
    return make_cfg(
        initial_treasury_u=200_000_000_000,
        gen0_population=100,
        max_population=150,
        population_floor=40,
        flush_every=flush_every,
        arena={"kind": "replay", "name": "bench", "csv": csv_path, "tick_seconds": 60},
        venue={"taker_bps": 10, "spread_bps": 2, "fill_delay_ticks": 1},
        debug=False,
    )


def test_throughput_population_100(tmp_path):
    path = write_csv(tmp_path / "bench.csv", bench_closes(2_501))
    con, orch = make_colony(tmp_path, bench_cfg(path, flush_every=100), "bench.db")
    assert len(orch.agents) == 100
    start = time.perf_counter()
    executed = orch.run(2_500)
    elapsed = time.perf_counter() - start
    rate = executed / elapsed
    print(f"\nthroughput: {executed} ticks in {elapsed:.2f}s = {rate:.0f} ticks/s")
    assert executed == 2_500
    assert rate >= CI_FLOOR_TICKS_PER_S, f"{rate:.0f} ticks/s is below the CI floor"


def test_flush_cadence_yields_identical_ledgers(tmp_path):
    path = write_csv(tmp_path / "p.csv", bench_closes(400))
    con_a, orch_a = make_colony(tmp_path, bench_cfg(path, flush_every=1), "a.db")
    con_b, orch_b = make_colony(tmp_path, bench_cfg(path, flush_every=100), "b.db")
    assert orch_a.run(399) == orch_b.run(399) == 399
    assert ledger_hash(con_a) == ledger_hash(con_b)


def test_crash_mid_window_resumes_to_identical_ledger(tmp_path, monkeypatch):
    """A crash inside a flush window rolls the db back to the last flushed
    boundary; deterministic replay then reproduces the identical ledger."""
    path = write_csv(tmp_path / "p.csv", bench_closes(400))
    con_a, orch_a = make_colony(tmp_path, bench_cfg(path, flush_every=100), "a.db")
    orch_a.run(300)

    con_b, orch_b = make_colony(tmp_path, bench_cfg(path, flush_every=100), "b.db")
    real = orchestrator.Orchestrator._step_inner

    def crashing(self):
        if self.tick == 249:  # mid-window: ticks 201-249 must roll back
            raise RuntimeError("injected crash mid-window")
        real(self)

    monkeypatch.setattr(orchestrator.Orchestrator, "_step_inner", crashing)
    try:
        orch_b.run(300)
    except RuntimeError:
        pass
    monkeypatch.setattr(orchestrator.Orchestrator, "_step_inner", real)
    con_b.close()

    con_b = db.connect(tmp_path / "b.db")
    resumed = orchestrator.Orchestrator(con_b)
    assert resumed.tick == 200  # the unflushed window is gone, cleanly
    resumed.run(100)
    assert resumed.tick == 300
    assert ledger_hash(con_a) == ledger_hash(con_b)

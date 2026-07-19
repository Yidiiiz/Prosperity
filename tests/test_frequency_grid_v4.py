"""spec v4 sections 4-6: the grid driver — manifest, holdout carving,
verdict logic, and one tiny offline cell end-to-end."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from colony.arenas.replay import parse_utc, read_rows
from experiments import frequency_grid as fg

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "data" / "btcusdt_1m_fixture.csv"
VENUE = {"taker_bps": 10, "maker_bps": 0, "spread_bps": 2, "min_fee_u": 0,
         "fill_delay_ticks": 1}


def test_manifest_profiles_and_windows_are_sane():
    for name, spec in fg.CELLS.items():
        assert spec["profile"] in ("second", "minute", "hourly", "daily")
        assert spec["windows"] >= 3
        assert spec["lot"] >= 1


def test_carve_holdout_slices_pins_and_refuses_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(fg, "ROOT", tmp_path)
    times, closes = read_rows(FIXTURE)
    (g_t, g_c), (h_t, h_c), path = fg.carve_holdout("cell_t", times, closes)
    assert g_c + h_c == closes and g_t + h_t == times
    assert len(h_c) == len(closes) - int(len(closes) * 0.8)
    assert path.exists()
    # idempotent second carve
    fg.carve_holdout("cell_t", times, closes)
    # drifted tape -> refuse
    with pytest.raises(SystemExit, match="does not match"):
        fg.carve_holdout("cell_t", times[:-5], closes[:-5])


def _entry(pooled_u, t0="2023-01-01", t1="2024-01-01", initial_u=10 ** 9):
    return ("w", initial_u, pooled_u, initial_u, parse_utc(t0), parse_utc(t1))


def test_judge_seed_verdicts():
    # 2023: SPY B&H on $1,000 lands well above initial; pooled far above it
    v, metrics, _ = fg.judge_seed("NO-EDGE", [_entry(2 * 10 ** 9)], VENUE)
    assert v == "BEATS-SPX"
    assert metrics[0]["spx_cagr"] > 0
    # pooled below SPY -> falls back to the v3 verdict
    v, _, _ = fg.judge_seed("EDGE", [_entry(10 ** 9 + 1)], VENUE)
    assert v == "EDGE"
    v, _, _ = fg.judge_seed("NO-EDGE", [_entry(10 ** 9 + 1)], VENUE)
    assert v == "NO-EDGE"
    # no SPY coverage -> never BEATS-SPX on zero tests
    v, metrics, _ = fg.judge_seed("NO-EDGE",
                                  [_entry(2 * 10 ** 9, "1970-01-01",
                                          "1971-01-01")], VENUE)
    assert v == "NO-EDGE" and metrics[0]["spx_cagr"] is None
    # no entries at all
    assert fg.judge_seed("NO-EDGE", [], VENUE)[0] == "NO-EDGE"


def test_tiny_cell_end_to_end(tmp_path, monkeypatch):
    """One fixture-tape cell, shrunken population, through run_cell +
    summarize: records, result JSON, holdout carve, frontier table."""
    monkeypatch.setattr(fg, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    shutil.copy(FIXTURE, tmp_path / "data" / "fix.csv")
    monkeypatch.setitem(fg.CELLS, "fix_1m", {
        "csv": "data/fix.csv", "profile": "minute", "lot": 1_000_000,
        "windows": 3,
        "overrides": {"initial_treasury_u": 20_000_000_000,
                      "gen0_population": 10, "max_population": 30,
                      "population_floor": 4, "min_ticks_for_fitness": 60}})
    args = SimpleNamespace(workdir=str(tmp_path / "wd"), cost="base",
                           min_fills=1)
    (tmp_path / "wd").mkdir()
    ok = fg.run_cell("fix_1m", args, [42])
    assert ok
    result = json.loads((tmp_path / "wd" / "result_fix_1m_42.json")
                        .read_text(encoding="utf-8"))
    assert result["verdict"] in ("BEATS-SPX", "EDGE", "NO-EDGE")
    assert (tmp_path / "data" / "holdout" / "fix_1m.csv").exists()
    recs = list((tmp_path / "records" / "experiments").glob(
        "frequency_grid_fix_1m_*.txt"))
    assert len(recs) == 1
    body = recs[0].read_text(encoding="utf-8")
    assert "span:" in body and "wall:" in body  # v3 2.2 footer intact
    assert fg.summarize(args) == 0


def test_counterfactual_cost_arm_is_labeled(tmp_path, monkeypatch):
    monkeypatch.setattr(fg, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    shutil.copy(FIXTURE, tmp_path / "data" / "fix.csv")
    monkeypatch.setitem(fg.CELLS, "fix_1m", {
        "csv": "data/fix.csv", "profile": "minute", "lot": 1_000_000,
        "windows": 3,
        "overrides": {"initial_treasury_u": 20_000_000_000,
                      "gen0_population": 10, "max_population": 30,
                      "population_floor": 4, "min_ticks_for_fitness": 60}})
    args = SimpleNamespace(workdir=str(tmp_path / "wd"), cost="free",
                           min_fills=1)
    (tmp_path / "wd").mkdir()
    assert fg.run_cell("fix_1m", args, [42])
    rec = next((tmp_path / "records" / "experiments").glob(
        "frequency_grid_fix_1m_cost_free_*.txt"))
    assert "COUNTERFACTUAL" in rec.read_text(encoding="utf-8")


def test_holdout_shot_fires_once_then_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(fg, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    shutil.copy(FIXTURE, tmp_path / "data" / "fix.csv")
    monkeypatch.setitem(fg.CELLS, "fix_1m", {
        "csv": "data/fix.csv", "profile": "minute", "lot": 1_000_000,
        "windows": 3,
        "overrides": {"initial_treasury_u": 20_000_000_000,
                      "gen0_population": 10, "max_population": 30,
                      "population_floor": 4, "min_ticks_for_fitness": 60}})
    args = SimpleNamespace(workdir=str(tmp_path / "wd"), cost="base",
                           min_fills=1, holdout="fix_1m")
    (tmp_path / "wd").mkdir()
    assert fg.holdout_shot(args, [42]) == 0
    assert (tmp_path / "data" / "holdout" / "fix_1m.SHOT").exists()
    with pytest.raises(SystemExit, match="already fired"):
        fg.holdout_shot(args, [42])

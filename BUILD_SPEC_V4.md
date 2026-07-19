# DARWIN-WALLET — Build Specification v4.0
### The Edge Hunt: frequency × asset, measured honestly

**You are upgrading an existing, working repository** (built to BUILD_SPEC_V3.md; 198 tests green; DECISIONS.md has 79 entries; `colony/` is 3,826 non-blank lines). Do not start from scratch. v1–v3 and DECISIONS.md remain in force except where this document amends them — where they conflict, **this document wins**.

**Target, in one sentence:** sweep the (asset × trading cadence × venue cost) grid with walk-forward certification, find the cell where certified champions earn faster than S&P-500 buy-and-hold over the same calendar window, confirm the winner once on a never-touched holdout — and report NO-EDGE honestly for every cell where the answer is no.

**Why v4 exists — the operator's hypothesis, and the honest way to test it.** The operator's thesis: higher trading frequency means more compounding opportunities, so faster cadences should win. The v3 evidence so far points the other way — SPY daily walk-forward: NO-EDGE ×3 seeds; BTC minute ladder: EXPECTED-FAIL ×9 with losses monotone in fill count (friction-dominated) — but that evidence is *two points*, not a frontier. Nobody has swept cadence systematically, nobody has tried more than two assets, and nobody has measured where the friction frontier sits as costs vary. v4 builds the sweep. The spec does not presume either verdict: **the deliverable is the measured frontier**, and if a cell certifies out-of-sample above the S&P bar, it gets banked and named; if none does, the deliverable is the map of *why not*, per cell, in records.

**Engineering principles carry over unchanged**: stdlib-only core, pytest the only dev dependency, integer µ$ money, one RNG, determinism, small boring functions, crash on invariant violation. **`colony/` is frozen at its v3 surface** — v4 lives in `experiments/`, `tools/`, and `tests/`; any `colony/` diff needs a DECISIONS entry justifying it. New ceiling for `experiments/`: **~1,600 non-blank lines** (currently ~1,050).

---

## 0. How to Use This Document

1. Read DECISIONS.md #63–#79 and this spec end to end before writing code.
2. Build in the §9 order; each stage's tests green before the next.
3. Every judgment call goes in DECISIONS.md, numbering continued from #79.
4. All v1–v3 acceptance criteria must STILL pass after v4 lands — regression is failure.

---

## 1. Ratifications

DECISIONS #63–#79 are promoted to spec law en bloc: realized-P&L accounting, verdict tiers, the mandatory record footer, bank admission/certification and the postdating refusal, snapshot-at-init determinism, the compounding ratchet, per-entry footer spans, held-out enforcement by date interval, EDGE = strict majority of test windows. The three operator items in #79 (year-long minute ladder, 24 h soak, CI matrix) remain outstanding v3 evidence; the v4 grid **subsumes the year-long minute ladder** (a year of BTC 1m under walk-forward is strictly stronger evidence than the same year replayed once).

The red lines are restated, not renegotiated: **virtual money only; no order-placement code anywhere in the repository; no self-modification — the bank stores parameter dicts, never code.** v4 changes *what is measured*, never *what the system is allowed to touch*.

---

## 2. The Yardstick — the S&P Bar

v3 benchmarks against buy-and-hold **of the same tape**. That stays mandatory. v4 adds the operator's actual question: *is this faster than just holding the S&P?*

**2.1 `experiments/yardstick.py` (new, target ≤60 lines).** One function:

```
spx_over(t0, t1, capital_u, venue) -> (cash_u, cagr, coverage)
```

Buy-and-hold of `data/spy_d.csv` (the honest `colony.benchmark.buy_and_hold`, same venue costs) over the calendar window [t0, t1], where t0/t1 are the tape timestamps of the run being judged. `coverage` states how much of [t0, t1] the SPY tape actually spans; if SPY covers < 90 % of the window the comparison prints `(partial SPY coverage)` — never silently.

**2.2 Every grid record's footer gains one line**: `spx: buy-and-hold $X (CAGR %/yr) | delta vs cell ±pp/yr`. Sub-year windows keep the `(projected)` suffix rule (v3 §2.3). Comparing a 3-day crypto window's projected CAGR against SPY's same-3-days projected CAGR is honest *because both are projections over the same days*; comparing either against SPY's 30-year CAGR is not, and is forbidden.

**2.3 The verdict vocabulary extends** (grid cells only; replay tiers from v3 §2.4 are unchanged underneath):

| Cell verdict | Meaning |
|---|---|
| `BEATS-SPX` | certified out-of-sample cash > SPY B&H over the same calendar window, majority of test windows |
| `EDGE` | beats same-tape B&H (v3 rule) but not SPY |
| `NO-EDGE` | neither; machinery sound |
| `FAIL` | machinery broke — still the only failure |

---

## 3. Cadence Profiles — One Colony Definition Per Timescale

**3.1 `experiments/profiles.py` (new, target ≤90 lines).** A registry `PROFILES: name -> (tick_seconds, config_factory)` with entries `second`, `minute`, `hourly`, `daily`. Each factory returns a full validated config from a (seed, csv_path) pair. `daily` is the existing `config.spy.json` path; `minute` is the existing minute-ladder profile (imported, not duplicated); `second` and `hourly` are new, with lifecycle stated in wall time (the config layer already converts): second-scale lifecycle mirrors `config.live.json`; hourly sits between minute and daily (max_age ~1 year, stagnation ~30 days, min_ticks_for_fitness ≥ 200 bars).

**3.2 `experiments/walk_forward.py` is re-based onto the registry**: `--profile` accepts any registry name; the two existing names keep their exact current behavior (byte-identical configs — regression tested). This is the only edit to an existing experiment.

**3.3 Fairness rule.** Across cadences, per-seed capitalization, archetype set, gate genes, and venue costs are held identical; **only** tick cadence, lot size, and wall-time lifecycle constants vary. A frequency comparison where the fast cell also got different fees is not a frequency comparison.

---

## 4. The Grid — `experiments/frequency_grid.py`

**4.1 A cell is (tape, profile, seeds).** The built-in manifest (overridable by `--cells`):

| cell | tape | cadence | span target |
|---|---|---|---|
| `btc_1s` | BTCUSDT 1s | second | ≥ 5 days |
| `btc_1m` | BTCUSDT 1m | minute | ≥ 1 year |
| `btc_1h` | BTCUSDT 1h | hourly | ≥ 4 years |
| `btc_1d` | BTCUSDT 1d | daily | full history (2017–) |
| `eth_1h` | ETHUSDT 1h | hourly | ≥ 4 years |
| `eth_1d` | ETHUSDT 1d | daily | full history |
| `spy_1d` | SPY | daily | full history (1993–) |
| `qqq_1d` | QQQ | daily | full history (1999–) |

Intraday **equities** are absent because no free, honest intraday equity tape exists at fetchable quality; crypto carries the intraday axis. State this in the README rather than faking it with resampled dailies.

**4.2 Per cell**: run the v3 walk-forward (K contiguous windows, evolve on k, certify champions on k+1 by frozen solo probe; leakage = machinery FAIL) on the cell's tape **minus the holdout (§6)**, seeds `[42, 7, 2026]`, then judge the pooled certified out-of-sample cash three ways: vs initial, vs same-tape B&H, vs `spx_over` the same calendar window → the §2.3 cell verdict per seed. K defaults: 4 (daily/hourly), 3 (minute/second — short spans).

**4.3 Operationally** the grid is what every v2/v3 experiment already is: per-seed incremental output, resumable `--workdir`, `--parallel` fan-out per (cell, seed), digest-pinned tapes (a changed tape refuses), one record per cell plus a grid summary record ending with the frontier table: cell × {OOS annualized, same-tape B&H annualized, SPY annualized, verdict}.

**4.4 Runtime honesty.** The grid must finish on the reference laptop overnight. If a cell cannot (1s × a year never will), shrink the *span*, never the honesty: shorter spans stay legal because projections are labeled (v3 §2.3). Record actual wall time per cell (the footer already does).

---

## 5. The Cost Arm — Locating the Friction Frontier

The v3 minute evidence says friction, not signal, sets the frequency limit. Measure that directly:

**5.1** After the base grid, re-run the **fastest cell that went NO-EDGE** at three venue-cost levels: `taker 10/spread 2` (base, already run), `taker 2/spread 1` (aggressive maker-ish), `taker 0/spread 0` (frictionless counterfactual). Same seeds, same tape, same windows.

**5.2** The record must label the cheap arms **counterfactual — no retail venue offers this**; their purpose is to separate "the signal is too weak" from "the signal exists but friction eats it". If even the frictionless arm is NO-EDGE, the cadence's signal is dead, full stop, and the frontier table says so.

---

## 6. The Holdout — One Shot, Never Reused

The grid is a search over ~8 cells × 3 seeds × K windows: enough trials to find a lucky cell by chance. The defense:

**6.1** Before ANY grid run, the final **20 % of every tape (by row count, min 30 days by wall time where the tape allows)** is split off as the holdout and written beside the tape (`data/holdout/<cell>.csv`). No grid cell, no cost arm, no certification probe may read it. Enforcement is mechanical: the grid driver slices the tape *before* windowing and records both digests; a holdout digest appearing in any evolve/certify config is a machinery FAIL (same date-interval enforcement as v3 §7.2/#72).

**6.2** After the grid and cost arm complete, the single best cell by OOS annualized delta vs SPY runs **once**: evolve on the last pre-holdout window, certify the resulting candidates on the holdout (frozen solo probes, full base venue costs — the cost arm never touches the holdout). The certified pooled result vs `spx_over(holdout)` is **the v4 headline**: `HOLDOUT BEATS-SPX` or `HOLDOUT NO-EDGE`, one line, one run, no reruns. Rerunning the holdout with a different cell after seeing the result is data snooping and is forbidden — a second look requires a new, later holdout that postdates this spec's data end.

**6.3** Champions that certify on the holdout are admitted to the bank with full provenance; they are the only v4 genomes allowed the `holdout` provenance tag.

---

## 7. Data

Fetched by the existing `tools/` fetchers (the only network code), digests recorded in the grid records: BTCUSDT 1s/1m/1h/1d and ETHUSDT 1h/1d from the Binance public mirror; SPY (refresh) and QQQ dailies from the existing daily fetcher. Big tapes are **not committed** (gitignore `data/*_big.csv` naming or explicit paths); committed fixtures keep tests offline. Every tape's digest + span + row count goes in the grid summary record — a result that can't name its tape is not evidence.

---

## 8. Reporting

- `records/experiments/frequency_grid_*` per cell + one summary with the frontier table (§4.3).
- README: a "The frequency frontier" section stating the measured answer and the honest caveats (intraday equities absence, projections, counterfactual cost arms).
- DECISIONS from #80: every judgment call, and a final entry with the v4 acceptance status.

---

## 9. Build Order

1. **Data**: fetch all §7 tapes; record digests/spans; carve holdouts (§6.1).
2. **Profiles**: `experiments/profiles.py` + walk-forward re-base (§3); tests: registry validity, byte-identical daily/minute configs, hourly/second configs validate & run a fixture window.
3. **Yardstick**: `experiments/yardstick.py` (§2); tests: known-window SPY B&H values, coverage warnings, the forbidden-comparison guard.
4. **Grid driver**: `experiments/frequency_grid.py` (§4 + §6.1 slicing); tests: manifest, holdout slicing digests, cell verdict logic on synthetic numbers, tiny offline fixture cell end-to-end.
5. **Run the grid** (§4), then the **cost arm** (§5) — background, incremental, per-cell records as they land.
6. **Holdout shot** (§6.2) — once.
7. README + DECISIONS + full suite green + commit.

---

## 10. Acceptance

1. Full test suite green (198 existing + new); no v1–v3 regression.
2. Every §4.1 cell has a record with tape digest, span, wall, per-seed verdicts, and the three-way benchmark footer; the summary record has the complete frontier table.
3. Cost arm recorded with counterfactual labeling (§5.2).
4. The holdout ran exactly once and its verdict — whichever way it points — is the recorded headline.
5. **Not required: BEATS-SPX.** Required: the measurement, per cell, honestly labeled. If the frontier says "nothing beats the S&P at retail costs", that sentence, with numbers, *is* the deliverable — it is the map that stops the operator losing real money to a hypothesis the data already killed.

---

## 11. Red Lines (unchanged from v3 §11, restated because they are load-bearing)

Virtual money only. No order-placement code anywhere in the repository. No self-modification: genomes are parameter dicts; the bank stores parameter dicts, never code; a running colony never reads the live bank. The core makes no network calls; `tools/` only reads public market data. Any violation is a build failure regardless of what it earns.

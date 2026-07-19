# DARWIN-WALLET — Build Specification v3.0
### The Profitmaker Economy

**You are upgrading an existing, working repository** (built to BUILD_SPEC_V2.md; 158 tests green; DECISIONS.md has 62 entries; `colony/` is 3,169 non-blank lines). Do not start from scratch. v1, v2 and DECISIONS.md remain in force except where this document amends them — where they conflict, **this document wins**.

**Target, in one sentence:** a colony that finds profitmakers, proves them on data their lineage never saw, banks them, and reuses them — compounding audited cash against an honest buy-and-hold benchmark, so every verdict answers "did this beat just holding the asset?"

**Why v3 exists — the measured gap.** v2 proved the machinery: byte-identical resume, replay-twin audits, 675 ticks/s, honest venue costs, a colony that re-speciates on a regime flip (+38 to +85 pt strategy shift, all seeds). But the economics are flat: the only real-market audited result is **+10.8% over 33.5 years of SPY (+0.31%/yr) vs +8.82%/yr for buy-and-hold of the same tape**. Three causes, all structural, all fixed here:

1. **Extraction doesn't compound.** Profit lands in the treasury and sits as cash forever. 33 years of extraction at fixed capitalization *cannot* beat an index that compounds.
2. **Profitmakers are discarded.** Every run ends with a terminal audit that liquidates the winners and throws their genomes away. The colony re-learns the same lessons from random gen-0 every single run. Reuse is the cheapest alpha in the system and v2 has none.
3. **Nothing is benchmarked.** v2 verdicts compare audited cash to *initial* — a bar that a savings account clears. Without buy-and-hold at the same venue costs on the same tape, "PASS" is not a claim about edge.

**Engineering principles carry over unchanged**: stdlib-only core, pytest the only dev dependency, integer µ$ money, one RNG, determinism, small boring functions, crash on invariant violation. New ceiling for `colony/`: **~3,600 non-blank lines** (v2 landed 3,169, accepted in DECISIONS #61; v3 adds ~2 small modules). Same rule: if a module fights the ceiling, simplify it — and prefer reusing existing machinery (the probe harness, the immigration path, the terminal audit) over building parallel machinery. Nothing in v3 needs threads, new schemas beyond what is specified, or a new storage engine.

---

## 0. How to Use This Document

1. Read DECISIONS.md #33–#62 and this spec end to end before writing code.
2. Build in the §9 order; each stage's tests green before the next.
3. Every judgment call goes in DECISIONS.md, numbering continued from #62.
4. All v1 and v2 acceptance criteria must STILL pass after v3 lands, re-based where §2 changes verdict wording — regression is failure.

---

## 1. Ratifications

DECISIONS #33–#62 are promoted to spec law en bloc: the daemon supervisor and pid-liveness mechanics, health sidecar, gap-counting semantics, the audit unit (fully-consumed closed segment), MASK24 gene mechanics, gates-block-opens-only, the pruned-on-use fill window, the immigration token bucket starting full, per-arena repro bars, the SSE design, bucketing envelopes, the ledger-live Money Strip, the machinery-vs-economics verdict split (#7.4 of v2), and the accepted line budget (#61). Cite the DECISIONS number in code comments where the behavior lives. The operator runbook items in #62 (year-long minute ladder, 24 h soak, CI matrix) remain outstanding acceptance evidence for v2 and are folded into §10 here.

One v2 behavior is **amended**: verdict vocabulary. "PASS" alone is retired for replay experiments; §2.4 defines the replacement tiers.

---

## 2. The Benchmark — Honesty About the Bar

**2.1 `colony/benchmark.py` (new, small — target ≤80 lines).** One function is the heart of it:

```
buy_and_hold(prices, capital_u, venue, lot_denominator) -> audited cash_u
```

Buy the maximum affordable lots at the FIRST bar (taker fee + spread charged, rounded against the buyer, same `risk` helpers agents use — no private math), hold, sell everything at the LAST bar (same costs), return final cash. Leftover cash rides along uninvested, exactly as an agent's would. This is the same honest venue the colony trades — the benchmark pays the same tolls.

A second trivial function, `cash(capital_u) -> capital_u`, names the do-nothing benchmark explicitly so reports can print both.

**2.2 Every replay record states its real-life terms.** The `Record` footer for any replay run/experiment gains four mandatory lines, computed from the tape's timestamps:

- **span**: first bar UTC → last bar UTC, and the span in years (365.25-day years, 2 decimals)
- **wall**: the experiment's own wall-clock runtime in seconds (measured in `Record.finish`)
- **annualized**: colony audited cash as CAGR over the span
- **benchmark**: buy-and-hold audited cash, its CAGR, and the delta in %-points/yr

This permanently answers "how long did it take, what does it make yearly, does it beat the index" — from the record file, no analyst required.

**2.3 Sub-year tapes project, and say so.** When span < 1 year the CAGR line prints with the suffix `(projected)`. Never suppress it; never present a projection without the suffix.

**2.4 Verdict tiers** (replaces bare PASS for all replay experiments; machinery FAIL is unchanged and still the only *failure*):

| Tier | Meaning |
|---|---|
| `ALPHA` | audited cash > buy-and-hold cash on the same tape at the same costs |
| `CASH` | audited cash > initial, but ≤ buy-and-hold — real profit, no edge over holding |
| `EXPECTED-FAIL` | audited cash ≤ initial; machinery sound; per-seed economics recorded (v2 §7.4) |
| `FAIL` | the machinery broke: crash, invariant violation, incomplete replay |

`real_market.py` and `minute_ladder.py` are re-based onto these tiers. Their v2 pass bars map to `CASH`; nothing that passed v2 may regress below its v2 tier. The v3 ambition is `ALPHA`, and §10 is honest about not demanding the sign — it demands the measurement.

---

## 3. Profitmaker Accounting — Who Actually Made Money

Reuse requires knowing who to reuse, from the ledger, not from fitness heuristics.

**3.1 Realized per-agent stats are computed, not stored.** No schema change: at terminal audit (`wind_down`) and on demand (`colony inspect`, bank admission), derive from the ledger and `trades`:

- `realized_pnl_u`: (everything the agent's account received) − (everything it was funded with), over its life — i.e. net ledger flow excluding the initial seed, so fees, spread, and rent are already inside it
- `fees_u`, `fills`, `active_days` (first fill → last fill, wall time)
- `realized_bps_per_day`: realized_pnl over seed, per active day — the ranking number

One function in `colony/report.py` (`agent_realized(con, agent_id)`), used by everything that needs it. If the existing fitness function disagrees with realized P&L about who the winners are, realized P&L wins for banking purposes — fitness remains untouched for breeding (do not re-tune evolution in v3).

**3.2 The sitter stays**, and so does the honesty rule: if sitters dominate realized P&L at some cadence, that surfaces in records as a finding about venue costs.

---

## 4. The Genome Bank — Profitmakers Persist

The core new deliverable. `colony/bank.py` (new, target ≤150 lines) plus a `colony bank` CLI verb group.

**4.1 Storage: one append-only JSONL event log**, `bank/bank.jsonl` at the repo root (path configurable). Events, one JSON object per line: `admit`, `certify`, `lapse`. Current state = fold the log (last status wins per genome). No database, no locking beyond append+flush — single-writer by convention (CLI and experiment drivers; the daemon never writes the bank). Each genome is identified by `genome_hash` = sha256 of its canonical JSON (sorted keys) — dedup is by hash.

**4.2 An `admit` event carries full provenance** — a banked genome without provenance is inadmissible:

```json
{"event": "admit", "utc": "...", "genome_hash": "...", "genome": {...},
 "source": {"arena": "spy_d", "tape_digest": "...", "window": ["1993-01-29", "2005-06-30"],
            "config_seed": 42, "run_db": "...", "agent_id": "000123"},
 "audited": {"realized_pnl_u": ..., "realized_bps_per_day": ..., "fills": ..., "fees_u": ..., "active_days": ...}}
```

**4.3 Admission rule** (automatic at every terminal audit, and available as `colony bank admit --db X`): among agents with `realized_pnl_u > 0` and `fills ≥ bank_min_fills` (config, default 20), rank by `realized_bps_per_day`, admit the top `bank_admit_top_k` (default 8), skipping hashes already in the bank. Admission is **in-sample by definition** — the genome profited on the data it evolved on. That earns `candidate` status, nothing more.

**4.4 Certification is out-of-sample, and it is the entire point.** `colony bank certify --tape CSV [--from DATE]` runs every `candidate` genome as a frozen solo probe (reuse the in-memory profit-matrix harness, DECISIONS #16 — no colony, no evolution, no new machinery) over a tape window that **postdates the genome's admission window** (enforced by comparing window end vs probe start; overlapping windows refuse). Probe profitable after full venue costs → `certify` event (with the probe's own provenance + audited numbers). Probe unprofitable → `lapse` event. Certified genomes that later fail a re-certification probe also lapse. Lapsed genomes stay in the log forever (they are data) but are never drawn again.

**4.5 CLI**: `colony bank list` (status, realized bps/day in- and out-of-sample, provenance one-liner), `colony bank show <hash-prefix>`, `colony bank admit`, `colony bank certify`. Every admit/certify/lapse also writes a record in `records/bank/`.

**4.6 Red-line note: the bank stores genomes (parameter dicts), never code.** Nothing executable enters or leaves it. Self-modification stays impossible by construction.

---

## 5. Reuse — Champions Rejoin the Colony

**5.1 Bank snapshot at init, for determinism.** `colony init` copies the certified set (hash, genome, provenance summary) into a new `bank_snapshot` table in the colony db. A run **never** reads the live bank file after init — draws come from the snapshot, so the same db resumes and replay-twins byte-identically with zero new audit machinery. Refreshing champions into a running colony = start a new colony (state this in the README; the daemon's audit guarantee is worth more than hot-reload convenience).

**5.2 Immigration draws from the bank.** New config `bank_immigrant_share_bps` (default 5,000). At each immigration event (existing token-bucket path, unchanged): with probability share, clone a uniformly-drawn snapshot genome (colony RNG — determinism preserved) as the immigrant's genome, **unmutated** (pure reuse; diversity is the other half's job); otherwise draw random gen-0 exactly as v2. Empty snapshot → always random. The immigrant is a new agent (fresh id, generation 0) whose row records `origin = bank:<hash-prefix>` so lineage and the dashboard can show it.

**5.3 Champions are funded like the proven assets they are.** `champion_seed_multiple` (config, default 2, bounds 1–10): bank-sourced immigrants receive `gen0_seed_u × multiple`, spending that many tokens from the same immigration bucket. Everything downstream (repayment quota, estate return, mitosis) is unchanged — reuse rides the existing rails.

**5.4 Compounding: the treasury redeploys surplus.** The v2 treasury hoards; the v3 treasury reinvests, conservatively and by rule:

- Track `treasury_high_water_u` in run state (starts at initial capitalization).
- At each UTC-day boundary: if treasury > high-water, move `(treasury − high_water) × reinvest_fraction_bps / 10⁴` (config, default 5,000) of headroom into the immigration token bucket — capped so the bucket never exceeds `4 ×` its base capacity — then raise the high-water to the current treasury.

No new accounts, no new ledger rows (tokens are budget, not money; the money moves only when an immigrant is actually seeded, through the existing path). Drawdowns below high-water redeploy nothing — the ratchet only turns one way, so a losing colony cannot chain-refill itself. This is the whole §5.4; resist making it cleverer.

---

## 6. Widening the Search — One New Archetype

Exactly one, chosen because 33 years of SPY and every BTC tape reward it and no current archetype expresses it: **`breakout`** — enter when price makes a new `lookback`-bar high by ≥ `confirm_bps`; exit when price falls `trail_bps` from the post-entry high (or `hold_max`). Params and bounds join `PARAM_BOUNDS`; the three touch points remain `ARCHETYPES`, `PARAM_BOUNDS`, `decide` (LEARNINGS §4.4); the three v2 gate genes apply to it like every trading archetype. Gen-0 round-robin now cycles four archetypes; the sitter control group stays. Petri regression: with the new archetype present, the v1/v2 flagship regime-flip must still PASS (the population may now include breakout agents — the acceptance metric is unchanged: mean-revert share shift ≥ +20 pts and positive wealth/treasury).

No other genome changes. No short-selling, no leverage, no position sizing models — long-only lots, as ever. If breakout earns nothing, that is a recorded finding, not a reason to add a fifth archetype in this version.

---

## 7. Experiments v3

All v2 experiment law carries over: per-seed, incremental, resumable via `--workdir`, digest-pinned tapes, terminal audits, subprocess `--parallel`, records with the §2.2 footer.

**7.1 The v3 flagship: walk-forward.** `experiments/walk_forward.py` — the experiment that decides whether profitmakers are real or overfit:

- Split a tape into K contiguous windows (`--windows`, default 4; SPY dailies → ~8-year windows, BTC 1m year → ~13-week windows).
- For k = 1…K−1: **evolve** a colony on window k (fresh init, bank snapshot = everything certified from windows < k), terminal audit, auto-admit per §4.3; then **certify** all candidates by frozen probe on window k+1 (§4.4).
- Record per window: candidates admitted, certified vs lapsed, and the certified set's out-of-sample realized bps/day vs window k+1's buy-and-hold.
- Final verdict: `EDGE` if the certified champions' pooled out-of-sample result beats buy-and-hold across a majority of test windows, else `NO-EDGE` — both are acceptance-passing outcomes for the machinery; the record must state which occurred, per seed. Overlapping-window leakage is a machinery FAIL (the §4.4 refusal must make it impossible).

**7.2 The reuse A/B.** `experiments/bank_reuse.py`: on a held-out tape window (one no banked genome's provenance touches — enforced), run seed-matched pairs of colonies: **A** random gen-0 (bank_immigrant_share 0) vs **B** bank-enabled (share 5,000, the walk-forward's certified bank). Record, per seed pair: audited cash, CAGR, benchmark delta, and time-to-first-treasury-surplus. Direction expected: B ≥ A. Direction demanded: none — the measurement is the deliverable, and a B < A result must be recorded with the same prominence.

**7.3 Re-based ladders.** `real_market.py` and `minute_ladder.py` adopt §2 tiers and footers. The minute ladder's full-year run and the 24 h soak remain the operator-time items they were in DECISIONS #62 — unchanged mechanics, new verdict vocabulary.

---

## 8. Observatory v3 — Show the Edge, Show the Champions

Keep everything from v2. Three additions, smallest first:

1. **The Money Strip gains the benchmark.** One more figure group: **vs B&H** — colony audited cash minus buy-and-hold cash over the same span (replay: whole tape; live: since colony genesis, benchmark computed from the journal). Signed, colored, allowed to be red — a red delta on a green treasury is the honest picture of `CASH`-tier performance and must render exactly that way.
2. **`/api/bank` + a Champions panel**: the snapshot the colony was initialized with — hash-prefix, archetype, certified out-of-sample bps/day, provenance one-liner, and how many currently-living agents descend from each (via `origin`). Bank-origin agents get a small badge in the leaderboard and inspector.
3. **Annualized, everywhere a total appears.** Any figure presented as "since genesis" carries its CAGR beside it, `(projected)` under one year (§2.3). Tick numbers stay banished.

No new libraries, no new panels beyond these, SSE and bucketing untouched.

---

## 9. Build Order (tests green before advancing)

1. **Benchmark & honest records** — `benchmark.py`, §2.2 footers (span/wall/annualized/benchmark) in `Record`, §2.4 tiers wired into `real_market.py` + `minute_ladder.py`; re-run both on committed fixtures and record the new baselines.
2. **Profitmaker accounting** — `agent_realized()` from ledger + trades; surfaced in `colony inspect` and wind_down output; property test: sum of per-agent realized P&L + treasury flows reconciles with conservation.
3. **The bank** — `bank.py` events/fold/hashing, admission at terminal audit, CLI verbs, `records/bank/`; probe-based certification with the postdating refusal.
4. **Reuse** — `bank_snapshot` at init, bank-sourced immigration with `origin`, champion seed multiple; determinism test: same snapshot + seed ⇒ byte-identical ledgers; replay-twin test green with bank immigrants in the tape.
5. **Compounding** — high-water ratchet, day-boundary redeploy into the token bucket with the 4× cap; tests: surplus redeploys, drawdown doesn't, conservation exact throughout.
6. **Breakout archetype** — params/bounds/decide; profit-matrix gains a breakout row (record the baseline); Petri regime-flip regression green.
7. **Walk-forward** — `walk_forward.py` on the SPY tape and the BTC fixture; leakage refusal tested.
8. **Reuse A/B** — `bank_reuse.py`, seed-paired, held-out-window enforcement tested.
9. **Observatory v3 + polish** — vs-B&H strip figure, `/api/bank`, champions panel, CAGR rendering; README v3; DECISIONS entries for every call; line-count check against the ~3,600 ceiling.

---

## 10. Acceptance Criteria (v3 is DONE when all hold, plus all v1+v2 criteria re-based)

**10.1 No unbenchmarked claims.** Every replay record carries span, wall-clock runtime, CAGR (with `(projected)` where due), and buy-and-hold at identical venue costs; every verdict is a §2.4 tier; no bare "PASS" remains in any replay experiment's output.

**10.2 Walk-forward completes** on 33 years of SPY dailies and ≥1 year of BTC 1m: zero machinery failures, per-window admitted/certified/lapsed counts and out-of-sample economics recorded per seed, and an honest `EDGE`/`NO-EDGE` verdict. Leakage (any probe overlapping its genome's training window) is impossible, proven by a test that tries.

**10.3 Reuse is measured.** The A/B records seed-paired results on a held-out window with the §2.2 footers. Direction is reported, not required — but a silent or missing B-worse-than-A result is an acceptance failure.

**10.4 Compounding machinery works and cannot run away**: surplus above high-water redeploys at the configured fraction; the bucket cap holds; drawdowns redeploy nothing; conservation is exact through all of it; the ratchet state survives hard-kill resume byte-identically.

**10.5 Determinism with reuse**: same config + seed + bank snapshot ⇒ byte-identical ledger; the daemon's replay-twin audit passes on a session that included bank immigrants.

**10.6 Honesty checks**: the sitter still exists and reports; lapsed champions remain visible in `bank list`; the dashboard renders a red vs-B&H delta without euphemism; every `EXPECTED-FAIL` and `NO-EDGE` cites its record path.

**10.7 The v2 runbook closes**: the year-long minute ladder (new tiers), the 24 h live soak, and a green Windows+Linux CI matrix are recorded — these were owed from DECISIONS #62 and v3 does not ship without them.

---

## 11. Red Lines (unchanged, restated because the target moved again)

Banking and reusing profitmakers changes nothing here: **virtual money only; no order-placement code anywhere in the repository; no self-modification — the bank stores parameter dicts, never code; conservation checked continuously with crash-on-violation; every session leaves a journal that replays byte-identically.** A certified champion is a measurement, not a mandate: pointing any of this at real capital remains a separate future project with its own spec, safeguards, and legal review — not a config change, and not a bank export.

---

*End of specification. The colony can survive anything; now make it worth keeping — find the profitmakers, prove them on tomorrow's data, and never throw them away again.*

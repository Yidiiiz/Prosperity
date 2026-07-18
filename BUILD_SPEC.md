# DARWIN-WALLET — Build Specification v1.0

**You are building:** an evolutionary colony of autonomous agents, each with a strictly isolated wallet. Agents act in a market arena; profitable agents reproduce (funding their children from their own profits, genetic-algorithm style); unprofitable agents go bankrupt and die. The colony evolves toward profit with no human tuning.

**This document is the complete, authoritative specification, and its parameters are empirically validated** — a reference prototype implementing this exact design was run through the acceptance experiments (5 RNG seeds) before this spec was finalized; every rule marked VALIDATED exists because the naive alternative was tested and failed, and the seed-repayment quota (§3.14) was additionally REPLICATED in a second, independently written prototype before adoption. A copy of that prototype ships in the repo root as `reference_prototype.py` for consultation only — **the real build follows this spec's architecture (SQLite ledger, modules, tests), not the prototype's in-memory shortcuts.** Build exactly what it says. Where it is silent, choose the *simplest* correct option and document the choice in `DECISIONS.md`.

---

## 0. How to Use This Document

1. Read the whole spec before writing code.
2. Build in the order given in §12 (Build Order). Each step has acceptance tests — do not proceed until they pass.
3. Keep a running `DECISIONS.md` for any judgment calls.
4. Commit small and often with clear messages. Set up the repo (README, `.gitignore`, `pyproject.toml`, tests) as step zero.

---

## 1. Scope

### v1 (this build) — Simulation only
- Discrete-time simulation, single process, single machine.
- Virtual money (integer cents in a double-entry ledger). **No real money, no exchange APIs, no network calls.**
- Parameter-driven strategy agents (no LLM calls in v1 — the cognition layer is a v2 plug-in point; leave a clean interface).
- One synthetic arena ("Petri Dish") with scriptable market regimes.
- Full genetic machinery: selection, crossover, mutation, elitism.
- Deterministic and reproducible: same config + same RNG seed ⇒ byte-identical results.

### Explicit non-goals for v1
- No containers, no Redis, no async, no ORM, no microservices, and no frontend build tooling (no node, npm, React, bundlers). The Observatory dashboard (§9) IS in scope — but as a single static HTML file served by the stdlib HTTP server, with exactly one external asset (Chart.js from a CDN, tables degrade gracefully without it).
- No real trading venues, no wallets on real payment rails, no LLM API usage.
- No self-modifying agents. Genomes change **only** between generations via the orchestrator.

The point of v1 is to prove the evolutionary machinery is correct and the accounting is airtight. Everything risky comes later, behind the interfaces defined here.

---

## 2. Engineering Principles (non-negotiable)

1. **Simple code.** Python 3.11+, standard library only for the core (`sqlite3`, `dataclasses`, `random`, `math`, `json`, `argparse`, `statistics`). Dev-dependency: `pytest`. Nothing else without a written reason in `DECISIONS.md`. One sanctioned exception lives entirely inside `colony/web/dashboard.html`: a `<script>` tag loading Chart.js from cdnjs — it is not a Python dependency, requires no build step, and the dashboard must still render all numbers and tables if the CDN is unreachable.
2. **Money is integer cents.** Never floats for cash. Asset quantities are integer *lots*; prices are integer cents per lot. All equity math stays in integers.
3. **Double-entry or it didn't happen.** Every movement of money is one ledger row with a debit account and a credit account. There is no other way money moves. Balances are derived from the ledger (with a cached materialized balance table that is *verified* against the ledger).
4. **One RNG.** A single `random.Random(seed)` instance owned by the orchestrator, passed explicitly to anything that needs randomness. No module-level `random` calls anywhere.
5. **Determinism.** Iterate agents in sorted-id order every tick. No wall-clock time, no dict-ordering dependence, no threads.
6. **Small functions, no cleverness.** Prefer boring, readable code over abstractions. No metaclasses, no decorators beyond stdlib, no plugin frameworks — the "pluggable arena" is just a Python class implementing a 3-method interface.
7. **Crash on invariant violation.** If conservation of money (§4.4) fails, raise immediately and halt the simulation. Never paper over accounting drift.

---

## 3. Corrected Design Decisions (read carefully — these fix subtle bugs)

These decisions resolve ambiguities and bugs that naive implementations of this idea hit. Follow them exactly.

**3.1 — Cash vs. equity.** An agent has *cash* (ledger balance) and *positions* (asset lots). Its **equity = cash + mark-to-market value of positions** at the current tick price.
- **Death** is decided on *equity*, not cash: `equity ≤ death_floor` ⇒ die.
- **Rent** is paid in *cash*. If cash < rent due, the agent force-liquidates positions (at current price, paying fees) until it can pay; if it still can't, it dies this tick.
- **Breeding** requires the child's seed to be available in *cash* (parents don't liquidate to breed; if cash is insufficient, the agent simply isn't breed-eligible this tick, even if equity is high).

**3.2 — Where trading P&L comes from (the conservation fix).** The arena is modeled as an external counterparty account `ARENA:petri`. When an agent buys, cash flows agent → arena; when it sells, arena → agent. The arena account may go arbitrarily negative or positive — it represents "the market." Conservation (§4.4) sums over *all* accounts including the arena, so the books always balance to the initial capitalization. Do **not** try to make agent-vs-agent trading zero-sum in v1; agents trade only against the arena at the scripted price ± fees.

**3.3 — Breeding is a queue, not a race.** When an agent's *cash* ≥ `repro_multiple × baseline` (the mitosis trigger, §3.4) and its cooldown has elapsed, it enters the **breeding queue**. Each tick, the matchmaker:
- pairs queued agents two at a time for **crossover** (pair selection weighted by fitness, §7.3);
- any agent left waiting in the queue for `solo_breed_patience` consecutive ticks reproduces **asexually** (clone + mutate).
This removes the race condition of "two agents crossing the threshold in the same window" and guarantees rich loners still reproduce.

**3.4 — Reproduction is mitosis, measured against a moving baseline (VALIDATED — the absolute-threshold version fails).** Each agent tracks a `baseline_cents`, set to its seed at birth and **reset to its post-split cash after every breeding event**. The breeding trigger is `cash ≥ repro_multiple × baseline` (relative, scale-free). Absolute cash thresholds were tested and fail: children start smaller than parents, so each generation must climb further to a fixed bar and lineages stall into extinction. With the relative rule, lineages compound generation after generation at any wealth scale.
- **Asexual split:** child seed = `floor(child_seed_fraction × cash)` (the parent's own gene); parent's baseline resets to its remaining cash.
- **Crossover split:** each parent contributes `floor(its_own_child_seed_fraction / 2 × its own cash)`; the child's seed is the sum; both parents reset baselines to their remaining cash.
- **Hard gates** (skip the attempt, stay in queue, if violated): each funder retains cash ≥ `reserve_floor_cents`; child's total seed > `2 × death_floor_cents` (no child is born dying).
- The transfer(s), baseline resets, and agent-row insert happen in **one SQLite transaction**.

**3.5 — Fitness ≠ survival.** Survival and the *right* to breed are purely economic (equity/cash rules above). Fitness — a risk-adjusted score — is used only for (a) choosing crossover partners and (b) the hall of fame. Definition:
```
growth  = ln(max(equity_now, 1) / max(first_snapshot_equity, 1)) / age_ticks   # log-growth per tick
max_dd  = clamp(1 − equity_now / peak_equity, 0, 0.99)                          # lifetime max drawdown vs peak
fitness = growth × (1 − max_dd)
```
Track `peak_equity` per agent (updated each tick). Compute from snapshots + current state; floats are fine here (fitness never touches the ledger). An agent needs age ≥ `max(min_ticks_for_fitness, 3 × snapshot_every)` ticks; before that its fitness is 0. This directly optimizes compounding speed — the "earn as much as possible" objective — while the drawdown penalty This selects for *repeatable* earners over lucky gamblers.

**3.6 — Anti-degenerate safeguards (evolution WILL try these; each rule below was empirically necessary).**
- *Do-nothing survival*: **proportional rent** each tick, `max(rent_min_cents, equity × rent_bps / 10000)` — proportional so selection pressure stays constant as the colony gets rich (fixed rent becomes negligible and evolution stalls). Rent must be well below the achievable earn rate or the whole colony starves (validated failure mode: 3bps rent vs ~2bps achievable earnings = guaranteed extinction).
- *Stagnation cull applies ONLY to never-traders*: an agent that has **never traded** by age `stagnation_ticks` dies (kills sitters and broken genomes). An agent that has traded even once is exempt — holding a position counts as economic activity, and patient specialists waiting for their regime must NOT be culled (validated failure mode: a 200-tick blanket stagnation rule executed agents whose indicators hadn't even warmed up, and later killed the exact reserve diversity needed at regime changes). `stagnation_ticks` must exceed the max `lookback` bound. Senescence handles long-term freeloaders.
- *Bet-it-all gambling*: per-action cap — a single order may not commit more than `max_action_fraction` of current equity. Enforced by the risk engine, never trusted to the genome.
- *Immortal squatters*: `max_age_ticks` senescence — die of old age, residue to treasury.
- *Exploiting sim bugs*: the conservation invariant (§4.4) runs every tick in debug mode, every 100 ticks otherwise; any violation halts the run.

**3.7 — Genome bounds are clamped.** Every numeric gene has a hard [min, max] in a bounds table (§6). Mutation output is clamped. Crossover blends stay in bounds automatically. This prevents mutation from producing nonsense (negative thresholds, >100% risk).

**3.8 — Only equity decisions use mark-to-market; the ledger never does.** Ledger rows record only actual cash movements (trades, rent, seeds, residue). Mark-to-market equity is computed on the fly from `positions × current_price` and stored in periodic *snapshots* for analysis — it is never a ledger entry.

**3.9 — Death is a full liquidation.** On death (any cause): sell all positions to the arena at current price (fees apply), then transfer all remaining cash to `TREASURY` with memo `death_residue:<cause>`. Agent row gets `died_tick` and `death_cause`. Its genome and stats are archived (Fossil Record, §9).

**3.10 — Gen-0 and immigrants are the only house-funded agents (see 3.12).** Treasury funds gen-0 seeds. After that, every cent of every child's seed comes from parents. Total agent wealth can therefore only grow if the colony out-earns rent + fees — by construction — with one deliberate exception, §3.12, which only ever recycles money the colony already returned to the treasury.

**3.11 — Lot granularity can silently kill the colony (VALIDATED).** Order size is `lots = floor(budget / price)`; if price per lot is large relative to seeds, small-`risk_fraction` agents can never afford one lot (they starve with perfect signals) and everyone else suffers huge rounding losses. **Hard config constraint (enforced at load): `gen0_seed_cents ≥ 200 × start_price_cents`.** Use `start_price_cents = 200` with a price floor of 20. If long trends inflate the price enough to threaten this ratio at current equity levels, log a warning in reports.

**3.12 — Immigration makes extinction impossible and keeps capital constantly deployed (VALIDATED — required for regime-change survival).** In the breeding phase, after births: while `living population < population_floor` and `treasury ≥ gen0_seed_cents`, the treasury spawns an **immigrant**: with probability 0.4 a mutated copy (double sigma) of a random **hall-of-fame** genome, else a fresh random genome. This is the standard genetic-algorithm "random restart," funded exclusively by recycled colony money (rent + death residues) — and every immigrant additionally carries the scoped seed-repayment quota (`repay_multiple × seed`, §3.14), typically repaid within its first profitable stretch; whatever remains returns as death residue within `max_age_ticks` regardless. Without it, a regime change that bankrupts the dominant archetype ends the colony; with it, the colony re-explores and re-conquers. It also serves the earning mandate: treasury cash is otherwise idle capital.

**3.13 — The hall of fame is a ROLLING window (VALIDATED — a frozen hall poisons adaptation).** When an agent whose peak equity reached ≥ 2× its birth seed dies, append its genome to the hall; keep only the most recent `hall_size` (100) entries. A hall that stops updating keeps resurrecting genomes from a dead regime and measurably delays adaptation after regime changes.

**3.14 — THE QUOTA IS SENESCENCE (VALIDATED — both mid-life quota designs were built, tested, and removed).** The design goal: the treasury (the "original wallet") must be what actually grows — the withdrawable-profit accumulator — while agents hold only circulating working capital. Two mid-life repayment mechanisms were implemented and swept empirically: (a) a harvest tax on profit at each breeding event, and (b) seed-debt repayment by house-funded agents. **Both lost, decisively, to the mechanism the system already has:**

| Quota design | Treasury growth | System wealth | Adaptation (min) |
|---|---|---|---|
| **None — recycle at death only** | **+31.3%** | **+44.6%** | **+66p** |
| Seed-debt repayment (50% of mitosis profit) | +19.5% | +29.5% | +58p |
| Debt + 10% harvest tax | +17.7% | +28.7% | +24p |
| Debt + 15% harvest tax | +10.9% | +22.1% | +17p (FAILS) |

Why: any mid-life quota fires at the worst possible moment — at a breeding event, right before the split — shrinking child seeds, causing births to fail the viability gate, and propagating the loss through population growth itself. Meanwhile **senescence + death residue already constitute a deferred 100% quota**: every agent returns its entire wealth (seed + all gains) to the treasury within `max_age_ticks`, guaranteed, and taxing wealth *after* it has finished compounding is strictly cheaper than taxing it mid-flight. Measured at population scale, the death-residue stream is also just as smooth as a continuous harvest (staggered ages ⇒ staggered inflows; identical trajectory shapes, same negative-window count).

**Therefore, binding rules:**
- Do NOT implement any mid-life quota, tithe, harvest tax, or seed-debt mechanism in v1. The quota channels are: rent (per tick) + death residue (per lifetime). Nothing else moves agent money to the treasury.
- `max_age_ticks` IS the quota dial: shorter lives = faster treasury repayment cadence, at a compounding cost. The shipped 3,000 is validated; treat changes as validation-suite triggers.
- The treasury is still the north-star KPI and, in live mode, the only human-withdrawable account (§14).
- A small "liquidity harvest" may be justified ONLY for tiny live colonies (~10 agents) where death-timed inflows genuinely are lumpy — that is a v2 plug-in (§14) with a validated cost warning, never a v1 feature.

**THE ONE VALIDATED EXCEPTION — the scoped seed-repayment quota (REPLICATED in two independent prototype implementations; SHIP THIS):** house-funded agents (gen-0 and immigrants) owe the treasury `repay_multiple × seed` (0.15×). Each tick, `min(debt, cash − baseline)` is swept to the treasury (memo `debt_repay`); an agent must be **debt-free to enter the breeding queue**. Parent-funded children owe nothing. Debt dies with the agent — no death-time enforcement is needed because the death residue sweeps the whole estate anyway, so defaults collect by construction. Flagship results, 5 seeds, same harness as every other number in this spec:

| Variant | Min shift (bar +20) | Avg shift | Treasury | System wealth |
|---|---|---|---|---|
| No quota (control) | +52p | +72p | +32.5% | +45.1% |
| **`repay_multiple` = 0.15** | **+65p** | **+78p** | **+34.6%** | **+46.0%** |
| `repay_multiple` = 0.25 | +18p (FAILS) | +47p | +13.8% | +23.0% |

Honest reading of the evidence: at 0.15 the quota is Pareto-safe — aggregate wealth statistically unchanged (per-seed deltas straddle zero) — and it **raises the worst-seed adaptation floor** (+52p → +65p), plausibly via the immigration flywheel: immigrants repay their seed quickly, the treasury refills sooner, and immigration capacity stays high exactly when regime changes demand exploration. The dose-response cliff between 0.15 and 0.25 replicated exactly across both independent implementations and is SILENT — the colony still runs, still conserves money, and quietly stops adapting. **The config loader MUST hard-reject `repay_multiple` > 0.25, and reject values > 0.15 unless a `revalidated: true` flag is set alongside.** Why this passes while the profit-based quotas above fail: it is small, front-loaded, scoped to house money only, and taxes *eligibility timing* rather than reproductive capital — children's seeds are never touched.

---

## 4. Data Model (SQLite, single file `colony.db`)

Use raw `sqlite3` with explicit transactions (`BEGIN IMMEDIATE` … `COMMIT`). WAL mode on. No ORM.

```sql
-- Every account that can hold cash. kind: TREASURY | AGENT | ARENA
CREATE TABLE accounts (
  id   TEXT PRIMARY KEY,          -- 'TREASURY', 'ARENA:petri', 'AGENT:000042'
  kind TEXT NOT NULL
);

-- The single source of truth for money. Append-only. amount_cents > 0 always.
CREATE TABLE ledger (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,
  tick           INTEGER NOT NULL,
  debit_account  TEXT NOT NULL REFERENCES accounts(id),   -- money leaves here
  credit_account TEXT NOT NULL REFERENCES accounts(id),   -- money arrives here
  amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
  memo           TEXT NOT NULL                             -- 'seed','rent','buy','sell','fee','child_seed','immigrant_seed','debt_repay','death_residue:bankrupt',...
);

-- Cached balances, updated in the same transaction as each ledger insert.
-- Periodically verified against SUM over ledger (see invariant test).
CREATE TABLE balances (
  account_id    TEXT PRIMARY KEY REFERENCES accounts(id),
  balance_cents INTEGER NOT NULL          -- may be negative ONLY for ARENA:* accounts
);

CREATE TABLE agents (
  id           TEXT PRIMARY KEY,          -- zero-padded: '000001'
  genome_json  TEXT NOT NULL,
  generation   INTEGER NOT NULL,
  parent_a     TEXT,                      -- NULL for gen-0
  parent_b     TEXT,                      -- NULL for gen-0 and asexual births
  born_tick    INTEGER NOT NULL,
  debt_cents   INTEGER NOT NULL DEFAULT 0,   -- outstanding seed-repayment quota; house-funded agents only (§3.14)
  died_tick    INTEGER,
  death_cause  TEXT                       -- 'bankrupt','old_age','stagnation','liquidity_death'
);

CREATE TABLE positions (
  agent_id TEXT NOT NULL REFERENCES agents(id),
  asset    TEXT NOT NULL,                 -- v1: always 'SIM'
  lots     INTEGER NOT NULL,              -- integer lots, >= 0 (no shorting in v1)
  PRIMARY KEY (agent_id, asset)
);

CREATE TABLE trades (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  tick        INTEGER NOT NULL,
  agent_id    TEXT NOT NULL,
  side        TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  lots        INTEGER NOT NULL,
  price_cents INTEGER NOT NULL,           -- per lot, fee-exclusive
  fee_cents   INTEGER NOT NULL
);

-- Periodic equity snapshots for fitness & reporting (every snapshot_every ticks and at death).
CREATE TABLE snapshots (
  tick         INTEGER NOT NULL,
  agent_id     TEXT NOT NULL,
  cash_cents   INTEGER NOT NULL,
  equity_cents INTEGER NOT NULL,
  PRIMARY KEY (tick, agent_id)
);

-- Aggregate time series written every snapshot_every ticks; feeds the Observatory and records.
CREATE TABLE colony_metrics (
  tick            INTEGER PRIMARY KEY,
  treasury_cents  INTEGER NOT NULL,
  colony_wealth_cents INTEGER NOT NULL,      -- sum of living agents' equity
  arena_cents     INTEGER NOT NULL,          -- ARENA account balance (− means colony extracted money)
  population      INTEGER NOT NULL,
  births_cum      INTEGER NOT NULL,
  deaths_cum      INTEGER NOT NULL,
  price_cents     INTEGER NOT NULL,
  regime_kind     TEXT NOT NULL,
  share_momentum  REAL NOT NULL,
  share_mean_revert REAL NOT NULL,
  share_sitter    REAL NOT NULL,
  diversity       REAL NOT NULL              -- Shannon entropy over archetype + binned params
);

CREATE TABLE runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at  TEXT NOT NULL,
  config_json TEXT NOT NULL,              -- full config incl. rng seed, for reproducibility
  last_tick   INTEGER NOT NULL DEFAULT 0
);
```

### 4.1 Ledger API (the only way money moves)
```python
def transfer(con, tick, debit, credit, amount_cents, memo):
    """Single double-entry movement. Called inside an open transaction.
    Raises InsufficientFunds if debit account is an AGENT or TREASURY
    account and would go below zero. ARENA accounts may go negative."""
```

### 4.2 Buy/sell mechanics (v1, single asset 'SIM')
- Price this tick: `p` cents/lot from the arena.
- BUY n lots: cost = `n*p`, fee = `max(1, round(n*p*fee_bps/10000))`; two ledger rows: agent→arena `n*p` memo `buy`, agent→arena `fee` memo `fee`. Position += n.
- SELL n lots: proceeds = `n*p`; arena→agent `n*p` memo `sell`; agent→arena fee row. Position −= n.
- The risk engine rejects (silently logs) any order violating: cash sufficiency, `max_action_fraction`, position ≥ 0.

### 4.3 Equity
`equity(agent) = cash + lots * current_price` (integer cents).

### 4.4 THE conservation invariant
```
SUM(balances.balance_cents over ALL accounts) == initial_treasury_cents   (constant, forever)
AND for every account: balances.balance_cents == SUM(credits) − SUM(debits) from ledger
```
Implement `verify_invariants(con)`; run per-tick when `config.debug=true`, else every 100 ticks and at run end. On failure: raise `AccountingError` and halt.

---

## 5. Configuration (single `config.json`, checked into repo as `config.default.json`)

```json
{
  "rng_seed": 42,
  "debug": false,
  "initial_treasury_cents": 20000000,
  "gen0_population": 100,
  "gen0_seed_cents": 100000,
  "max_population": 400,
  "population_floor": 40,
  "death_floor_cents": 10000,
  "rent_min_cents": 10,
  "rent_bps_of_equity": 2,
  "fee_bps": 20,
  "repro_multiple": 1.25,
  "repay_multiple": 0.15,
  "reserve_floor_cents": 15000,
  "breed_cooldown_ticks": 50,
  "solo_breed_patience": 10,
  "max_age_ticks": 3000,
  "stagnation_ticks": 400,
  "max_action_fraction": 0.80,
  "min_ticks_for_fitness": 75,
  "snapshot_every": 25,
  "hall_size": 100,
  "hall_immigrant_prob": 0.4,
  "mutation": {
    "sigma_fraction": 0.10,
    "gene_flip_prob": 0.05,
    "archetype_hop_prob": 0.01,
    "adaptive": { "window_generations": 5, "stagnant_multiplier": 1.5, "improving_multiplier": 0.8,
                  "sigma_min": 0.02, "sigma_max": 0.30 }
  },
  "elitism_top_k": 3,
  "arena": { "name": "petri", "start_price_cents": 200, "price_floor_cents": 20,
             "regimes": [ {"kind": "trend_up",    "ticks": 3000, "drift_bps": 12,  "vol_bps": 60},
                          {"kind": "mean_revert", "ticks": 3000, "kappa": 0.15,    "vol_bps": 200},
                          {"kind": "crash",       "ticks": 100,  "drift_bps": -80, "vol_bps": 200},
                          {"kind": "mean_revert", "ticks": 2000, "kappa": 0.15,    "vol_bps": 200} ] }
}
```
**These defaults are empirically validated** (reference prototype, 5 RNG seeds): the colony survives both regimes, grows total system wealth +32% to +80% over 8,000 ticks, and passes the flagship adaptation test with wide margin. Do not "improve" them without re-running the validation experiments.
```
All cents values are integers. Sanity-check config on load and refuse to run on nonsense: `death_floor < gen0_seed`, `reserve_floor ≥ death_floor`, `gen0_seed_cents ≥ 200 × start_price_cents` (§3.11), `stagnation_ticks > max lookback bound`, `rent_bps_of_equity ≤ 2` (rent must stay far below achievable earn rates, §3.6), `gen0_population × gen0_seed_cents ≤ initial_treasury_cents`, `repay_multiple ≤ 0.25` hard-rejected above and values > 0.15 rejected without a `revalidated: true` flag (§3.14 — the failure mode above the cliff is silent).

---

## 6. Genome

```json
{
  "archetype": "momentum",
  "params": { "lookback": 30, "entry_z": 1.5, "exit_z": -0.5,
              "risk_fraction": 0.40, "hold_max": 600 },
  "econ":   { "child_seed_fraction": 0.40 },
  "genes":  ["fee_aware"]
}
```

### 6.1 Bounds table (clamp all mutations to these)
| Gene | Min | Max | Type |
|---|---|---|---|
| `lookback` | 5 | 100 | int (must stay < `stagnation_ticks`) |
| `entry_z` | 0.2 | 3.0 | float |
| `exit_z` | −2.0 | 2.0 | float, **SIGNED** — see semantics below |
| `risk_fraction` | 0.05 | 0.80 | float (also globally capped by risk engine) |
| `hold_max` | 20 | 1500 | int |
| `econ.child_seed_fraction` | 0.30 | 0.55 | float |

**`exit_z` semantics (VALIDATED — the naive version cannot express profitable behavior):** `exit_z` is the *signed z-level at which the agent exits*. Momentum exits **down** through it (`z ≤ exit_z`); mean-revert exits **up** through it (`z ≥ exit_z`). Because it may be negative, momentum can evolve "let winners run until price falls well below its mean" — with a positive-only exit threshold, momentum sells every ordinary dip, churns fees, and loses money even in a 10× bull market (validated failure). Constraint repair after mutation/crossover: momentum requires `exit_z < entry_z` (repair: `exit_z = entry_z − 1.0`); mean-revert requires `exit_z > −entry_z` (repair: `exit_z = −entry_z + 1.0`).

### 6.2 Archetypes (v1 ships exactly these three — simple, distinct, honest)
All decide once per tick from the price history the arena exposes. `z` = z-score of current price vs. trailing `lookback` mean/stdev.

- **momentum** — BUY when flat and `z ≥ entry_z`; SELL all when `z ≤ exit_z` (signed) or after `hold_max` ticks in position.
- **mean_revert** — BUY when flat and `z ≤ −entry_z`; SELL all when `z ≥ exit_z` (signed) or `hold_max` reached.
- **sitter** — never trades. **Deliberately included** as the control: the never-trader stagnation rule (§3.6) must reliably drive this archetype extinct. If sitters survive, your anti-degeneracy mechanics are broken.

`z` = z-score of the current price vs the trailing `lookback`-tick mean and population stdev; returns 0 until `lookback+1` prices exist. Order size for BUY: `lots = floor((risk_fraction × equity) / price)`, then risk-engine capped by `max_action_fraction`. Optional gene `fee_aware`: skip trades whose expected edge (|z|−exit_z, in vol units, converted to bps) is below `2 × fee_bps` — a cheap heuristic; exact formula is your choice, document it.

### 6.3 Genetic operators (implement in `evolution.py`, pure functions taking the RNG)
- **mutate(genome, sigma, rng)** — per numeric gene: `value += rng.gauss(0, sigma × (max−min))`, clamp; ints rounded. With `gene_flip_prob`: add/remove one gene from the small gene pool `{"fee_aware"}` (v1 pool is tiny; that's fine). With `archetype_hop_prob`: switch archetype uniformly at random and re-draw that archetype's params uniformly in bounds (a macro-mutation, not a blend).
- **crossover(gA, gB, rng)** — if archetypes match: per-param uniform pick (50/50) from either parent, then mutate. If archetypes differ: child inherits the *fitter* parent's archetype and params, the other parent contributes `econ` + genes, then mutate.
- **adaptive sigma** — track median equity growth of agents born in each generation cohort; if the trailing `window_generations` medians are non-increasing, `sigma ×= stagnant_multiplier`, else `×= improving_multiplier`; clamp to [sigma_min, sigma_max].

---

## 7. The Tick Loop (orchestrator core — keep it under ~150 lines)

Every tick, in this exact order (determinism):

```
0. quota sweep (§3.14): for each living agent with debt > 0, transfer
   min(debt, cash − baseline) to TREASURY (memo 'debt_repay'); never touches capital at or below baseline
1. arena.step(rng)                      # advance price per current regime
2. for agent in living agents (sorted by id):
     a. charge PROPORTIONAL rent = max(rent_min_cents, equity*rent_bps/10000);
        if cash short, force-liquidate the ENTIRE position in one sale (fees apply);
        if still short, die 'liquidity_death'
     b. decision = strategy.decide(genome, price_history, agent_state)
     c. risk_engine.check(decision)     # cap size, reject invalid; log rejects
     d. execute via ledger (buy/sell + fees), update positions & peak_equity, record trade
3. deaths: if equity ≤ death_floor → die('bankrupt')
           if age ≥ max_age → die('old_age')
           if NEVER traded and age ≥ stagnation_ticks → die('stagnation')   # never-traders only, §3.6
   (death = liquidate all, residue → TREASURY, archive; hall-of-fame check §3.13)
4. breeding (mitosis, §3.4):
     a. enqueue agents with cash ≥ repro_multiple × baseline AND debt == 0, cooldown elapsed, population < max_population
     b. matchmaker pairs queue (fitness-weighted); crossover births; funders reset baselines
     c. queue members waiting ≥ solo_breed_patience → asexual birth; baseline resets
     d. each birth: one atomic transaction (seed transfers + baseline updates + agent insert + genome archive)
     e. IMMIGRATION (§3.12): while population < population_floor and treasury ≥ gen0_seed_cents,
        treasury spawns an immigrant (40% mutated hall-of-fame genome at 2×sigma, else random)
5. snapshots (every snapshot_every ticks): write cash/equity for all living agents,
   plus ONE colony_metrics row (aggregates above are cheap to compute in the same pass)
6. every 100 ticks (or every tick in debug): verify_invariants()
```

### 7.3 Matchmaker pairing
Sample pairs from the breeding queue with probability proportional to `max(fitness, 0) + ε` (ε=1e−6 so zero-fitness agents can still pair). Elitism: the current top-`elitism_top_k` lineages by fitness are exempt from `max_population` blocking (they may always breed by displacing… nothing — simply allow the cap to be exceeded by at most `elitism_top_k`; document this).

---

## 8. Arena Interface + Petri Dish

```python
class Arena(Protocol):
    def step(self, rng) -> None: ...          # advance one tick
    def price(self) -> int: ...               # current price, cents/lot
    def history(self, n) -> list[int]: ...    # last n prices (oldest first)
```

**Petri Dish** (`arenas/petri.py`): a scripted price path through the config's regime list.
- `trend_up`: `p ← p × (1 + drift_bps/10000 + rng.gauss(0, vol_bps/10000))`
- `mean_revert` (OU-style): `p ← p + kappa × (anchor − p) + p × rng.gauss(0, vol_bps/10000)`, anchor = price at regime start
- `crash`: same as trend with negative drift and high vol
- Round to integer cents; floor price at `price_floor_cents` (20). Start at `start_price_cents` (200) — the seed/price ratio is a hard constraint, §3.11.
- When the regime list is exhausted, loop it.
The arena is exogenous: agents cannot move the price in v1. That is intentional — v1 tests evolution, not market impact.

---

## 9. Fossil Record & Reporting

- **Fossil Record** = the `agents` table itself (never delete rows) + `genome_json` + `death_cause` + final snapshot. Add a view or query helpers:
  - `lineage(agent_id)` → ancestor chain
  - `cause_of_death_histogram(run)`
  - `hall_of_fame(k)` → top-k dead-or-alive by lifetime fitness (with ≥ `min_ticks_for_fitness` history)
- **The north-star KPI is treasury growth** (§3.14): report it first, always, with its inflow breakdown by ledger memo — rent and death residues — minus immigration outflows (§3.14: these two channels ARE the quota; there are no others). Treasury above `initial_treasury_cents` means every cent of deployed capital has been recovered AND profit is banked, withdrawal-ready.
- **CLI reports** (plain text tables, no plotting deps):
  - `colony report` → per-tick-range summary: population, births, deaths by cause, treasury, arena account, total agent wealth, archetype distribution among the living, diversity index (Shannon entropy over archetype + binned params)
  - `colony tree > tree.dot` → Graphviz DOT of the family tree (node label: id/gen/peak equity; users can render with any dot tool; rendering is NOT a dependency of this repo)
  - `colony inspect <agent_id>` → genome, lifetime P&L, trade list, fitness

### 9.1 The Observatory (web monitor)

A read-only, live-updating dashboard: `colony serve [--port 8477]` starts a stdlib `http.server.ThreadingHTTPServer` that serves `colony/web/dashboard.html` at `/` and JSON at the endpoints below. **Security rule: the web layer opens SQLite in read-only mode (`file:colony.db?mode=ro`) and has no write path of any kind — no start/stop/config endpoints. Control stays in the CLI.** Binds to 127.0.0.1 by default.

**Endpoints (all GET, JSON):**
- `/api/summary` → `{run_id, tick, regime_kind, treasury_cents, colony_wealth_cents, system_total_cents, arena_extracted_cents, population, births_cum, deaths_cum, outstanding_debt_cents, invariant_ok, config}`
- `/api/timeseries?after_tick=0` → parallel arrays straight from `colony_metrics` (tick, treasury, colony_wealth, population, price, regime_kind, share_momentum, share_mean_revert, share_sitter, diversity)
- `/api/deaths` → death-cause histogram
- `/api/leaderboard?limit=20` → living agents by equity: id, generation, archetype, equity_cents, fitness, age, lineage_depth
- `/api/agent/{id}` → genome, lifetime stats, last 50 trades
- `/api/runs` → run list with config hash, seed, last_tick
- `/records/…` → read-only static listing/serving of the records folder (§9.2)

**Panels (one screen, no navigation):**
1. **Signature hero — the Strata Chart**: full-width stacked area of archetype shares over time, regime bands shaded behind it, birth/death rate as a faint line underneath. This is the flagship acceptance test rendered live — the colony's evolutionary history reads like sediment layers, and a regime flip should be visible as a stratum changing color.
2. KPI row: **Treasury** (north star — biggest figure, with Δ since run start and an "all capital recovered" badge once above `initial_treasury_cents`), System Total, Colony Wealth, Extracted from Market, Population, Invariant status (green OK / red HALT).
3. Wealth chart: treasury vs colony wealth vs system total.
4. Price chart with regime shading; population + cumulative births/deaths on a twin axis.
5. Death causes bar; diversity sparkline; outstanding-debt gauge (§3.14).
6. Leaderboard table (top 20 living), rows link to an agent inspector drawer fed by `/api/agent/{id}`.

Poll `/api/summary` + incremental `/api/timeseries?after_tick=<last>` every 2s; pause when the tab is hidden.

**Design tokens (build to these; the goal is an instrument, not a template):**
- Palette: background `#0B0E14` (deep ink), panel `#11151F` with 1px `#1D2433` borders; archetype identity colors carry the whole design — momentum `#E8A33D` (amber), mean_revert `#3DBFB0` (teal), sitter `#6B7280` (ash); treasury/north-star `#EFE7D3` (bone); alerts `#E5484D`. No gradients, no glow.
- Type: numerals are the personality — every figure in a tabular-lining mono (`"IBM Plex Mono", ui-monospace`) with `font-variant-numeric: tabular-nums`; labels/headings in a compact grotesque (`"Space Grotesk", system-ui`) set in small caps with wide tracking for panel titles. Cents rendered as currency with thin-space grouping.
- Layout: 12-col CSS grid, hero spans all 12; density over whitespace — this is a monitor, every panel earns its pixels. Motion: number ticks may ease over 300ms; nothing else animates. Respect `prefers-reduced-motion`; visible keyboard focus.

### 9.2 Records (plain-text audit trail)

Every run, experiment, and test session writes an append-only, human-readable `.txt` record. The database is the source of truth; records are the permanent, greppable lab notebook that survives db resets and gets committed to git.

```
records/
├── INDEX.txt                      # one line per record: timestamp | kind | path | headline result
├── runs/run_<id>_<UTCstamp>.txt
├── experiments/<name>_<UTCstamp>.txt      # profit_matrix, regime_flip output
└── tests/pytest_<UTCstamp>.txt
```

Rules (enforced by a small `colony/records.py` used by run/experiments/tests alike):
- **Append-only, never overwrite**: filenames carry a UTC timestamp; creating a record fails loudly if the path exists.
- Every record begins with a reproducibility header: UTC time, git describe/commit if available, full config JSON, RNG seed, code of the experiment's regime schedule where applicable.
- Run records: written at run end AND every 2,000 ticks (checkpoint sections appended), containing the §9 report plus KPI deltas and invariant status. A run killed mid-flight still leaves its last checkpoint.
- Experiment records: the full printed output of the experiment, verbatim, plus PASS/FAIL verdicts per criterion.
- Test records: `colony test` runs pytest and tees complete output into `records/tests/`.
- `INDEX.txt` gets one appended summary line per record so the whole project history reads in one file.

---

## 10. CLI (single entry point `python -m colony ...` via `argparse`)

```
colony init   [--config config.json]          # create db, accounts, gen-0 agents (seeds from treasury)
colony run    --ticks N [--config ...]        # run/continue the simulation
colony report [--last N]
colony tree
colony inspect AGENT_ID
colony verify                                  # run all invariants against the ledger, exit non-zero on failure
colony serve  [--port 8477]                    # read-only Observatory dashboard (§9.1)
colony test                                    # run pytest and tee output into records/tests/ (§9.2)
```

`init` on an existing db refuses to run (no silent resets). `run` resumes from `runs.last_tick`.

---

## 11. Repository Layout

```
darwin-wallet/
├── README.md                # what/why/quickstart (write it for a newcomer)
├── DECISIONS.md
├── config.default.json
├── pyproject.toml           # project metadata; pytest as dev dep
├── colony/
│   ├── __main__.py          # CLI
│   ├── config.py            # load + validate
│   ├── db.py                # schema, connections, transactions
│   ├── ledger.py            # transfer(), balances, verify_invariants()
│   ├── agents.py            # spawn, death, positions, equity
│   ├── strategies.py        # the 3 archetypes (pure functions: genome, history, state -> decision)
│   ├── risk.py              # order caps & validation
│   ├── evolution.py         # mutate, crossover, fitness, adaptive sigma, matchmaker
│   ├── orchestrator.py      # the tick loop
│   ├── report.py
│   ├── records.py           # append-only txt records (§9.2)
│   ├── server.py            # stdlib HTTP server, read-only JSON API (§9.1)
│   ├── web/
│   │   └── dashboard.html   # the Observatory — single self-contained file
│   └── arenas/
│       ├── base.py          # Arena protocol
│       └── petri.py
├── reference_prototype.py   # validated in-memory prototype (consult, don't copy architecture)
├── records/                 # committed plain-text audit trail (§9.2); .gitkeep + INDEX.txt
├── experiments/
│   ├── profit_matrix.py     # environment pre-check (§13.3a)
│   └── regime_flip.py       # the flagship acceptance experiment (§13.3b)
└── tests/
    ├── test_ledger.py
    ├── test_conservation.py
    ├── test_evolution.py
    ├── test_strategies.py
    ├── test_lifecycle.py
    └── test_determinism.py
```

Rough size target: the whole `colony/` package should land well under ~1,500 lines. If a module wants to be bigger, you are overcomplicating — simplify.

---

## 12. Build Order (do these in sequence; tests green before moving on)

1. **Repo scaffold** — README stub, pyproject, gitignore, empty tests collected by pytest.
2. **db.py + ledger.py** — schema, `transfer()`, balance cache, `verify_invariants()`. Tests: overdraw rejection for AGENT/TREASURY, arena-may-go-negative, cache==ledger-sum, conservation under 1,000 random transfers.
3. **config.py** — load, validate, reject nonsense.
4. **arenas/petri.py** — regime engine. Tests: determinism given seed; price floor; regime boundaries honored.
5. **agents.py + strategies.py + risk.py** — spawn gen-0 from treasury; equity; the 3 archetypes as pure functions with unit tests on hand-built price histories; risk caps.
6. **orchestrator.py (life & death only)** — tick loop steps 1–3 + 5–6, breeding disabled. Tests: rent starves sitters; bankruptcy triggers full liquidation and residue transfer; old-age and stagnation deaths; conservation holds over a 2,000-tick run.
7. **evolution.py + breeding (steps 4)** — operators with bound-clamping tests; atomic birth (test: kill the process mid-birth via an injected exception → db shows either both seed-transfer and agent row, or neither); queue/matchmaker; cooldowns; population cap + elitism.
8. **report.py + records.py + CLI** — all commands work end-to-end; runs and `colony test` write records (§9.2).
9. **experiments/profit_matrix.py** — the environment pre-check (§13.3a). Run it BEFORE the flagship; if the matrix is wrong, fix the arena/regime parameters, not the GA.
10. **experiments/regime_flip.py** — the flagship experiment (§13.3b).
11. **server.py + dashboard.html (the Observatory)** — endpoints against a populated db from step 10's experiments; build the dashboard to the §9.1 panel list and design tokens; verify live polling against a running `colony run` in a second terminal.
12. **Polish** — README with quickstart + a sample run's output; `colony verify` wired into CI (a simple GitHub Actions workflow running pytest + a 500-tick smoke sim + verify).

---

## 13. Acceptance Criteria (the build is DONE when all of these hold)

**13.1 Accounting**
- `colony verify` passes after any run.
- A 10,000-tick default-config run completes with zero invariant violations.
- Property test: 1,000 random valid operations (trades/rent/births/deaths) preserve conservation exactly.

**13.2 Ecology sanity (default config, seed 42 — these ranges are empirically achievable with the shipped defaults)**
- Gen-0 experiences meaningful selection: between 30% and 90% of gen-0 dies within the first regime.
- All gen-0 **sitter** agents are dead by tick `stagnation_ticks + 50`, and no sitter (including ones re-created by archetype-hop mutations) ever survives longer than `stagnation_ticks + 1` ticks after birth.
- At least one lineage reaches generation ≥ 4 by tick 10,000.
- Population never exceeds `max_population + elitism_top_k`, and never falls below `population_floor` while the treasury can afford an immigrant.
- The colony NEVER goes extinct in a 10,000-tick default run (immigration guarantees this — extinction means §3.12 is broken).

**13.3a Environment pre-check: the profitability matrix (REQUIRED before the flagship — validated to catch broken environments)**
`experiments/profit_matrix.py`: run a SINGLE agent (no death, no breeding, rent on) with a known-good genome per archetype in each pure regime for 3,000 ticks × 5 seeds; report mean realized bps/tick of equity growth. Known-good probes: momentum `{lookback 30, entry_z 1.5, exit_z −0.8, risk 0.6}`; mean_revert `{lookback 60, entry_z 1.5, exit_z 0.2, risk 0.6}`.
**Pass:** momentum ≥ +3 bps/t in `trend_up` and ≤ −2 bps/t in `mean_revert`; mean_revert ≥ +2 bps/t in `mean_revert` and ≤ 0 in `trend_up`. (Validated reference values with shipped params: momentum +4.9 / −10.8; mean_revert +3.3 / −0.9.) If this matrix has the wrong signs, **adaptation is impossible no matter how good the GA is** — fix the regime parameters. This experiment exists because the naive mean-revert regime (kappa 0.05, vol 80) makes mean-reversion unprofitable: the trailing mean chases the price, and the "reversion" signal fires because the mean fell.

**13.3b Evolution actually adapts (the flagship experiment)**
`experiments/regime_flip.py` (builds its own config in code — fresh db, fixed seeds, two-regime arena): run 3,000 ticks of `trend_up` (drift 12, vol 60), record the archetype distribution among living agents; then 5,000 ticks of `mean_revert` (kappa 0.15, vol 200); record again. **Pass:** across seeds {42, 7, 2026}, the living-population share of the `mean_revert` archetype increases by ≥ +20 percentage points in EVERY run, and total system wealth (treasury + agent wealth) is above its starting value in every run. Print a before/after table per seed. (Validated reference results: shifts +66/+80/+85 pts; wealth +32%/+44%/+58%. If you see numbers far below these, something regressed.)

**13.3c The treasury actually grows (the quota criterion, §3.14)**
- In the flagship run (13.3b), ending treasury > `initial_treasury_cents` on EVERY seed: full recovery of all deployed capital plus banked, withdrawal-ready profit, via rent + death residues alone. (Validated reference: treasury +31.3% average over the 8,000-tick flagship.)
- The ledger contains NO agent→treasury transfer memos other than `rent`, `death_residue:*`, and `debt_repay` — any other kind means a forbidden mid-life quota crept in.
- Quota property tests (§3.14): `debt_repay` sweeps never reduce cash below `baseline`; only house-funded agents ever carry debt; an agent with debt > 0 never appears in the breeding queue; the flagship passes with the quota ON (it is the default) — reference: min +65p, treasury +34.6%, system +46.0% over 5 seeds.
- The per-500-tick treasury inflow series has no drought longer than `max_age_ticks` (staggered senescence keeps the stream continuous).

**13.4 Determinism**
- Two runs with identical config+seed produce identical `ledger` tables (compare row-by-row hash).

**13.5 Observatory & Records**
- `colony serve` starts against an existing db; every endpoint in §9.1 returns valid JSON matching its documented shape; all endpoints work mid-run while `colony run` is writing (WAL mode makes this safe).
- The web layer performs zero writes: property test asserts the server's SQLite connection is read-only and the handler exposes no non-GET routes.
- Dashboard renders all panels against experiment data with no console errors; with the CDN blocked, all KPI numbers and tables still render (charts absent is acceptable).
- Every `colony run`, both experiments, and `colony test` produce a record file with the reproducibility header; `INDEX.txt` gains one line each; attempting to overwrite an existing record path raises.
- A run interrupted with SIGINT at tick 5,000 leaves a checkpoint section covering at least tick 4,000 in its record.

**13.6 Simplicity**
- Core package: stdlib only. `pip install -e . && pytest` from a clean venv passes with only pytest installed.

---

## 14. v2 Plug-in Points (design for, do NOT build)

Leave clean seams; add `TODO(v2)` comments at each:
- **Cognition layer**: `strategies.py` decisions are pure functions today; v2 adds an archetype whose `decide()` calls an external LLM provider (configured by environment variable), with the API cost debited from the agent's wallet via a `SINK:METABOLISM` account (an ARENA-kind account, so conservation still holds). The interface: same inputs, same `Decision` dataclass out, plus a `cost_cents` the orchestrator debits.
- **Liquidity harvest (only if ever needed)**: a small profit tax at breeding events for tiny live colonies (~10 agents) where death-timed treasury inflows are genuinely lumpy. Validated costs if enabled: 10% rate ⇒ ~−12pp treasury growth and −16pp system wealth vs none; ≥15% breaks adaptation. Default absent; never in v1.
- **Treasury = the master wallet in live mode**: the treasury account maps to YOUR real wallet/account on the payment rail; human withdrawals happen ONLY from the treasury, never from agent wallets; the quota (§3.14) is the pipeline that moves realized profit from the colony's working capital into that withdrawable account continuously.
- **Tax drag modeling (REQUIRED before any live capital)**: real-world taxes on trading gains are, economically, an involuntary harvest on realized profit — and the §3.14 validation showed even a 15% profit skim silently damages colony adaptation. Before Phase 4, re-run the full validation suite (profit matrix + flagship) with tax modeled two ways: (a) as additional per-trade bps on top of `fee_bps` (crude, for transaction-tax jurisdictions), and (b) as a settlement-time skim of `tax_rate × realized_gain` on each profitable SELL, at the rate applicable to the operator's jurisdiction and entity type. If the flagship no longer passes at the applicable rate, the strategy economics do not survive taxation and live deployment must not proceed. Track estimated tax liability in a dedicated `SINK:TAX` account (ARENA-kind, so conservation holds) so the treasury's "withdrawable profit" is stated net of the liability, never gross. Rates and treatment vary by jurisdiction — confirm with a tax professional; this spec models the drag, it does not give tax advice.
- **Real arenas**: paper-trading adapter implementing the same `Arena` protocol against live exchange testnet data; then per-agent exchange sub-accounts / per-agent keypairs for live micro-capital. The ledger becomes a *mirror* of external rails with reconciliation jobs.
- **Speciation**: genome-distance clustering restricting crossover within clusters.
- **Multi-asset / agent-vs-agent order book**: replaces the exogenous-price Petri Dish.

None of this may leak complexity into v1.

---

## 15. Definition of the Repo's README

The README must contain: one-paragraph concept summary, quickstart (`init` → `run --ticks 10000` → `serve` in a second terminal → `report` → `tree`), a screenshot-free description of the Observatory panels and where records land, the regime-flip experiment invocation and what a passing result looks like, the money-conservation guarantee stated plainly, and a short "safety by construction" section (simulation-only, no network calls, no real funds, agents cannot self-modify).

---

*End of specification. Build it simple, build it correct, and let the colony do the rest.*

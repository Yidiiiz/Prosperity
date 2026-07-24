# darwin-wallet

An evolutionary colony of autonomous agents, each with a strictly isolated
wallet, acting in a market arena. Profitable agents reproduce by **mitosis**
— funding their children out of their own profits — and unprofitable agents
go bankrupt and die. Selection, crossover, mutation and immigration drive the
colony toward profit with no human tuning. Every micro-dollar is integer
money in a double-entry SQLite ledger, and the whole system is deterministic:
same config + same RNG seed ⇒ byte-identical ledgers.

v2.0 makes the colony **always-on**: a supervised daemon trades the live
1-second BTC tape (paper only), audits its own history nightly against an
offline replay twin, and serves a phone-usable observatory. Money is
micro-dollars (1 $ = 1,000,000 u) so second-scale economics don't round to
zero; config speaks wall time (`_seconds`/`_hours`/`_days`, annualized rent)
so the same colony definition runs at day, minute, or second bars.

v3.0 is the **profitmaker economy**: every replay verdict is measured against
buy-and-hold on the same tape at the same costs (tiers: **ALPHA** beats
buy-and-hold, **CASH** beats initial, **EXPECTED-FAIL** made no money with
sound machinery; **FAIL** means the machinery broke — the only failure);
agents are ranked by **realized** P&L from the ledger, not marks; proven
genomes are admitted to a **bank**, certified out-of-sample by a frozen solo
probe on a postdating window, and reused as immigrants in later colonies;
and treasury profit **compounds** — a one-way high-water ratchet redeploys
half of each new high into the immigration budget.

## Quickstart (simulation)

```
pip install -e .[dev]            # stdlib core; pytest is the only dev dep
python -m colony init                            # create colony.db, seed gen-0
python -m colony run --ticks 10000               # run the simulation
python -m colony serve                           # (second terminal) live dashboard
python -m colony report                          # plain-text summary
python -m colony inspect 000001                  # one agent's genome/P&L/trades
python -m colony verify                          # audit the ledger invariants
python -m colony test                            # pytest, teed into records/tests/
```

`init` refuses to touch an existing database; `run` resumes exactly where the
last run stopped (RNG, arena and per-agent state are checkpointed). A hard
kill at any instant is safe: live configs pin `flush_every 1`, so the database
is always at a committed tick boundary.

## Quickstart (the always-on daemon)

```
python -m colony --db live.db daemon                 # config.live.json by default
python -m colony daemon status                       # health probe: exit 0/1/2
python -m colony --db live.db audit                  # replay-twin audit, on demand
python -m colony daemon clear-audit                  # operator clears a CRITICAL latch
```

One process does everything: it supervises the feed subprocess
(`tools/live_feed.py`, Binance `@kline_1s` websocket — public market data, no
key, no orders), consumes the journal one appended row per tick, verifies
conservation **every tick**, writes a health sidecar served at
`/api/health`, and after each UTC-midnight segment rotation replays the
closed day offline through the replay arena and compares ledger hashes. A
mismatch is a CRITICAL incident (`grep '^!!' records/INDEX.txt`) that latches
until an operator clears it — but the daemon keeps running: an audit failure
is an alarm about the past, not a reason to lose the present.

The journal is a directory of daily segments (`data/journal/YYYY-MM-DD.csv`,
sealed with a `.sha256` on rotation). A stale feed pauses the colony — the
wall clock paces, the journal decides; only operators stop it. Feed gaps are
counted and reported, never errors: the colony simply didn't tick.

## The economy, briefly

- **Treasury** is the north-star KPI: the house account that funded gen-0.
  Agents pay **rent** (an annual rate, `rent_apr_bps`, charged per tick),
  house-funded agents repay a seed quota (0.15×), and every estate returns to
  the treasury at death. Treasury above initial capitalization means every
  deployed micro-dollar was recovered *and* profit is banked.
- **The venue is honest**: taker fees plus a bid/ask spread charged at the
  fill (rounded against the agent), and orders decided at bar N fill at bar
  N+1's price. Same-bar fills exist only in the scripted Petri arena.
- **Four archetypes** (`momentum`, `mean_revert`, `breakout`, and `sitter` —
  the deliberate do-nothing control) share three universal **gate genes**: a
  volatility gate
  (don't play flat tapes), a trades-per-day throttle (rolling 24h fill
  window), and a 24-bit UTC active-hours mask. Gates block opens only;
  closing is always allowed. Evolution decides when *not* to trade.
- **Immigration is budget-capped**: the treasury reseeds the population from
  a token bucket accruing at `immigration_budget_apr_bps` (default 20%/yr of
  initial treasury). When the budget is exhausted the population honestly
  sits below the floor — visible on the dashboard — instead of the treasury
  churning itself into life support.

## The bank — proven genomes, reused

```
python -m colony bank list                       # every genome ever admitted
python -m colony bank show a1b2c3d4e5f6          # one genome, full history
python -m colony bank certify --tape data/spy_d.csv --from 2019-01-01
```

When a replay colony winds down, the terminal audit admits the top realized
profitmakers to an **append-only JSONL bank** as *candidates* — in-sample
performance proves nothing. A candidate is **certified** by a frozen solo
probe ($1,000, no rent, no evolution) on a window that must postdate its
admission window; overlap is refused, not warned about. Lapsed genomes stay
visible forever — the bank remembers its failures.

A new colony configured with `bank_path` copies the certified set into an
immutable `bank_snapshot` at init and draws half its immigrants from it
(unmutated clones, seeded at `champion_seed_multiple ×` the gen-0 stake, paid
from the same immigration budget — reputation buys size, not new money). A
running colony **never** reads the live bank: to refresh champions, start a
new colony. The bank stores parameter dictionaries, never code.

## The Observatory

`python -m colony serve` (or the daemon's `--port`) serves a single-file,
read-only dashboard at `http://127.0.0.1:8477/`:

- **The Money Strip**: EXTRACTED (audited cash pulled from the market —
  today, this hour, per second; the number is allowed to be red), CASH
  (treasury, colony cash), MARKED (position value, outlined, labeled
  *unrealized* — never summed with cash), and **vs B&H** (system total minus
  buy-and-hold on the same tape at the same costs — a red delta on a green
  treasury is the honest picture).
- **Liveness chips**: feed LIVE/STALE/RECONNECTING, ticks-behind, invariant
  badge, last audit ✓/✗, immigration-budget gauge.
- **Strata chart** — stacked archetype shares over wall-clock time with
  regime bands and UTC day rules; the colony's history reads like sediment.
- **Trade tape** — the last 50 fills, streamed live.
- Wealth/price charts, death causes, diversity, a leaderboard (bank-descended
  agents wear a BANK badge) opening an agent inspector with origin and an
  inline collapsible ancestry chain, and a **Champions** panel: the bank
  snapshot this colony started from, with living descendants per champion.

Data arrives by Server-Sent Events (`/api/events`: coalesced summaries ≤1/s,
per-fill events, health changes) with automatic fallback to polling; series
are server-bucketed (`/api/timeseries?max_points=`) so a full 86,400-tick day
renders from under 100 KB. The web layer opens SQLite read-only and answers
GET only — control stays in the CLI. Under 720 px the grid collapses to one
column: an always-on colony gets checked from a phone.

## Feeds and data

```
python tools/fetch_binance_klines.py BTCUSDT 1m --days 365 -o data/btcusdt_1m.csv
python tools/fetch_market_data.py SPY -o data/spy_d.csv
python tools/live_feed.py BTCUSDT --journal data/journal            # 1s websocket
```

The only network code in the project lives in `tools/` (Binance public data
hosts: `data-api.binance.vision`, `data-stream.binance.vision`). The core
replays CSVs offline and stays fully deterministic; resume is guarded by
digests of the consumed price series — a changed tape refuses to resume.
Committed fixtures (`data/btcusdt_1m_fixture.csv`, `data/spy_d.csv`) keep the
entire test suite offline.

## Experiments

```
python -m experiments.profit_matrix     # environment pre-check — run FIRST
python -m experiments.regime_flip       # the flagship adaptation experiment
python -m experiments.real_market       # SPY dailies, $200k → $100 → $10
python -m experiments.minute_ladder     # BTC 1m, $200k → $1k → $10 (--parallel)
python -m experiments.live_soak --hours 24   # the acceptance soak (daemon + kill)
python -m experiments.walk_forward      # evolve on window k, certify on k+1
python -m experiments.bank_reuse --bank BANK.jsonl --csv TAPE --from DATE
```

Every experiment is per-seed, incremental (results print the moment a seed
finishes), and resumable via `--workdir`. Replay experiments end in a
**terminal audit**: every estate liquidated at the last real price, so
verdicts are audited cash vs initial — no mark-to-market hand-waving. The
minute ladder separates machinery from economics: a rung may be
EXPECTED-FAIL (sound machinery, negative economics, numbers recorded) — only
the machinery can truly fail. The soak starts the daemon, hard-kills it at a
random moment, verifies exact resume, and hands the verdict to the
replay-twin audit.

The v3 evidence experiments *measure* rather than demand: `walk_forward`
splits a tape into contiguous windows, evolves on each, certifies champions
on the next, and reports EDGE/NO-EDGE per seed (SPY 1993–2026: NO-EDGE — the
honest result); `bank_reuse` runs a with-bank vs without-bank A/B on a
held-out window that must not overlap any banked genome's history, and
reports the delta whichever way it points. Every replay record ends with the
mandatory footer: span, wall, annualized return (marked "(projected)" under
a year), and the buy-and-hold benchmark with delta.

## The frequency frontier (v4)

```
python -m experiments.frequency_grid --cells all --workdir work_grid --parallel
python -m experiments.frequency_grid --summarize --workdir work_grid
python -m experiments.frequency_grid --holdout <cell> --workdir work_grid   # fires ONCE
```

v4 asks one question: is there a trading cadence (second / minute / hourly /
daily) and asset (BTC, ETH, SPY, QQQ) where certified champions earn faster
than S&P-500 buy-and-hold over the same calendar window? Every cell is the
v3 walk-forward — evolve on window k, certify on k+1 by frozen solo probe —
judged against `spx_over` the same dates (never against SPY's long-run
average), with the final 20 % of every tape carved off as a one-shot holdout
before any window is cut. Intraday equities are absent by honesty, not
oversight: no free intraday equity tape of fetchable quality exists, so
crypto carries the intraday axis.

Measured (mean OOS %/yr vs SPY same-window, seeds 42/7/2026, base retail
costs 10 bps taker / 2 bps spread):

| cell    | verdict        | champions %/yr | SPY %/yr |
|---------|----------------|----------------|----------|
| eth_1d  | BEATS-SPX ×2, NO-EDGE ×1 | +16.2 .. +27.2 | +14.9 |
| btc_1d  | NO-EDGE (window majority) | +17.5 .. +33.3 | +9.7 |
| qqq_1d  | NO-EDGE        | +1.9 .. +2.5   | +8.0     |
| spy_1d  | NO-EDGE        | −0.6 .. −0.4   | +4.6     |
| btc_1h  | NO-EDGE        | +5.8 .. +9.4   | +22.2    |
| eth_1h  | NO-EDGE        | −6.4 .. −0.8   | +22.2    |
| btc_1m  | NO-EDGE        | 1 seed −89.7; 2 seeds zero champions | +4.0 |
| btc_1s  | NO-EDGE        | zero champions certified | — |

The gradient is monotone the wrong way for the trade-faster thesis: daily >
hourly ≫ minute > second. The cost arm (btc_1s at counterfactual 2/1 and
0/0 bps) shows second-scale trading fails on friction first and signal
second — even at free costs the best seed merely lost slower than a falling
yardstick. The one-shot holdout went to the best cell by pre-registered rule
(btc_1d, best OOS delta) and is the v4 headline: **HOLDOUT NO-EDGE — 0/3
seeds beat SPY** on 2024-10-06 → 2026-07-19 (champions −2.2/+1.8/−1.1 %/yr
vs SPY +16.2; BTC buy-and-hold itself +1.5). At retail costs on these
tapes, nothing here earns faster than the S&P out-of-sample — that map is
the deliverable, and it is cheaper than learning it with real money.

## The allocation bench (v5)

```
python -m experiments.allocation                       # all five families
python -m experiments.allocation --holdout <family>    # fires ONCE
```

v4 settled *how fast* (daily, and still not fast enough); v5 tests *what to
hold*: four deterministic daily strategy families over SPY/QQQ/BTC/ETH —
dual momentum (hold the trailing-L winner or cash), trend (asset above its
SMA or cash), equal-weight rebalancing, volatility targeting — plus a
`best_bh` beta control that just buys last window's best asset. Parameter
grids were pre-declared in BUILD_SPEC_V5.md; train window k selects, the
frozen pick runs on window k+1; signals lag fills by one day; every fill
pays base venue costs. No leverage, no seeds (nothing random to seed).

Measured (7 test windows on 2017-08-17 → 2024-10-01, verdict by strict
window majority vs SPY same-window):

| family        | verdict   | windows beat SPY | mean OOS delta |
|---------------|-----------|------------------|----------------|
| dual_momentum | BEATS-SPX | 5/7              | +122.2 pp/yr¹  |
| equal_weight  | BEATS-SPX | 4/7              | +53.3 pp/yr¹   |
| trend         | BEATS-SPX | 4/7              | +0.5 pp/yr     |
| vol_target    | NO-EDGE   | 2/7              | −3.7 pp/yr     |
| best_bh (control) | NO-EDGE | 3/7            | −5.1 pp/yr     |

¹ outlier-heavy: one 2020–21 window where momentum rode BTC/ETH to 7×.

The control failing while momentum certifies means the majority is not
asset selection in a costume. The one-shot holdout (final 448 days,
2024-10-02 → 2026-07-17 — the same flat-crypto stretch that killed v4's
btc_1d) went to dual_momentum by the pre-registered rule and is the first
positive holdout in this repository: **[L=252] $10,000 → $16,871.76
(+33.99 %/yr) vs SPY $13,035.91 (+15.99 %/yr), delta +18.00 pp/yr**, and it
beat every single-asset buy-and-hold it could have hidden in (best was QQQ
at $14,394). One window, one shot, 1.79 years: evidence, not proof —
cross-asset momentum is the literature's most robust anomaly and also its
most famous crasher. `data/holdout/alloc.SHOT` forbids reruns; a second
look requires data that postdates 2026-07-19.

## The universe bench (v6)

```
python -m experiments.allocation6                      # equity era (decisive)
python -m experiments.allocation6 --bench full         # crypto era (exploratory)
python -m experiments.allocation6 --holdout <family>   # fires ONCE
```

v6 stress-tests the v5 survivor on the two axes v5 couldn't: **more assets**
(iwm, efa, gld, tlt join the universe) and **more history** (the equity-era
bench reaches back to GLD inception, 2004-11-18, covering 2008/2011/2015/
2018/2020). Six families with grids frozen in BUILD_SPEC_V6.md: three
momentum expansions (`dm_topk` — top-K generalization, K=1 provably bridges
to the v5 winner; `dm_1201` — classical skip-month; `dm_defensive` — tlt/gld
fallback) and two new ideas (`sma_ew` — Faber tactical; `inv_vol` — risk
parity lite), plus the `best_bh` control.

Measured, 9 OOS windows each:

| family | equity era 2004→2022 | crypto era 2017→2026 |
|---|---|---|
| dm_topk | 3/9, −3.19 pp/yr | **6/9, +31.70 pp/yr** |
| dm_1201 | 5/9, −2.57 pp/yr | 4/9, +31.64 pp/yr |
| dm_defensive | 1/9, −9.00 pp/yr | **6/9, +56.30 pp/yr** |
| sma_ew | 1/9, −4.47 pp/yr | 3/9, +5.12 pp/yr |
| inv_vol | 4/9, −0.86 pp/yr | 5/9, −0.47 pp/yr |
| best_bh (control) | **6/9, +4.89 pp/yr** | 3/9, −11.34 pp/yr |

The two columns invert. Rotation earns its keep only where the universe has
real dispersion (crypto vs equities); on the correlated ETF universe the
monthly rotators pay tolls to whipsaw, while the control's wins ride
decade-scale regimes (gold 2005–2011, QQQ 2014–2020) — "hold last window's
winner" is itself momentum at a ~1.7-year clock. The v6 one-shot holdout
(final 1,089 equity-era days, 2022-03-15 → 2026-07-17, through the 2022
bear) therefore went to the *control* by the pre-registered rule:
**best_bh [asset=qqq] $10,000 → $21,142.69 (+18.83 %/yr) vs SPY $17,399.98
(+13.61 %/yr), delta +5.22 pp/yr, BEATS-SPX**, robust at 2× and 5× costs
(two trades). Honest reading: that verdict is concentrated growth beta, not
skill — gld nearly matched ($20,547) and tlt lost a third — and its tail
overlaps the spent v5 holdout (disclosed in the spec before the run).
`data/holdout/alloc6.SHOT` forbids reruns.

## The dispersion gate (v7)

```
python -m experiments.allocation7                      # crypto era (decisive)
python -m experiments.allocation7 --bench etf          # equity era (regime check)
python -m experiments.allocation7 --holdout dm_gated   # refuses until ~2027-01
```

v7 turns the v6 diagnosis into strategy. `dm_gated` measures cross-asset
dispersion (best-minus-worst trailing return) every month and switches
modes: momentum rotation when dispersion is high, ride-the-378-day-winner
when it is low. `slow_bh` makes the v6 control's regime-riding explicit;
`dm_cadence` tests weekly vs monthly vs quarterly rotation; `dm_topk` and
`best_bh` ride along as incumbent and control (grids frozen in
BUILD_SPEC_V7.md).

Measured (full spans — every historical holdout is spent, so these windows
are evidence, not validation):

| family | crypto era, 9 windows | equity era, 11 windows |
|---|---|---|
| dm_gated | **5/9, +61.93 pp/yr** | 5/11, −0.51 pp/yr |
| dm_cadence | 6/9, +52.51 | 5/11, −4.16 |
| slow_bh | 4/9, +40.83 | 6/11, −0.08 |
| dm_topk (incumbent) | 6/9, +31.70 | 6/11, −2.02 |
| best_bh (control) | 3/9, −11.34 | **8/11, +5.81** |

The gate beats the incumbent on both universes — doubling the crypto-era
mean and cutting the equity-era loss — but nothing rotational beats SPY
where dispersion is low; the control still owns that column. Cadence picks
were unstable (5, 21, and 63 days all selected): *what gates the rotation
matters; how often you rotate inside a week-to-quarter barely does.*

Because all three historical holdouts are spent, the v7 shot is **forward**:
`data/holdout/alloc7.FORWARD` (committed, frozen) declares dm_gated on the
8-asset universe, firing only once ≥126 joint rows postdate 2026-07-19 —
roughly January 2027 after a tape refetch. Parameters get re-selected once
on pre-cutoff data, frozen, run once on rows nobody has seen — because they
haven't happened. The runner refuses a spent shot, an unripe tape, and any
undeclared family; all three refusals are tests.

## The regime bench (v8)

```
python -m experiments.allocation8                              # bull/bear + inverse ETFs
python -m experiments.allocation8 --holdout regime_safe        # historical shot (fires once)
python -m experiments.allocation8 --holdout regime_safe --forward   # refuses until ~2027
```

v8 tests a market idea directly: markets have **bull** and **bear** regimes,
and you can hold an **inverse ETF** to profit while the market falls instead
of only fleeing to cash. Universe `U_DIR` adds *real* inverse-ETF tapes (SH
−1× S&P 500, PSQ −1× Nasdaq-100, from 2006) so the test pays their true
daily-reset drag. Three families share one regime clock (risk-on asset vs its
own SMA) and differ only in the bear leg — `regime_inv` holds the inverse,
`regime_flat` goes to cash, `regime_safe` flees to gold/bonds — so their
ranking isolates whether *being short adds value*. `mom_inv` lets momentum
rotate into an inverse ETF when a selloff makes it the leader; `best_bh` is
the control (grids frozen in BUILD_SPEC_V8.md).

Measured (dir bench, 9 OOS windows, 2006→2022 grid span):

| family | verdict | bear leg |
|---|---|---|
| regime_safe | 4/9, **+2.38 pp/yr** | flee to gold/bonds |
| best_bh (control) | **6/9, +1.10** | — (only BEATS-SPX family) |
| regime_flat | 4/9, +0.60 | cash |
| mom_inv | 4/9, −5.09 | rotate (inverses poison it) |
| regime_inv | 2/9, **−6.72** | hold the inverse ETF |

**The operator's literal idea — hold inverse ETFs in a bear — is the worst
family.** The ranking is unambiguous: *flee-to-safety > cash > short-via-
inverse.* Inverse ETFs bleed from daily-reset drag and 200-day-SMA timing
can't overcome it; over the holdout span buy-and-hold of the inverses roughly
halved capital (psq $3,794, sh $5,129 from $10,000). Passive beta still wins
in-grid.

The historical shot (`regime_safe [R=qqq, S=gld]`, reserved 2022→2026 span)
did beat SPY — $27,297 vs $19,300, +10.62 pp/yr — but read honestly that win
is **beta + luck, not shorting skill**: it holds QQQ in bull markets (the v6
growth-beta finding) and flees to gold, and gold ripped over this span. It was
NO-EDGE in-grid, and the author knew the span held the 2022 bear when
designing it (disclosed). So the clean test is **forward**: `alloc8.FORWARD`
pre-declares `regime_safe` on `U_DIR`, firing once ≥126 rows postdate
2026-07-23 (~2027). Refuses a spent shot, unripe tape, and any undeclared
family; all three refusals are tests.

## The Wish bench (v9)

```
python -m experiments.allocation9                            # Green Line Breakout, GMI inverse, sectors
python -m experiments.allocation9 --holdout sector_mom       # historical shot (fires once)
python -m experiments.allocation9 --holdout sector_mom --forward   # refuses until ~2027
```

v9 implements Dr. Eric Wish's strategies and tests the leveraged-inverse idea
*correctly*, on real tapes: SQQQ/SPXU (−3×), SDS (−2×), plus defense (ITA) and
home construction (ITB) sectors. Families: `glb` (Green Line Breakout — buy an
all-time-high breakout that has held ≥3 months, ride it under a 30-/42-week MA
stop), `gmi_inv` (Wish's market timing → hold the index above its 30-week MA,
else rotate into an inverse ETF — each index paired with −1× *and* −3×/−2× to
test the leverage head-to-head), `sector_mom` (momentum over the long-only
sleeve), and `best_bh` (control).

**The 3× fact, measured.** SQQQ's *daily* beta vs QQQ is **−2.96** — so yes,
−3× products move ~3× the inverse each day. But daily reset compounds into
decay: **buy-and-hold SQQQ 2010→2026 went to ≈$0 while QQQ rose 15.9×.** The
3× relationship is a one-day statement; held for any length these bleed out.

Measured (wish bench, 9 OOS windows, 2010→2022 grid span):

| family | verdict | note |
|---|---|---|
| sector_mom | 2/9, **−1.57 pp/yr** | least-bad (frontier), still loses |
| glb (Green Line Breakout) | 2/9, −4.74 | timing out of a bull decade costs |
| best_bh (control) | 4/9, −6.29 | even the best hold lagged SPY |
| gmi_inv | 2/9, **−7.56** | worst — the −3×/−2× legs made it worse, not better |

**Nothing beat buy-and-hold SPY.** The 2010s were a relentless SPY bull; every
strategy that stepped out of the index, shorted it, or diluted into sectors
paid for it, and the leveraged-inverse legs were the single worst family. The
historical shot confirmed it: `sector_mom` on the reserved 2023→2026 span made
$12,858 vs SPY's $18,113, −11.82 pp/yr. The clean test is **forward**
(`alloc9.FORWARD`, `sector_mom` on `U_WISH`, ripe ~2027), refusing spent shot /
unripe tape / undeclared family — all three refusals are tests.

## The careful Wish bench (v10)

```
python -m experiments.allocation10                              # careful GMI switch, 5%-stop GLB
python -m experiments.allocation10 --holdout gmi_switch         # historical shot (fires once)
python -m experiments.allocation10 --holdout gmi_switch --forward   # refuses until ~2027
```

v10 answers the operator's refinement of the Wish brief: *"when the GMI signal
is red, switch from buy-and-hold to an inverse or something else, and when it's
green switch back … green line breakouts sell if the stock falls ~5% off its
all-time high."* Two corrections over v9's crude versions:

- **GMI is a count, not one moving average.** The real GMI (Wish, 2005) is a
  0–6 tally of six mostly-QQQ trend/breadth indicators (green ≥3, defensive
  below 4, cash below 3). `gmi_switch` builds a 6-component **GMI-lite** and
  switches on a *hysteresis* band — leave green only below 3, re-enter only at
  4 — which kills the whipsaw that made v9's single-MA `gmi_inv` the worst
  family. The red destination — inverse **or** cash/bonds/gold — is a bench
  parameter, so "an inverse or something else" is decided head-to-head.
- **GLB exits on a 5% stop from the high, not a moving average.** `glb_pct`
  rides an all-time-high breakout and sells only when the close falls p% below
  the running high (p ∈ {3,5,8}%). `gmi_glb` gates GLB entries on GMI-green, as
  Wish actually trades. `best_bh` is the control.

*GMI-lite is a disclosed approximation:* the true GMI needs new-high breadth
over ~4,000 stocks the repo has no tape for; GMI-lite is a QQQ/SPY vs
50/150/200-day trend count plus a narrow {spy,qqq,ita,itb} breadth proxy.

Measured (careful-wish bench, 9 OOS windows, 2010→2022 grid span):

| family | verdict | note |
|---|---|---|
| gmi_switch | 4/9, **−2.84 pp/yr** | frontier — roughly *half* v9's crude gmi_inv drag |
| best_bh (control) | 4/9, −6.29 | the bull-decade hold still led the timers |
| glb_pct (5% stop) | 1/9, −6.81 | percent stop no better than v9's MA stop |
| gmi_glb | 1/9, −7.02 | gating GLB on GMI did not save it |

**Care helped, but nothing beat SPY.** The careful hysteresis switch halved the
crude version's drag — real progress — yet the 2010s bull still rewarded simply
holding the index. The historical shot is the honest punchline: `gmi_switch`
re-selected **[R=qqq, D=gld]** and *nominally* beat SPY on the reserved
2023→2026 span ($18,264 vs $18,113, +0.30 pp/yr) — but the margin is razor-thin,
it **loses at 2× and 5× costs**, its **drawdown was worse than SPY's** (19.8% vs
19.0% — the timing didn't even buy downside protection), and the selection
**rejected every inverse ETF in favor of gold**. So "an inverse or something
else?" — *something else* (flee-to-safety) won, exactly as v8 and v9 found. The
clean test is **forward** (`alloc10.FORWARD`, `gmi_switch` on `U_WISH2`, ripe
~2027), refusing spent shot / unripe tape / undeclared family — all three
refusals are tests.

## The regime-gated rotation bench (v11)

```
python -m experiments.allocation11                              # gate the rotation on the GMI regime
python -m experiments.allocation11 --holdout pure_mom --forward     # clean forward shot, refuses until ~2027
```

v11 answers *"work on former promising ideas, and this current one too."* The two
idea-lines each have the flaw the other fixes, so v11 **fuses** them:

- **Former promising idea — cross-asset momentum rotation.** The only edge ever
  validated out of sample (v5, +18 pp/yr on its holdout). It earns from
  *dispersion* — rotate into the strongest asset — but it is **slow**: it rides a
  crash for weeks before the trailing return turns the pick defensive. Upside, no
  brake.
- **This current idea — GMI regime timing (v10).** A 6-count regime read with a
  hysteresis band. On a single index in a correlated bull it only ever
  *sacrificed* upside (v8/v9/v10, three times). Brake, no upside.

`gated_mom` gates the rotation on the regime: **green → rotate into the strongest
RISK asset** (btc/eth/spy/qqq/iwm/efa — momentum's upside, crypto included);
**red → step to a cash/gld/tlt safe sleeve** (timing's brake). Universe = the
8-asset crypto-era set (2017→2026, bound by the Binance tapes) — the widest
genuine dispersion available, holding the 2018/2020/2022 drawdowns for the brake
to matter. No inverse or leveraged ETFs (they decay — v8/v9/v10); the safe sleeve
is long-only. `pure_mom` (ungated) and `gmi_bh` (timing alone) are the two
references; `best_bh` the passive control.

Measured (regime-gated bench, 9 OOS windows, 2017→2026 crypto-era span):

| family | verdict | note |
|---|---|---|
| pure_mom (ungated) | 6/9, **+105.46 pp/yr** | frontier — the validated dispersion edge, re-appearing |
| gated_mom (the synthesis) | 7/9, **+61.03 pp/yr** | most consistent, but the brake halves the return |
| gmi_bh (timing alone) | 4/9, −3.35 | NO-EDGE — a single-index timer, no upside |
| best_bh (control) | 3/9, −11.34 | NO-EDGE |

**The bench isolates two questions.** *Does momentum help the brake?* Emphatically
yes — adding the rotation turns the losing `gmi_bh` timer (−3.35) into the strong
`gated_mom` winner (+61); the upside capture is the whole story. *Does the brake
help momentum?* On raw return, no — the GMI equity-breadth gate pulls out of
crypto during crypto's biggest runs, roughly *halving* the edge (105 → 61 pp/yr);
it buys marginal consistency (7/9 vs 6/9 windows) at a steep cost. So on a
high-dispersion universe **momentum is the engine and the GMI brake is a drag** —
the exact mirror of the equity-only benches, where the brake's *absence* of
upside was the whole problem.

Every historical span is spent (the v5 shot consumed this same crypto calendar's
tail), so v11 fires **no historical shot** — the historical `--holdout` refuses,
pointing to `--forward`. The one clean test is **forward** (`alloc11.FORWARD`,
`pure_mom` on the 8-asset universe, cutoff 2026-07-23, ripe ~2027). The frontier
*rule* ("highest mean OOS delta") was fixed in the spec before results, so the
forward names `pure_mom`, not the flashier `gated_mom` — picking the novel idea
post-hoc would be the cherry-pick the discipline exists to prevent.

## The risk-budget rotation bench (v12)

```
python -m experiments.allocation12                              # limit risk by SIZING, not timing
python -m experiments.allocation12 --holdout pure_mom --forward     # clean forward shot, refuses until ~2027
```

v12 answers *"risk is fine as long as you limit it"* — and answers it with the
one lever every prior bench left untouched. v4–v11 were all-or-nothing (weight
1.0 on a single asset), and eleven versions proved downside *timing* is a drag
(inverse ETFs, GLB stops, the GMI gate, even that gate fused onto momentum — each
lost or clipped the edge). So v12 does not add another brake. It keeps the
validated momentum engine and makes the **weight** the object of study:

- **`vt_mom`** — volatility-targeted top-1 momentum: `w = min(1.0, target /
  realized vol)` of the pick. A calm asset is held full; a hot one (crypto in a
  vol spike) is sized down toward cash. The `min(1.0, …)` clamp is the
  no-leverage red line — **v12 only ever scales a position down, never up.**
- **`rp_topk`** — risk parity across the top-K momentum names, inverse-vol
  weighted, normalized to 1.0. Diversifies `pure_mom`'s single-asset
  concentration without a timing gate.
- **`pure_mom`** (full-size top-1, the v11 frontier) and **`best_bh`** are the
  controls. Universe = the 8-asset crypto-era set (2017→2026).

**The frontier metric was changed to risk-adjusted — pre-declared, for the "limit
risk" mandate.** Prior benches ranked by raw mean OOS delta; v12's spec (before
the run) fixes the frontier as `score = mean OOS delta / max(mean OOS maxDD_pp,
5.0)` — return per point of downside.

Measured (risk-budget bench, 9 OOS windows, 2017→2026 crypto-era span):

| family | verdict | mean delta | mean maxDD | risk-adj score |
|---|---|---|---|---|
| pure_mom (full-size) | 6/9 | **+105.46 pp/yr** | 36.4% | **+2.895** (frontier) |
| rp_topk (risk parity) | 4/9 | +25.06 | 31.3% | +0.801 |
| vt_mom (vol target) | 5/9 | +23.45 | 30.8% | +0.760 |
| best_bh (control) | 3/9 | −11.34 | 47.3% | −0.240 |

**The result is a clean negative.** The metric was chosen *specifically* to reward
risk-limiting, and the full-size control still wins it — the two risk-limited
families lose on return-per-unit-drawdown, not just on raw return. Sizing to a
budget removed ~78% of the return to shave ~15% of the drawdown, because the
momentum edge lives precisely in the **high-vol crypto winners that vol targeting
caps.** Limiting risk by sizing costs the edge; on this universe you are paid for
holding the volatility, not for damping it.

Every historical span is spent (the v5 shot consumed this same crypto calendar's
tail), so v12 fires **no historical shot**. The spec pre-declared both forward
branches; since `pure_mom` won the risk-adjusted score, `alloc12.FORWARD` names it
(ripe ~2027) — the same family/calendar as `alloc11.FORWARD`, disclosed in the
file as **confirmatory, not a new mechanism test.** Arming a risk-limited family
that lost would be the reverse cherry-pick; the discipline arms whatever the rule
names.

## The cross-section bench (v13)

```
python -m experiments.allocation13                              # momentum ACROSS 66 large-cap stocks
python -m experiments.allocation13 --holdout xs_topk --forward       # clean forward shot, refuses until ripe
```

Every prior bench rotated a handful of asset-class ETFs (+ crypto). v13 answers
*"what if you run momentum across all stocks — big companies list hundreds that
are doing well?"* — the classic cross-sectional momentum factor. Out of a
**fixed 66-name large-cap US universe**, each month own the strongest few by
trailing return, long-only, exposure ≤ 1.0.

Two honest departures from the ETF machinery:

- **Masked universe.** Stocks list at different times (AMZN 1997, GOOGL 2004,
  META 2012). A name is `None` — invisible to the ranker — until it has real
  history, so the universe *grows* over time (56 names listed in 1993, 66 by
  2026) instead of truncating to the youngest tape.
- **Survivorship is measured, not hidden.** A modern large-cap list is a basket
  of *survivors*: the companies that went to zero (Lehman, Enron, the dot-coms)
  are gone, and even WBA 404'd on fetch because Walgreens was taken private in
  2025. That flatters any long-biased backtest. So the bench includes **`ew_all`**
  — own *every* listed name equal-weight, zero selection skill — as the control.

Families: **`xs_topk`** (top-K by momentum, equal weight, short-fall → cash),
**`xs_invvol`** (top-K inverse-vol weighted), **`ew_all`** (survivorship control),
**`best_bh`** (chase last window's single hottest name).

Measured (9 OOS walk-forward windows, 1993→2026, base-venue tolls):

| family | verdict vs SPY | mean OOS delta |
|---|---|---|
| xs_topk (top-K momentum) | **9/9** | **+23.82 pp/yr** (frontier) |
| xs_invvol (risk parity) | 9/9 | +19.14 |
| best_bh (chase the winner) | 9/9 | +16.82 |
| **ew_all (survivorship control)** | 8/9 | **+6.65** |

**Read this carefully — the honest signal is not the +23.82 vs SPY.** `ew_all`,
with *zero* skill, already beats SPY 8/9 at +6.65 pp/yr: that is pure
survivorship beta, and you cannot buy "1993's survivors-of-2026" in 1993. The
real, bias-controlled test is **`xs_topk` vs `ew_all`** — both share the
*identical* biased universe, so the survivor inflation cancels. There, momentum
wins **9/9 windows** by ~+17 pp/yr. Cross-sectional momentum genuinely adds
skill *on top of* its universe; roughly ~7 of the 23.8 pp/yr is survivor
inflation and ~17 is real edge.

That 9/9-over-the-neutral-control is the strongest cross-sectional evidence in
the repo — so `xs_topk` [K=5, L=63] earns a **forward holdout**
(`data/holdout/alloc13.FORWARD`), fired only on virgin post-2026-07-17 rows.
The bench carves no historical shot: on a survivorship-shaped universe the
absolute in-sample number is inflated by construction, and only forward data is
clean.

## The survivorship stress bench (v14)

```
python -m experiments.allocation14 --mode all      # bench + regime-direction + graveyard stress
```

v13 disclosed that its vs-SPY number is survivor-inflated and controlled the
*level* of that inflation with `ew_all`. v14 asks the sharper questions: does the
canonical academic signal help, which *direction* does survivorship bias run, and
**would putting the dead companies back into the ranking pool blow up the
concentrated top-K book?**

The literal test — fetch the graveyard, re-rank — turns out to be **impossible
with honest data.** Yahoo's chart API (the only network tool this repo allows)
does not serve delisted price history: real casualties 404 (LEH, ENE, WCOM,
BSC…), the post-bankruptcy "Q" tickers resolve to empty shells (LEHMQ, ENRNQ,
WAMUQ…), and reusable symbols (WB, CC, SHLD, GM) point at *new* companies, not
the dead originals. Fabricating a tape and calling it history would violate the
repo's integrity, so v14 answers three honest ways instead:

- **`xs_skip` — the 12–1 signal (skip the recent month).** A clean negative:
  **+18.63 pp/yr (9/9)** vs raw `xs_topk`'s +23.82. Skipping short-term reversal
  *hurts* by ~5 pp/yr here — recent-month momentum is additive in this universe,
  not reversal. The textbook tweak loses; raw momentum wins.
- **Survivorship *direction* (real data).** Attribute `xs_topk`'s daily
  log-edge over `ew_all` by SPY regime (≥ / < its 200-day MA): **+3.54 in bull
  regimes, −1.06 in bear.** Momentum's *entire* edge is bull-market winner-
  chasing, and it actually **loses to the equal-weight survivor basket in
  down-markets.** So survivorship **inflates** the edge — it is *not* a defensive
  cash-exit that a real graveyard would flatter.
- **Synthetic graveyard stress (labeled, seeded — not history).** Inject phantom
  "landmines" that rise (get chased) then collapse −95% and delist. The lever is
  collapse *speed*: under a slow bleed momentum's cash-exit escapes and `ew_all`
  eats the loss; under a **1-day gap** (bankruptcy filing / fraud reveal) the
  concentrated book is hit harder. Result: the `xs_topk − ew_all` edge **survives
  even single-day gaps up to ~2.4 delistings/yr**, but the gap roughly halves its
  intensity-scaling (edge +8.4 vs +16.2 at ~1.2/yr) and widens its variance —
  concentration *is* more fragile to sudden landmines, just not fatally so.

Net: cross-sectional momentum's skill over its universe is real and graveyard-
robust, but its *absolute* return is a bull-market, survivor-flattered upper
bound with no downside protection over simply owning the whole universe.
`xs_skip` is worse than the already-armed `xs_topk`, so v14 arms **no new
forward** — re-arming a weaker near-duplicate would be a cherry-pick.

## Money conservation, stated plainly

Every movement of money is one ledger row with a debit and a credit account.
There is no other way money moves. At all times,

```
SUM(all account balances) == initial_treasury_u
```

and every cached balance equals the ledger-derived sum for its account. A
fast O(accounts) check runs on cadence (every tick under the daemon); the
full O(ledger) audit runs at run boundaries and on `colony verify`. Any
violation raises and halts the run. The nightly replay-twin extends the same
guarantee across days: the live ledger must be byte-identical to an offline
replay of its own journal.

## Safety by construction

- **Simulation only.** Virtual money; no payment rails and **no
  order-placement code anywhere in the repository**. The core makes no
  network calls; the only network code is in `tools/`, and it only *reads*
  public market data.
- **No self-modification.** Genomes change only between generations, via the
  orchestrator's genetic operators; agents cannot rewrite themselves or the
  rules. The bank stores parameter dictionaries, never code, and a running
  colony never reads it.
- **Airtight accounting.** Double-entry ledger, integer micro-dollars,
  conservation checked continuously, crash-on-violation.
- Remaining seams (an LLM cognition layer, treasury withdrawal, order
  execution) stay documented interfaces only — none of that code exists here.

## CI

GitHub Actions runs the full suite on **Windows and Linux** (the daemon's
pid-liveness, subprocess supervision, and hard-kill resume tests included),
plus a 500-tick smoke simulation with a full ledger audit. The throughput
benchmark enforces ≥250 ticks/s in CI (≥500 documented on the reference
laptop in `records/`).

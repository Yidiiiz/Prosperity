# Build spec v12 — the Risk-Budget Rotation Bench

## 0. Premise

The operator's brief this round: *"work on the next version. Please both improve
and change as you need. Work on both old ideas and try new things you think might
work. do research as needed and go for success. risk is fine as long as you limit
it."*

Two things are settled by eleven prior versions and must anchor this one:

1. **Cross-asset momentum rotation on a high-dispersion (crypto-era) universe is
   the only mechanism ever validated out of sample** (v5, +18 pp/yr on its
   holdout; re-appears as the v11 frontier, pure_mom +105 pp/yr in-grid).
2. **Downside *timing* is a drag.** Every attempt to add a brake by getting OUT
   of the market — inverse ETFs (v8), GLB stops (v9), a GMI regime gate (v10),
   even that gate fused onto momentum (v11) — either lost to buy-and-hold or
   clipped the dispersion edge. Timing sacrifices upside; on this universe upside
   *is* the edge.

So the honest way to answer *"risk is fine as long as you limit it"* is **not**
another timing brake. It is to keep the validated engine fully invested in spirit
but **limit risk through position sizing** — hold the momentum pick, but size it
so a fixed risk budget is respected. This is the one lever every prior bench left
untouched: v4–v11 were all all-or-nothing (weight 1.0 on a single asset). v12
makes the *weight* the object of study.

This is a genuinely new mechanism (never tested here), it builds on the only
validated edge rather than fighting it, and it takes risk seriously without ever
selling the upside outright. The research question it settles: **does sizing the
momentum rotation to a risk target keep enough of the return to be worth the
drawdown it removes?** Constant leverage-scaling cannot change a return/drawdown
ratio (both scale together); vol targeting changes it *only* if trailing
volatility predicts forward drawdowns better than forward returns — a real,
documented effect in crypto (vol clusters ahead of crashes). v12 measures whether
that effect survives walk-forward on this universe.

## 1. Universe

`U_RISK = U_FULL` — v6's 8-asset crypto-era set, bound by the 2017 Binance tapes:
`btc, eth, spy, qqq, iwm, efa, gld, tlt`. This is the widest genuine dispersion
available and holds the 2018/2020/2022 drawdowns that make a risk budget matter.

- **RISK sleeve** (momentum candidates): `btc, eth, spy, qqq, iwm, efa`.
- **Cash** is the only de-risking destination. No safe-sleeve rotation, no inverse
  or leveraged ETFs (they decay — v8/v9/v10), no shorting, no margin. Un-invested
  weight sits in cash. Portfolio exposure is always ≤ 1.0 — **v12 only ever scales
  a position DOWN, never up.** That is the entire risk-limit mechanism and the
  standing red line, in one sentence.

## 2. Families and grids

Reuses v6 machinery verbatim (`load_joint`, `momentum_ranked`, `rebalance`,
`judge`, `COST_LADDER`, `split_bounds`). Monthly cadence `REBAL = 21` throughout.
Grid order is pre-declared and breaks train-window ties (earlier entry wins).

- **`pure_mom`** — the control and the v11 frontier: top-1 momentum over the RISK
  sleeve at full weight 1.0 (cash only when no pick has a positive trailing
  return). Grid: `L ∈ {63, 126, 252}`.

- **`vt_mom`** *(new)* — **volatility-targeted** top-1 momentum. Same pick as
  `pure_mom`, but the weight is `w = min(1.0, target_daily / realized_daily)`
  where `target_daily = TV / sqrt(252)` and `realized_daily` is the population
  stdev of the pick's trailing `V`-day log returns. The `min(1.0, …)` clamp is
  the no-leverage red line: a calm asset is held full, a hot asset (crypto in a
  vol spike) is sized down toward cash. Grid: `L ∈ {63, 126, 252} × TV ∈ {0.20,
  0.40, 0.80}` (annualized vol target), `V = 21` fixed.

- **`rp_topk`** *(new)* — **risk-parity** across the top-K momentum picks: take the
  top `K` names with positive trailing return, weight each by `1/vol` (trailing
  `V=63`-day stdev) and normalize to sum 1.0 (cash if none qualify). Diversifies
  the single-asset concentration `pure_mom` carries, without a timing gate. Grid:
  `K ∈ {2, 3} × L ∈ {63, 126, 252}`, `V = 63` fixed.

- **`best_bh`** — passive buy-and-hold of each asset; the SPY-relative control.
  Grid: one entry per asset in `U_RISK`.

## 3. Bench protocol

Joint daily calendar, 1-day signal lag (day `i` uses history ≤ `i−1`), fresh
$10,000 in integer micro-dollars, full liquidation at each window's last close.
Base venue = 10 bps taker + 2 bps spread. Walk-forward over `--windows` (default
10) equal blocks: train window `k` selects the params with the highest audited
final cash (grid-order tiebreak); the frozen selection is tested on window `k+1`.
A window *wins* iff cash > SPY buy-and-hold over the same window at base costs.
Family verdict `BEATS-SPX` iff `wins × 2 > tests`.

**Frontier rule — changed from v11, for the operator's stated mandate.** Prior
benches ranked families by raw mean OOS delta. Because the brief is explicitly
*"limit risk,"* v12 pre-declares a **risk-adjusted** frontier, fixed here before
any result is seen:

> For each family, over the OOS windows, compute the mean OOS delta vs SPY
> (`mean_delta`, pp/yr) and the mean OOS maximum drawdown (`mean_dd`, in
> percentage points). The **frontier** is the family maximizing
> `score = mean_delta / max(mean_dd, 5.0)` — return earned per point of downside
> risk, with a 5-pp drawdown floor so a near-flat family cannot win on a division
> artifact.

`mean_delta`, `mean_dd`, and `score` are reported for **every** family, so the
raw-return ranking stays visible next to the risk-adjusted one. The verdict
(`BEATS-SPX`) is unchanged and still win-count based, for comparability with
v4–v11. Only the *frontier selection* (hence the forward-holdout target) uses the
risk-adjusted score. This change is disclosed in DECISIONS and the README; it is
not a post-hoc metric swap — it is declared in this spec, before the run.

**Diagnostics (never change a verdict):** the OOS max-drawdown per window (drives
the frontier score above); and, on the forward shot only, the v6 `COST_LADDER`
(2× and 5× friction) plus a SPY-drawdown comparison.

## 4. Holdout discipline — forward only

Every historical span on this calendar is spent: the v5 one-shot already consumed
the 2017→2026 crypto tail (documented in `data/holdout/*.SHOT` and memory). So
**v12 carves no historical holdout and fires no historical shot.** The historical
`--holdout` path refuses (exit 2) and points at `--forward`.

One clean **forward** shot is registered: `data/holdout/alloc12.FORWARD` names the
**frontier family by the §3 risk-adjusted rule**, on `U_RISK`, cutoff 2026-07-23,
`min_new_rows = 126`. It fires exactly once, only on rows that postdate the
cutoff, only for the declared family, re-selecting that family's params on the
pre-cutoff rows and freezing them; it writes `alloc12.SHOT` and reruns refuse.
Because the frontier rule is fixed above before the run, whatever it names is not
a cherry-pick — if a risk-limited family wins the risk-adjusted score, the forward
tests the new mechanism; if `pure_mom` wins even after risk-adjustment, the
forward names it and the honest finding is that raw dispersion dominates downside.

## 5. Acceptance / tests

`tests/test_allocation_v12.py`:

- **Leakage** — corrupting closes at `≥ i` never changes any family's day-`i`
  targets.
- **`vt_mom` caps a hot pick** — when the momentum leader is high-vol, its weight
  is `< 1.0` with the remainder in cash; a smoothly-rising low-vol leader is held
  full (weight `1.0`); the weight never exceeds `1.0` even when `TV ≫` realized
  vol (no-leverage clamp).
- **`rp_topk` risk-parity** — with two positive picks the lower-vol name gets the
  larger weight; the weights sum to 1.0 when at least one pick qualifies, and to
  cash when none do.
- **Invariants** — weights never exceed 1.0 across the real grid; a flat tape
  never creates money; the RISK sleeve excludes gld/tlt.
- **Discipline** — historical `--holdout` without `--forward` refuses (exit 2,
  "every span is spent"); forward refuses on a spent shot, an unripe span, and an
  undeclared family; the FORWARD declaration names a real family on `universe:
  risk` with `min_new_rows ≥ 126`.

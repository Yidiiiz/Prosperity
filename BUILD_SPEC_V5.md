# BUILD SPEC v5 — the Allocation Bench

Operator directive (2026-07-19): "please brainstorm ideas to beat s&p and keep
testing." v4 answered the *frequency* question: daily > hourly ≫ minute >
second, and even the daily winners were beta that failed the one-shot holdout.
v5 moves to the axis v4 could not test: **which assets you hold and when**,
rather than how fast you trade one of them. Everything runs at daily cadence —
the only cadence v4 found alive.

## 1. The brainstorm — four families and a control

Every idea below is a *hand-crafted, parameterized* daily strategy over the
four assets v4 taped: SPY, QQQ, BTCUSDT, ETHUSDT. No evolution, no RNG — the
families are deterministic, so there are no seeds; robustness comes from the
test windows and the holdout (deviation from the three-seed convention,
recorded in DECISIONS).

| family | idea | parameter grid (pre-declared, frozen here) |
|---|---|---|
| `dual_momentum` | every 21 trading days, rank assets by trailing L-day return; hold the top asset if its return > 0, else cash | L ∈ {63, 126, 252} |
| `trend` | hold asset A while close > SMA(L), else cash | A ∈ {spy, qqq, btc, eth} × L ∈ {100, 200} |
| `equal_weight` | hold all four at equal weight, rebalance every R trading days (harvest the rebalancing premium) | R ∈ {21, 63} |
| `vol_target` | hold asset A at exposure min(1, T / realized 20-day vol), rest in cash | A ∈ {spy, qqq, btc, eth} × T ∈ {0.10, 0.20} |
| `best_bh` (control) | buy-and-hold whichever single asset won the train window | — (selection is the parameter) |

`best_bh` is the beta control: if a family only ties it, that family is asset
selection wearing a costume. **No leverage anywhere** — a financing-cost model
would be invented rather than measured, and an invented cost is exactly the
kind of optimism v4 existed to kill. Exposure is capped at 1.0.

## 2. The joint calendar

Master clock = SPY trading days restricted to the span where all four tapes
exist (2017-08-17 → last common day). Crypto trades weekends; each SPY day
samples the crypto close at the latest bar ≤ that day. Signals on day t may
use any history ≤ t, including pre-window history (lookback warmup is past
data — leakage is only *future* data). All fills at that day's close through
`risk.buy_price_u/sell_price_u/fee_u` at **base venue costs** (10 bps taker,
2 bps half-spread-rounded, per side) — the same tolls every v3/v4 agent paid.
Lot denominators: 100 for SPY/QQQ, 100 000 for BTC/ETH. Capital
$10,000 per run, integer micro-dollar accounting throughout.

## 3. Walk-forward protocol

1. Carve the final 20% of the joint calendar (by rows) to
   `data/holdout/alloc.csv` before anything else sees it.
2. Split the remaining 80% into K = 8 contiguous windows.
3. For k = 1..7: **train** on window k (evaluate every parameter combo in the
   family's grid; select the one with the highest audited final cash),
   **test** the frozen selection on window k+1.
4. A test window is a *win* iff the family's audited cash beats SPY
   buy-and-hold over the same calendar window at base costs (v4 §2 yardstick,
   same-window comparison only, coverage rules unchanged).

Verdicts per family: **BEATS-SPX** iff wins × 2 > tests (strict majority,
v4 §5); **NO-EDGE** otherwise; **FAIL** is reserved for machinery. The
measurement is the deliverable — NO-EDGE is a passing outcome.

## 4. The holdout (one-shot, pre-registered)

The single family with the **highest mean OOS delta vs SPY (pp/yr) across its
test windows** gets one shot on `data/holdout/alloc.csv`. Its parameters are
re-selected once on the full 80% grid span, frozen, then run on the holdout.
The shot writes `data/holdout/alloc.SHOT` (committed via the gitignore
exception) and any rerun refuses. This rule is written before any v5 code
runs and must not be overridden post hoc, even if another family's verdicts
look better (v4 §6.2 precedent: btc_1d).

## 5. Acceptance

- Records under `records/experiments/` with the mandatory footer
  (span/wall/annualized/benchmark) and the spx line per judged window.
- Tests cover: calendar join (weekend sampling), cost accounting round-trip,
  train/test boundary (no future data in selection), holdout guard refusal.
- DECISIONS entries for every ratified rule in this spec.
- README gains a v5 section reporting whatever the bench measured.

## 6. Red lines (unchanged from v4 §11)

Virtual money only. No order-placement code anywhere in the repository. No
self-modification. The core makes no network calls; `tools/` only reads
public market data.

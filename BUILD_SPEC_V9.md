# BUILD_SPEC_V9 — the Wish Bench (Green Line Breakout, GMI-gated inverse, sectors)

## 0. Why v9

Operator directive: implement Dr. Eric Wish's strategies (Green Line
Breakout and others), do leveraged inverse ETFs "correctly" (his ~3× point),
and add defensive/thematic sectors (construction, defense). v9 does all three
with v4–v8 discipline and settles the 3× question empirically.

**The 3× fact, measured, not argued.** −3× products (SQQQ, SPXU) target 3×
the *daily* inverse return — confirmed: SQQQ's daily beta vs QQQ is **−2.96**.
But daily reset means multi-day compounding decays them: **buy-and-hold SQQQ
2010→2026 went to ≈$0 while QQQ rose 15.9×.** So a −3× ETF is a short-holding
tactical instrument gated by a timing signal, never a hold. v9 tests whether a
real gate (Wish's market-timing idea) can extract value from −3× despite the
drag, head-to-head against −1× and cash.

## 1. Universe (fixed for v9)

`U_WISH` = sorted(`gld, ita, itb, psq, qqq, sds, sh, spxu, sqqq, spy, tlt`),
joint calendar bound by SQQQ inception (2010-02-11). New real tapes:

| asset | tape | role |
|---|---|---|
| sqqq | data/sqqq_d.csv | **−3× Nasdaq-100** (ProShares UltraPro Short QQQ) |
| spxu | data/spxu_d.csv | **−3× S&P 500** (ProShares UltraPro Short S&P500) |
| sds  | data/sds_d.csv  | **−2× S&P 500** (ProShares UltraShort S&P500) |
| ita  | data/ita_d.csv  | US aerospace & **defense** (war companies) |
| itb  | data/itb_d.csv  | US home **construction** (homebuilders) |

Carried from v6/v8: spy, qqq, gld, tlt, sh (−1×), psq (−1×). All real listed
products, all held as ordinary long positions (leverage is internal to the
fund; portfolio exposure ≤ 1.0, no margin — the no-leverage red line holds).

`MOM_U` = (`spy, qqq, gld, tlt, ita, itb`) — the long-only sleeve the sector
rotor ranks over (never rotates into a decaying leveraged inverse).

Same machinery as v6/v8: joint calendar, 1-day signal lag (day *i* uses
history ≤ *i*−1), base venue (10 bps taker + 2 bps spread), integer
micro-dollars, `CAPITAL_U = $10,000`, no seeds.

## 2. Families (pre-declared; grid order breaks train-window ties)

| family | grid | behaviour |
|---|---|---|
| `glb` | R∈{qqq,spy} × S∈{150,210} | **Green Line Breakout** (Wish). Enter R when its close exceeds the highest close that is ≥63 trading days (~3 months) old — an all-time high that has *held* 3 months; ride with a 30-/42-week MA(S) stop; exit to cash below it. |
| `gmi_inv` | (R,I)∈{(qqq,psq),(qqq,sqqq),(spy,sh),(spy,spxu),(spy,sds)} × S∈{150,210} | **GMI-lite market timing → inverse.** Risk-on (R above its 30-/42-week MA) → hold R; risk-off → hold the inverse I. The (qqq,psq) vs (qqq,sqqq) and (spy,sh) vs (spy,spxu/sds) pairs answer the professor's question directly: does −3×/−2× beat −1× under the same gate? |
| `sector_mom` | L∈{63,126,252} × K∈{1,2} | monthly top-K momentum over `MOM_U` — does adding defense/construction create dispersion the rotor can use? |
| `best_bh` | one per asset in `U_WISH` | buy-and-hold control. |

**Green Line Breakout, operationalized (deterministic).** Green line at day
*j* = `max(close[R][0 .. j−63])` (highest close at least ~3 months old).
Breakout = `close[R][j] > green_line`; enter long on the first such day while
flat. Stop = `close[R][j] < mean(close[R][j−S+1 .. j])`; exit to cash. This
is a faithful, testable form of Wish's rule: buy confirmed all-time-high
breakouts, ride under the 30-week (150d) / 42-week (210d) average.

The full 6-component GMI (breadth, new-highs, IBD lists) needs market-internals
data this repo does not carry; `gmi_inv` uses the QQQ-vs-30-week-MA core of
Wish's timing as a faithful proxy, disclosed as a simplification.

## 3. Bench

`--bench wish` (only bench). Walk-forward over the grid span (first 80% of
`U_WISH`'s joint calendar): train window selects the best combo by audited
final cash (grid-order tiebreak), the frozen selection is judged same-window
vs SPY buy-and-hold at base venue costs on the next window. BEATS-SPX iff a
strict majority of test windows win. Frontier = highest mean OOS delta.

## 4. Holdout — historical shot + forward shot (v6/v8 precedent)

The 2018/2020/2022 drawdowns in this span are known to the author, so a
timing strategy designed today carries researcher-degrees-of-freedom
contamination. v9 discloses it and hedges both ways:

- **Historical carve:** final 20% of `U_WISH`'s calendar → `alloc9.csv`,
  never in bench selection; the frontier fires once (`--holdout FAMILY`) with
  the 2×/5× cost ladder. Guarded by `alloc9.SHOT`; reruns refuse. Disclosure
  in the record.
- **Forward registration:** `alloc9.FORWARD` pre-declares the frontier on
  `U_WISH`, cutoff 2026-07-23, min 126 rows postdating it; fires only on data
  that does not yet exist (`--holdout FAMILY --forward`). Refuses spent shot /
  unripe tape / undeclared family (tests). Ripe ~2027.

Pre-registered one-shot rule: highest mean OOS delta across the bench windows.

## 5. Tests (tests/test_allocation_v9.py)

1. **leakage** — future-corrupting closes past day *i* leaves every family's
   targets unchanged.
2. **GLB mechanics** — on a synthetic tape that breaks a 3-month-old high the
   `glb` family enters; when it falls below the MA stop it exits to cash.
3. **gmi switch** — melt-up holds R, breakdown flips to the inverse I; the
   grid actually reaches the −3× products (sqqq/spxu selectable).
4. **no daily churn** — timed families emit `None` on unchanged days.
5. **sector_mom bridge** — K=1 over `MOM_U` equals v6 `dm_topk` K=1.
6. **weights ≤ 1.0** and **flat tape never creates money**.
7. **forward refusals** (spent/​unripe/​undeclared) and **FORWARD integrity**.

## 6. Red lines (unchanged)

Virtual money only; no order-placement code; core makes no network calls
(only tools/fetch_market_data.py); no self-modification. Leveraged and inverse
ETFs are ordinary long positions in real listed products — no synthetic
shorting, no margin, portfolio exposure ≤ 1.0.

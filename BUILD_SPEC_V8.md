# BUILD_SPEC_V8 — the Regime Bench (bull/bear timing + inverse ETFs)

## 0. Why v8

The operator learned the market idea that motivates this build: markets have
**bull** and **bear** regimes, and one can hold **inverse ETFs** to profit
while the market falls rather than only retreating to cash. v8 tests that
hypothesis with the same discipline as v4–v7.

Current knowledge carried in:

- **v6/v7 finding:** cross-asset momentum's edge is *dispersion* — it wins
  where return streams diverge. A market selloff *creates* dispersion between
  a long index and its inverse. So an inverse instrument is not just a hedge;
  it is a second, anti-correlated return stream a momentum rotor can rotate
  into. v8's `mom_inv` family is that idea made literal.
- **v6 control lesson:** in a correlated equity world the thing to beat is
  buy-and-hold beta (`best_bh`), because trend/defensive families paid tolls
  to whipsaw. v8 keeps `best_bh` as the yardstick-anchored control and asks
  whether *regime timing with a downside instrument* can finally beat it.
- **v6 carve + v7 forward discipline, combined (§4).**

## 1. Universe (fixed for v8)

`U_DIR` = sorted(`gld, psq, qqq, sh, spy, tlt`), joint calendar bound by the
inverse-ETF inceptions (SH, PSQ start 2006-06-21). Tapes:

| asset | tape | role |
|---|---|---|
| spy | data/spy_d.csv | risk-on long, yardstick |
| qqq | data/qqq_d.csv | risk-on long (growth) |
| sh  | data/sh_d.csv  | **−1× S&P 500** (ProShares Short S&P500) |
| psq | data/psq_d.csv | **−1× Nasdaq-100** (ProShares Short QQQ) |
| gld | data/gld_d.csv | flight-to-safety |
| tlt | data/tlt_d.csv | flight-to-safety |

Real inverse-ETF tapes are used deliberately: their daily-reset **volatility
drag and expense** are baked into the closes, so the test pays the true cost
of holding an inverse product — no synthetic −1× returns that would flatter
the idea.

Same machinery as v6/v7: joint calendar (SPY days both tapes cover, non-SPY
sampled at latest close ≤ each SPY day), 1-day signal lag (day *i* uses
history ≤ *i*−1, fills at *i*'s close), base venue (10 bps taker + 2 bps
spread), integer micro-dollars, `CAPITAL_U = $10,000`, no leverage
(exposure ≤ 1.0), no seeds (families are deterministic).

## 2. Families (pre-declared; grid order breaks train-window ties)

Regime signal for the timed families: the **risk-on asset R vs its own
SMA(L)**. Bull ⇔ `C[R][j] > mean(C[R][j−L+1 .. j])`. Timed families trade
only when the regime *changes* (no daily churn); the momentum family
rebalances every 21 days.

| family | grid | behaviour |
|---|---|---|
| `regime_inv`  | L∈{150,200} × (R,I)∈{(spy,sh),(qqq,psq)} | **the operator's idea.** Bull → hold R; Bear → hold the inverse I. |
| `regime_flat` | L∈{150,200} × R∈{spy,qqq} | control: Bull → R; Bear → **cash**. Isolates whether being *short* beats being *flat*. |
| `regime_safe` | L∈{150,200} × R∈{spy,qqq} × S∈{gld,tlt} | alt: Bull → R; Bear → flight-to-safety S. Does shorting beat fleeing? |
| `mom_inv`     | L∈{63,126,252} | monthly top-1 momentum over all of `U_DIR` (r>0 else cash); rotates into an inverse ETF when the selloff makes it the momentum leader. |
| `best_bh`     | one per asset in `U_DIR` | buy-and-hold control. |

`regime_flat`, `regime_safe`, and `regime_inv` share the **same regime clock**
and differ only in what they do in a bear. Their comparison is therefore
*contamination-symmetric*: whatever the researcher knows about past bears
biases all three equally, so the *ranking between them* — does the inverse ETF
add value? — is the robust result even if absolute levels are not.

`mom_inv` at L∈{63,126,252} with the inverse ETFs removed would be exactly
v6 `dm_topk` K=1; the added assets are the only new degree of freedom (bridge
test, §5).

## 3. Bench

`--bench dir` (the only bench). Walk-forward over the grid span (first 80% of
`U_DIR`'s joint calendar): each train window selects the best combo by audited
final cash (grid-order tiebreak), the frozen selection is tested on the next
window and judged same-window vs SPY buy-and-hold at base venue costs. A
family BEATS-SPX iff it wins a strict majority of test windows. The frontier =
family with the highest mean OOS delta.

## 4. Holdout — one historical shot **and** one forward shot

The historical bears in this span (2008, 2020, 2022) are known to the author,
so a bear-timing strategy designed today and tested on that history carries
**researcher-degrees-of-freedom contamination**. v8 discloses this and hedges
it two ways, reusing both precedents:

- **Historical carve (v6 §4 precedent):** the final 20% of `U_DIR`'s calendar
  is reserved to `data/holdout/alloc8.csv` and never enters bench selection.
  The frontier family fires **once** on it (`--holdout FAMILY`), re-selecting
  params on the grid span, with the 2×/5× cost ladder. Guarded by
  `data/holdout/alloc8.SHOT`; reruns refuse. Contamination disclosed in the
  record: the author knew this span held the 2022 bear when designing the
  families — weaker evidence than a virgin window.
- **Forward registration (v7 §4 precedent):** `data/holdout/alloc8.FORWARD`
  pre-declares the same frontier family on `U_DIR`, cutoff 2026-07-23, minimum
  126 joint rows postdating it. It fires only on data that does not yet exist
  (`--holdout FAMILY --forward`), the first *uncontaminated* test of the
  regime idea, ripe ~2027 after refetching tapes. Reruns / unripe tape /
  undeclared family all refuse (tests).

The pre-registered one-shot rule (both shots): **highest mean OOS delta across
the bench's test windows.**

## 5. Tests (tests/test_allocation_v8.py)

1. **leakage** — future-corrupting closes past day *i* leaves every family's
   targets unchanged (signals use only history ≤ *i*−1).
2. **mom_inv bridge** — `mom_inv` L over `U_DIR` minus the inverses reproduces
   v6 `dm_topk` K=1 on the same days.
3. **regime switch** — on a synthetic melt-up R stays long R; on a synthetic
   crash R flips to the inverse I (`regime_inv`) / to cash (`regime_flat`) /
   to S (`regime_safe`).
4. **no daily churn** — a timed family emits `None` (hold) on days the regime
   does not change.
5. **weights never exceed 1.0** and **flat tape never creates money**.
6. **forward refusals** — spent SHOT, unripe tape, undeclared family (rc 2).
7. **FORWARD declaration integrity** — parses and names a real family/universe.

## 6. Red lines (unchanged)

Virtual money only; no order-placement code anywhere; core makes no network
calls (only `tools/fetch_market_data.py` reads public closes); no
self-modification; inverse ETFs are ordinary long positions in real listed
products — no synthetic shorting, no leverage, exposure ≤ 1.0.

# BUILD SPEC v13 — the Cross-Section Bench

## 0. Premise

Every bench through v12 rotated a handful of asset-class ETFs plus crypto. The
operator's v13 question: *run momentum across all stocks* — "big companies list
hundreds of stocks currently doing well." That is the classic cross-sectional
momentum factor (Jegadeesh–Titman 1993): out of a large stock universe, each
month own the strongest few by trailing return. v13 tests exactly that, and — as
the first thing it does — measures how much of any apparent edge is just
**survivorship**, not skill.

Standing red lines (unchanged): virtual money only; no order-placement code; no
shorting; no leverage (exposure ≤ 1.0, long-only); core code makes no network
calls — only `tools/fetch_market_data.py` may.

## 1. Universe

- **Fixed 66-name large-cap US list**, declared in `allocation13.TICKERS` (part
  of the pre-registration — the universe is not globbed from disk). Tapes
  fetched once by `tools/fetch_market_data.py` into `data/stocks/*_d.csv`;
  gitignored like the other operator-fetched big tapes, digests pinned in the
  record. WBA is deliberately absent — it 404'd on fetch (Walgreens taken
  private 2025), a live survivorship deletion.
- **Masked calendar.** Master clock = SPY trading days from SPY's start to the
  earliest tape end (never forward-filling past a tape). Each name is `None`
  before its first trade and samples its latest close ≤ each SPY day thereafter.
  A `None` name is invisible to ranking, vol, and rebalancing. The universe
  grows over time (56 listed at the 1993 open → 66 by 2026), as it did in life.

## 2. Families and grids (pre-declared; grid order breaks train-window ties)

- **`xs_topk`** — top-K by trailing-L return, equal weight `1/K`, ONLY
  positive-momentum names; if fewer than K are positive, the short-fall is cash
  (absolute-momentum de-risking). Grid: K ∈ {5, 10, 20}, L ∈ {63, 126, 252}.
- **`xs_invvol`** — top-K positive by momentum, inverse-vol weighted (63-day
  realized), normalized to 1.0; fully invested whenever ≥1 is positive, cash
  when none. Grid: K ∈ {5, 10, 20}, L ∈ {63, 126, 252}.
- **`ew_all`** — own EVERY listed name equal-weight, rebalanced monthly. Zero
  selection skill: the pure survivorship-beta control. No params.
- **`best_bh`** — chase the single strongest name from the train window (the
  winner-chasing baseline). Grid: one entry per universe name.

Cadence: rebalance every 21 trading days. Signals use history ≤ i−1 and fill at
day i's close (the house 1-day lag). All fills through `BASE_VENUE`
(10 bps taker + 2 bps spread), integer micro-dollars, money-conserving
sells-before-buys.

## 3. Protocol

Walk-forward over the masked calendar: split into `--windows` (default 10)
windows; the train window selects K/L by audited final cash (grid-order
tiebreak); the frozen selection runs on the next window; a window is a **win iff
final cash > SPY buy-and-hold over the SAME window** at base costs. Verdict
BEATS-SPX iff wins × 2 > tests. Frontier = highest mean OOS delta vs SPY.

**The decisive comparison is not vs SPY.** SPY-relative deltas are inflated by
survivorship (you cannot buy today's survivors in the past). The honest signal
is the frontier family **vs `ew_all`** — both share the identical biased
universe, so survivor inflation cancels and what remains is momentum skill.

## 4. Holdout discipline (forward-only)

v13 carves **no historical one-shot**: the absolute in-sample number is inflated
by construction on a survivor universe, and the calendar overlaps spans that
shaped prior priors. If the frontier family BEATS-SPX *and* beats `ew_all` out
of sample, a clean forward holdout is armed **by hand** naming it —
`data/holdout/alloc13.FORWARD` (family, params, cutoff, min_new_rows, rule, and
a survivorship disclosure) — fired only on virgin post-cutoff rows via
`--holdout FAMILY --forward`. `--holdout` without `--forward` refuses (rc 2)
before touching disk. Resolved this run: `xs_topk [K=5, L=63]`, cutoff
2026-07-17, min 126 new rows.

## 5. Tests (offline; synthetic + tmp CSVs — the real tapes are gitignored)

Masking (unlisted names invisible to the ranker; loader marks `None` before
listing); the 1-day lag boundary (a spike at i−1 selects a name, the same spike
at i is ignored); `xs_topk` top-K equal weight, short-fall→cash, all-negative→
cash; `xs_invvol` fully invested + calmer leg heavier + cash when none positive;
`ew_all` equal weight over listed-only; `realized_daily_vol` → None on an
unlisted gap; rebalance conserves money and never buys a `None` leg; no-leverage
weight invariant; `read_forward` names `xs_topk`; the no-forward refusal.

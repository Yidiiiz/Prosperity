"""spec v3 section 2: buy-and-hold benchmark, CAGR, verdict tiers, footer."""

import pytest

from colony import benchmark, risk
from colony.records import Record

VENUE = {"taker_bps": 20, "maker_bps": 0, "spread_bps": 2, "min_fee_u": 0,
         "fill_delay_ticks": 1}
FREE = {"taker_bps": 0, "maker_bps": 0, "spread_bps": 0, "min_fee_u": 0,
        "fill_delay_ticks": 1}
YEAR = int(benchmark.SECONDS_PER_YEAR)


def test_buy_and_hold_no_costs_is_exact():
    # $10 capital, $2 first bar, $4 last bar: 5 lots -> $20
    assert benchmark.buy_and_hold([2.0, 3.0, 4.0], 10_000_000, FREE) == 20_000_000


def test_leftover_cash_rides_uninvested():
    # $5 capital, $2 lots: 2 lots + $1 cash; last bar $1 -> $2 + $1
    assert benchmark.buy_and_hold([2.0, 1.0], 5_000_000, FREE) == 3_000_000


def test_costs_charged_both_sides_via_risk_helpers():
    capital = 10_000_000
    first = benchmark.to_price_u(2.0, 1)
    last = benchmark.to_price_u(4.0, 1)
    fill = risk.buy_price_u(first, VENUE)
    lots = capital // fill
    while lots * fill + risk.fee_u(lots * fill, VENUE) > capital:
        lots -= 1
    cash = capital - lots * fill - risk.fee_u(lots * fill, VENUE)
    proceeds = lots * risk.sell_price_u(last, VENUE)
    cash += proceeds - risk.fee_u(proceeds, VENUE)
    assert benchmark.buy_and_hold([2.0, 4.0], capital, VENUE) == cash
    # and the tolls actually bite vs the free venue
    assert cash < benchmark.buy_and_hold([2.0, 4.0], capital, FREE)


def test_cannot_afford_one_lot_returns_capital():
    assert benchmark.buy_and_hold([2.0, 9.0], 1_000_000, VENUE) == 1_000_000


def test_lot_denominator_scales_affordability():
    # $1 capital cannot buy a $2 lot, but can buy 1/1000 slices
    assert benchmark.buy_and_hold([2.0, 4.0], 1_000_000, FREE, 1000) == 2_000_000


def test_cash_benchmark_is_identity():
    assert benchmark.cash(123) == 123


def test_tiers():
    assert benchmark.tier(100, 300, 200) == "ALPHA"
    assert benchmark.tier(100, 150, 200) == "CASH"
    assert benchmark.tier(100, 100, 200) == "EXPECTED-FAIL"
    assert benchmark.tier(100, 90, 200) == "EXPECTED-FAIL"


def test_cagr_doubling_in_one_year():
    assert benchmark.cagr(100, 200, 1.0) == pytest.approx(1.0)
    assert benchmark.cagr(100, 200, 2.0) == pytest.approx(2 ** 0.5 - 1)
    assert benchmark.cagr(100, 0, 1.0) == -1.0


def test_footer_states_all_four_terms():
    text = benchmark.footer(0, 2 * YEAR, [("full seed 42", 100_000_000,
                                           110_000_000, 120_000_000)], 3.25)
    assert "span:" in text and "(2.00 years)" in text
    assert "wall: 3.2 s" in text
    assert "annualized: full seed 42" in text
    assert "benchmark: full seed 42 buy-and-hold" in text
    assert "(projected)" not in text


def test_footer_sub_year_span_is_projected_never_suppressed():
    text = benchmark.footer(0, YEAR // 4, [("x", 100, 110, 120)], 1.0)
    assert "(0.25 years)" in text
    assert text.count("(projected)") == 2  # colony CAGR and benchmark CAGR


def test_record_finish_appends_replay_footer(tmp_path):
    rec = Record(tmp_path, "experiments", "demo", seed=1)
    rec.set_replay_terms(0, YEAR, [("x", 100_000_000, 90_000_000, 200_000_000)])
    rec.finish("EXPECTED-FAIL")
    text = rec.path.read_text(encoding="utf-8")
    for term in ("span:", "wall:", "annualized:", "benchmark:"):
        assert term in text
    assert text.index("benchmark:") < text.index("=== RESULT")


def test_record_without_terms_has_no_footer(tmp_path):
    rec = Record(tmp_path, "experiments", "demo2", seed=1)
    rec.finish("PASS")
    text = rec.path.read_text(encoding="utf-8")
    assert "span:" not in text and "annualized:" not in text

"""spec v4 section 2: the S&P yardstick."""

import pytest

from colony.arenas.replay import parse_utc
from experiments.yardstick import spx_line, spx_over

VENUE = {"taker_bps": 10, "maker_bps": 0, "spread_bps": 2, "min_fee_u": 0,
         "fill_delay_ticks": 1}
CAP = 1_000_000_000  # $1,000


def utc(iso):
    return parse_utc(iso)


def test_known_window_beats_cash_in_a_bull_year():
    # 2023 was a strong SPY year; B&H must end above initial even after costs
    cash_u, cagr, coverage = spx_over(utc("2023-01-01"), utc("2024-01-01"),
                                      CAP, VENUE)
    assert cash_u > CAP
    assert cagr > 0.10
    assert coverage > 0.9


def test_coverage_reports_partial_and_missing_windows():
    # window mostly before SPY's 1993 start -> partial or empty
    _, _, cov = spx_over(utc("1980-01-01"), utc("1994-01-01"), CAP, VENUE)
    assert cov < 0.9
    cash_u, cagr, cov = spx_over(utc("1970-01-01"), utc("1971-01-01"),
                                 CAP, VENUE)
    assert (cash_u, cagr, cov) == (CAP, 0.0, 0.0)  # no data: the cash bench


def test_degenerate_window_refused():
    with pytest.raises(ValueError):
        spx_over(utc("2020-01-02"), utc("2020-01-02"), CAP, VENUE)


def test_spx_line_labels_projection_and_partial_coverage():
    line = spx_line("cell", utc("2023-03-01"), utc("2023-06-01"),
                    CAP, CAP + 1_000_000, VENUE)
    assert line.startswith("spx: cell buy-and-hold")
    assert "(projected)" in line and "delta" in line
    line = spx_line("cell", utc("1990-01-01"), utc("1994-01-01"),
                    CAP, CAP, VENUE)
    assert "(partial SPY coverage)" in line
    line = spx_line("cell", utc("1970-01-01"), utc("1971-01-01"),
                    CAP, CAP, VENUE)
    assert "no SPY coverage" in line

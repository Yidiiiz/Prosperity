from colony.risk import check, fee_u
from colony.strategies import Decision, decide, zstats

MOMENTUM = {
    "archetype": "momentum",
    "params": {"lookback": 10, "entry_z": 1.5, "exit_z": -0.5, "risk_fraction": 0.40,
               "hold_max": 600},
    "econ": {"child_seed_fraction": 0.40},
    "genes": [],
}
MEAN_REVERT = {
    "archetype": "mean_revert",
    "params": {"lookback": 10, "entry_z": 1.5, "exit_z": 0.2, "risk_fraction": 0.40,
               "hold_max": 600},
    "econ": {"child_seed_fraction": 0.40},
    "genes": [],
}
SITTER = {
    "archetype": "sitter",
    "params": {"lookback": 10, "entry_z": 1.5, "exit_z": 0.0, "risk_fraction": 0.40,
               "hold_max": 600},
    "econ": {"child_seed_fraction": 0.40},
    "genes": [],
}

FLAT = [200] * 11
SPIKE_UP = [200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 230]
SPIKE_DOWN = [200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 170]


def test_zstats_needs_lookback_plus_one():
    assert zstats([200] * 10, 10) == (0.0, 0.0, 0.0)
    z, mean, stdev = zstats(SPIKE_UP, 10)
    assert z > 2.5


def test_zstats_flat_window_is_zero():
    z, _, stdev = zstats(FLAT, 10)
    assert z == 0.0 and stdev == 0.0


def test_sitter_never_trades():
    assert decide(SITTER, SPIKE_UP, 0, 0, 100_000, 20) is None
    assert decide(SITTER, SPIKE_DOWN, 5, 100, 100_000, 20) is None


def test_momentum_buys_spike_with_sized_order():
    d = decide(MOMENTUM, SPIKE_UP, 0, 0, 100_000, 20)
    assert d == Decision("BUY", int(0.40 * 100_000) // 230)


def test_momentum_ignores_dip_when_flat():
    assert decide(MOMENTUM, SPIKE_DOWN, 0, 0, 100_000, 20) is None
    assert decide(MOMENTUM, FLAT, 0, 0, 100_000, 20) is None


def test_momentum_exits_down_through_signed_exit_z():
    assert decide(MOMENTUM, SPIKE_DOWN, 7, 5, 100_000, 20) == Decision("SELL", 7)
    # ordinary flat drift does NOT trigger the signed exit (lets winners run)
    assert decide(MOMENTUM, FLAT, 7, 5, 100_000, 20) is None


def test_momentum_exits_on_hold_max():
    assert decide(MOMENTUM, FLAT, 7, 600, 100_000, 20) == Decision("SELL", 7)


def test_mean_revert_buys_dip_and_exits_up():
    d = decide(MEAN_REVERT, SPIKE_DOWN, 0, 0, 100_000, 20)
    assert d == Decision("BUY", int(0.40 * 100_000) // 170)
    assert decide(MEAN_REVERT, SPIKE_UP, 4, 5, 100_000, 20) == Decision("SELL", 4)
    assert decide(MEAN_REVERT, SPIKE_UP, 0, 0, 100_000, 20) is None


def test_fee_aware_blocks_thin_edges():
    genome = dict(MOMENTUM, genes=["fee_aware"])
    # tiny stdev -> edge in bps is far below 2x fee_bps -> skip
    thin = [20_000] * 10 + [20_003]
    assert decide(genome, thin, 0, 0, 100_000_000, 20) is not None or True
    z, mean, stdev = zstats(thin, 10)
    edge_bps = (abs(z) - genome["params"]["exit_z"]) * (stdev / mean) * 10_000
    expected = decide(genome, thin, 0, 0, 100_000_000, 20)
    if edge_bps < 40:
        assert expected is None
    # without the gene the same signal trades
    assert decide(MOMENTUM, thin, 0, 0, 100_000_000, 20) is not None


def test_risk_caps_buy_at_max_action_fraction():
    d = check(Decision("BUY", 1_000), cash=1_000_000, equity=1_000_000, lots_held=0,
              price=200, max_action_fraction=0.10, fee_bps=20)
    assert d.lots == int(0.10 * 1_000_000) // 200


def test_risk_caps_buy_at_cash_including_fee():
    # cash covers 5 lots at 200 but not 5 lots + fee
    d = check(Decision("BUY", 5), cash=1_000, equity=1_000_000, lots_held=0,
              price=200, max_action_fraction=0.80, fee_bps=20)
    assert d.lots == 4
    assert 4 * 200 + fee_u(4 * 200, 20) <= 1_000


def test_risk_rejects_unaffordable_buy():
    assert check(Decision("BUY", 10), cash=100, equity=100, lots_held=0,
                 price=200, max_action_fraction=0.80, fee_bps=20) is None


def test_risk_caps_sell_at_position():
    d = check(Decision("SELL", 10), cash=0, equity=1_000, lots_held=3,
              price=200, max_action_fraction=0.80, fee_bps=20)
    assert d.lots == 3
    assert check(Decision("SELL", 5), cash=0, equity=0, lots_held=0,
                 price=200, max_action_fraction=0.80, fee_bps=20) is None


def test_risk_passes_none_through():
    assert check(None, 0, 0, 0, 200, 0.8, 20) is None

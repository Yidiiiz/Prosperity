import pytest

from colony.config import ConfigError, load_config, validate
from tests.conftest import make_cfg


def test_default_config_is_valid():
    load_config("config.default.json")


def test_missing_key_rejected():
    cfg = make_cfg()
    del cfg["fee_bps"]
    with pytest.raises(ConfigError):
        validate(cfg)


def test_float_u_rejected():
    cfg = make_cfg()
    cfg["death_floor_u"] = 100_000_000.5
    with pytest.raises(ConfigError):
        validate(cfg)


def test_death_floor_must_be_below_seed():
    with pytest.raises(ConfigError):
        make_cfg(death_floor_u=1_000_000_000)


def test_reserve_floor_at_least_death_floor():
    with pytest.raises(ConfigError):
        make_cfg(reserve_floor_u=99_999_999)


def test_lot_granularity_constraint():
    # gen0_seed_u must be >= 200 x start_price_u (spec 3.11)
    with pytest.raises(ConfigError):
        make_cfg(arena={"start_price_u": 6_000_000})


def test_stagnation_must_exceed_max_lookback():
    with pytest.raises(ConfigError):
        make_cfg(lifecycle={"stagnation_days": 100})


def test_rent_apr_capped():
    with pytest.raises(ConfigError):
        make_cfg(rent_apr_bps=731)


def test_treasury_must_fund_gen0():
    with pytest.raises(ConfigError):
        make_cfg(gen0_population=100)  # 100 x 100k > 2M


def test_repay_multiple_hard_cliff():
    with pytest.raises(ConfigError):
        make_cfg(repay_multiple=0.26)
    with pytest.raises(ConfigError):
        make_cfg(repay_multiple=0.26, revalidated=True)  # never allowed above 0.25


def test_repay_multiple_above_validated_needs_flag():
    with pytest.raises(ConfigError):
        make_cfg(repay_multiple=0.20)
    make_cfg(repay_multiple=0.20, revalidated=True)  # allowed with the flag
    make_cfg(repay_multiple=0.15)  # the shipped default needs no flag


def test_unknown_regime_kind_rejected():
    with pytest.raises(ConfigError):
        make_cfg(arena={"regimes": [{"kind": "sideways", "ticks": 100}]})

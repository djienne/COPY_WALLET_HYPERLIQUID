"""
Sizing math in copy_core.py, checked against freqtrade's own conversions
(freqtrade 2025.7 freqtradebot.py):
  increase: amount = stake / rate * leverage                  (execute_entry)
  decrease: amount = |stake| * trade.amount / trade.stake_amount  (check_and_call_adjust_trade_position)
"""
import pytest

from copy_core import is_significant, scale_to_me, stake_for_delta


def ft_increase_coins(stake, rate, leverage):
    return stake / rate * leverage


def ft_decrease_coins(stake, trade_amount, trade_stake):
    return abs(stake) * trade_amount / trade_stake


# A long opened at 100 with 3x: 10 coins, stake 10 * 100 / 3.
AMOUNT, OPEN_RATE, LEV = 10.0, 100.0, 3.0
STAKE = AMOUNT * OPEN_RATE / LEV


@pytest.mark.parametrize("rate", [50.0, 100.0, 180.0])  # price far below / at / above entry
def test_decrease_sells_exactly_delta_coins(rate):
    stake = stake_for_delta(-4.0, rate, LEV, AMOUNT, STAKE)
    assert stake < 0
    assert ft_decrease_coins(stake, AMOUNT, STAKE) == pytest.approx(4.0)


@pytest.mark.parametrize("rate", [50.0, 180.0])
def test_increase_buys_exactly_delta_coins(rate):
    stake = stake_for_delta(2.5, rate, LEV, AMOUNT, STAKE)
    assert ft_increase_coins(stake, rate, LEV) == pytest.approx(2.5)


def test_decrease_is_capped_at_whole_position():
    stake = stake_for_delta(-25.0, 180.0, LEV, AMOUNT, STAKE)
    assert ft_decrease_coins(stake, AMOUNT, STAKE) == pytest.approx(AMOUNT)


@pytest.mark.parametrize("delta", [1e-6, -1e-6, 0.01, -0.01])
def test_sign_never_flips_for_tiny_deltas(delta):
    # The old code subtracted a 0.51 USDC "dust", which flipped small adjustments.
    stake = stake_for_delta(delta, 100.0, LEV, AMOUNT, STAKE)
    assert stake * delta > 0


def test_degenerate_inputs_do_nothing():
    assert stake_for_delta(-1.0, 100.0, LEV, 0.0, STAKE) == 0.0
    assert stake_for_delta(1.0, 0.0, LEV, AMOUNT, STAKE) == 0.0


def test_scale_is_linear_and_guards_empty_account():
    assert scale_to_me(300_000, 1_000_000, 10_000) == pytest.approx(3_000)
    assert scale_to_me(3.0, 1_000_000, 10_000) == pytest.approx(0.03)
    assert scale_to_me(100, 0.0, 1_000) is None


def test_significance():
    assert is_significant(6_000, 1_000_000, 0.5) is True
    assert is_significant(4_000, 1_000_000, 0.5) is False
    assert is_significant(5_000, 0.0, 0.5) is False

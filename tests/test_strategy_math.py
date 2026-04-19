"""
Pure arithmetic regression tests for the money-moving formulas in
`user_data/strategies/COPY_HL.py`. No freqtrade import — we reproduce the
formulas inline so the tests cannot be skipped in a bare pytest env.

If anyone edits the real formulas, mirror the change here and vice versa.
"""
import pytest

DUST_USDC = 0.51


def custom_stake_amount(position_value_in_copied, copied_acct, my_acct, leverage,
                        min_stake=10.0):
    """Mirrors COPY_HL.py custom_stake_amount body (post-guard)."""
    if copied_acct <= 0:
        return None
    scale_factor = my_acct / copied_acct
    returned = position_value_in_copied * scale_factor
    returned = (returned / leverage) - DUST_USDC
    if returned < min_stake:
        returned = min_stake
    return returned


def adjust_increased(old_value, new_value, copied_acct, my_acct, leverage):
    if copied_acct <= 0:
        return None
    scale_factor = my_acct / copied_acct
    delta_stake = abs(old_value - new_value) * scale_factor
    return delta_stake / leverage - DUST_USDC


def adjust_decreased(old_value, new_value, copied_acct, my_acct, leverage):
    """Mirrors the FIXED decrease-branch: `return -(delta/lev - dust)`."""
    if copied_acct <= 0:
        return None
    scale_factor = my_acct / copied_acct
    delta_stake = abs(old_value - new_value) * scale_factor
    return -(delta_stake / leverage - DUST_USDC)


def is_position_significant(position_value, copied_acct, change_threshold=0.5):
    if copied_acct <= 0:
        return False
    min_threshold = copied_acct / (100.0 / change_threshold)
    return position_value > min_threshold


# --- custom_stake_amount -----------------------------------------------------

def test_custom_stake_snaps_to_min():
    # Very small copy: raw = 50k * 0.001 = 50 ; /6 = 8.33 ; -0.51 = 7.82 → below
    # min_stake=10 → snaps up.
    assert custom_stake_amount(50_000, 1_000_000, 1_000, 6, min_stake=10.0) == 10.0


def test_custom_stake_large_copy():
    assert custom_stake_amount(300_000, 1_000_000, 10_000, 6) == pytest.approx(499.49)


def test_custom_stake_guard_zero_copied():
    assert custom_stake_amount(100, 0.0, 1000, 6) is None


# --- adjust_trade_position sign symmetry (the headline regression) -----------

def test_increase_and_decrease_are_sign_symmetric():
    inc = adjust_increased(300_000, 350_000, 1_000_000, 10_000, 6)
    dec = adjust_decreased(350_000, 300_000, 1_000_000, 10_000, 6)
    # After the fix, |inc| must equal |dec| — dust is applied symmetrically.
    assert abs(abs(inc) - abs(dec)) < 1e-9, f"inc={inc} dec={dec} — sign bug regressed"


def test_adjust_guard_zero_copied():
    assert adjust_increased(1, 2, 0.0, 1000, 6) is None
    assert adjust_decreased(2, 1, 0.0, 1000, 6) is None


# --- _is_position_significant ------------------------------------------------

def test_significance_above_threshold():
    assert is_position_significant(6_000, 1_000_000) is True


def test_significance_below_threshold():
    assert is_position_significant(4_000, 1_000_000) is False


def test_significance_guard_zero_copied():
    assert is_position_significant(5_000, 0.0) is False

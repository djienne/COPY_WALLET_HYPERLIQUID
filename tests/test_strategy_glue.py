"""
Tests of the real strategy (user_data/strategies/COPY_HL.py) with the freqtrade runtime
replaced by minimal stubs: IStrategy, IntParameter and Trade.get_trades_proxy are all
the strategy uses from freqtrade. The Hyperliquid fetch is monkeypatched with payloads.
"""
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest


class _IStrategy:
    def __init__(self, config):
        self.config = config


class _IntParameter:
    def __init__(self, low, high, default, **kwargs):
        self.value = default


class _Trade:
    open_trades: list = []
    closed_trades: list = []

    @classmethod
    def get_trades_proxy(cls, *, pair=None, is_open=None, **kwargs):
        pool = {True: cls.open_trades, False: cls.closed_trades}.get(
            is_open, cls.open_trades + cls.closed_trades)
        return [t for t in pool if pair is None or t.pair == pair]


sys.modules["freqtrade"] = types.ModuleType("freqtrade")
sys.modules["freqtrade.strategy"] = types.SimpleNamespace(IStrategy=_IStrategy, IntParameter=_IntParameter)
sys.modules["freqtrade.persistence"] = types.SimpleNamespace(Trade=_Trade)
import COPY_HL as strat  # noqa: E402

ADDR = "0x" + "ab" * 20
T0 = 1_700_000_000_000  # payload server time [ms]


def payload(t_ms, equity, **positions):
    """positions: coin=(size, mark_price)"""
    return {
        "time": t_ms,
        "marginSummary": {"accountValue": str(equity)},
        "equity": equity,
        "assetPositions": [
            {"type": "oneWay", "position": {
                "coin": coin, "szi": str(size), "entryPx": str(px),
                "positionValue": str(abs(size) * px), "unrealizedPnl": "0",
                "leverage": {"type": "cross", "value": 5}, "marginUsed": "0"}}
            for coin, (size, px) in positions.items()
        ],
    }


def trade(coin, amount, open_rate, leverage=3.0, open_orders=False):
    return SimpleNamespace(pair=f"{coin}/USDC:USDC", amount=amount, open_rate=open_rate,
                           stake_amount=amount * open_rate / leverage, leverage=leverage,
                           has_open_orders=open_orders)


@pytest.fixture
def bot(tmp_path, monkeypatch):
    """Dry-run strategy, my wallet total 1000 USDC, ticker price = open price (no uPnL)."""
    _Trade.open_trades, _Trade.closed_trades = [], []
    monkeypatch.setattr(strat, "DATA_ROOT", tmp_path)
    s = strat.COPY_HL({"runmode": SimpleNamespace(value="dry_run"), "copy_address": ADDR,
                       "stake_currency": "USDC"})
    s.bot_start()
    s.wallets = SimpleNamespace(update=lambda: None, get_total=lambda currency: 1000.0)
    prices = {}
    s.dp = SimpleNamespace(ticker=lambda pair: {"last": prices[pair.split("/")[0]]},
                           current_whitelist=lambda: [f"{c}/USDC:USDC" for c in ("BTC", "ETH", "SOL")])
    s.prices = prices
    return s


def loop(bot, monkeypatch, state):
    monkeypatch.setattr(strat, "fetch_user_state", lambda address: state)
    bot.bot_loop_start(current_time=datetime.now(timezone.utc))


def signal(bot, coin):
    df = bot.populate_indicators(pd.DataFrame({"close": [1.0, 2.0]}), {"pair": f"{coin}/USDC:USDC"})
    return int(df["signal"].iloc[-1])


def adjust(bot, t, rate, min_stake=None):
    return bot.adjust_trade_position(t, datetime.now(timezone.utc), rate, 0.0, min_stake, 1e9,
                                     rate, rate, 0.0, 0.0)


def test_substring_coin_does_not_exit(bot, monkeypatch):
    """Trader closes ETHFI: my ETH must not get an exit (old code matched 'ETH' in 'ETHFI')."""
    _Trade.open_trades = [trade("ETH", 1.0, 3000.0)]
    bot.prices["ETH"] = 3000.0
    loop(bot, monkeypatch, payload(T0, 1_000_000, ETH=(100, 3000.0), ETHFI=(50_000, 1.0)))
    loop(bot, monkeypatch, payload(T0 + 60_000, 1_000_000, ETH=(100, 3000.0)))
    assert bot._changes["ETHFI"].change_type == "closed"
    assert signal(bot, "ETH") == 2


def test_entry_and_exit_follow_state_not_events(bot, monkeypatch):
    """A missed event does not matter: every loop compares holdings coin by coin."""
    _Trade.open_trades = [trade("BTC", 0.01, 60000.0)]
    bot.prices["BTC"] = 60000.0
    loop(bot, monkeypatch, payload(T0, 1_000_000, SOL=(1000, 100.0)))  # 10 % of account
    assert signal(bot, "SOL") == 1   # trader long SOL, I don't hold it
    assert signal(bot, "BTC") == 0   # I hold BTC, trader doesn't
    assert signal(bot, "ETH") == 2


def test_short_or_flip_exits_long(bot, monkeypatch):
    _Trade.open_trades = [trade("SOL", 1.0, 100.0)]
    bot.prices["SOL"] = 100.0
    loop(bot, monkeypatch, payload(T0, 1_000_000, SOL=(-1000, 100.0)))
    assert signal(bot, "SOL") == 0


def test_significance_hysteresis(bot, monkeypatch):
    # 0.4 % of the copied account: below the 0.5 % entry threshold, above the 0.25 % exit one.
    loop(bot, monkeypatch, payload(T0, 1_000_000, SOL=(40, 100.0)))
    assert signal(bot, "SOL") == 2
    _Trade.open_trades = [trade("SOL", 0.4, 100.0)]
    bot.prices["SOL"] = 100.0
    loop(bot, monkeypatch, payload(T0 + 60_000, 1_000_000, SOL=(40, 100.0)))
    assert signal(bot, "SOL") == 2
    loop(bot, monkeypatch, payload(T0 + 120_000, 1_000_000, SOL=(20, 100.0)))  # 0.2 %
    assert signal(bot, "SOL") == 0


def test_fetch_failure_does_nothing(bot, monkeypatch):
    _Trade.open_trades = [trade("BTC", 0.01, 60000.0)]

    def boom(address):
        raise TimeoutError("stalled")
    monkeypatch.setattr(strat, "fetch_user_state", boom)
    bot.bot_loop_start(current_time=datetime.now(timezone.utc))
    assert signal(bot, "BTC") == 2
    assert adjust(bot, _Trade.open_trades[0], 60000.0) is None
    assert bot.custom_stake_amount("BTC/USDC:USDC", None, 60000.0, 16, 5, 1e9, 3, None, "long") is None


def test_empty_copied_wallet_is_inert(bot, monkeypatch):
    """Wrong or emptied address: no exits of my positions, no entries."""
    _Trade.open_trades = [trade("BTC", 0.01, 60000.0)]
    loop(bot, monkeypatch, payload(T0, 0.0))
    assert not bot._ok
    assert signal(bot, "BTC") == 2


def test_signal_error_does_nothing(bot, monkeypatch):
    loop(bot, monkeypatch, payload(T0, 1_000_000, SOL=(1000, 100.0)))
    bot._positions["SOL"].position_value = None  # corrupt data -> exception inside _signal
    assert signal(bot, "SOL") == 2


def test_reentry_waits_for_traders_next_move_after_stop(bot, monkeypatch):
    stopped_at = datetime.fromtimestamp((T0 + 30_000) / 1000, tz=timezone.utc)
    loop(bot, monkeypatch, payload(T0, 1_000_000, SOL=(1000, 100.0)))
    _Trade.closed_trades = [SimpleNamespace(pair="SOL/USDC:USDC", exit_reason="stop_loss",
                                            close_date_utc=stopped_at)]
    loop(bot, monkeypatch, payload(T0 + 60_000, 1_000_000, SOL=(1000, 90.0)))  # price move only
    assert signal(bot, "SOL") == 2
    loop(bot, monkeypatch, payload(T0 + 120_000, 1_000_000, SOL=(1200, 90.0)))  # trader adds
    assert signal(bot, "SOL") == 1


def test_reentry_after_normal_exit_is_immediate(bot, monkeypatch):
    _Trade.closed_trades = [SimpleNamespace(pair="SOL/USDC:USDC", exit_reason="exit_signal",
                                            close_date_utc=datetime.now(timezone.utc))]
    loop(bot, monkeypatch, payload(T0, 1_000_000, SOL=(1000, 100.0)))
    assert signal(bot, "SOL") == 1


def test_entry_stake_buys_target_coins(bot, monkeypatch):
    loop(bot, monkeypatch, payload(T0, 1_000_000, SOL=(1000, 100.0)))  # target 1 coin
    stake = bot.custom_stake_amount("SOL/USDC:USDC", None, 105.0, 16, 5, 1e9, 3, None, "long")
    assert stake / 105.0 * 3 == pytest.approx(1.0)  # freqtrade: amount = stake / rate * leverage


def test_trader_decrease_is_copied_in_coins(bot, monkeypatch):
    """Price 50 % above my entry: the old code trimmed 1.5x too much here."""
    t = trade("SOL", 10.0, 100.0)
    _Trade.open_trades = [t]
    bot.prices["SOL"] = 100.0  # ticker = open rate -> my equity stays 1000
    loop(bot, monkeypatch, payload(T0, 1_000_000, SOL=(10_000, 150.0)))  # target 10 coins
    loop(bot, monkeypatch, payload(T0 + 60_000, 1_000_000, SOL=(5_000, 150.0)))  # target 5
    assert "SOL" in bot._pending
    stake = adjust(bot, t, 150.0)
    assert abs(stake) * t.amount / t.stake_amount == pytest.approx(5.0)  # freqtrade's conversion


def test_cooldown_is_per_pair_and_keeps_pending(bot, monkeypatch):
    sol, btc = trade("SOL", 10.0, 100.0), trade("BTC", 0.1, 50_000.0)
    _Trade.open_trades = [sol, btc]
    bot.prices.update(SOL=100.0, BTC=50_000.0)
    loop(bot, monkeypatch, payload(T0, 1_000_000, SOL=(10_000, 100.0), BTC=(100, 50_000.0)))
    loop(bot, monkeypatch, payload(T0 + 60_000, 1_000_000, SOL=(12_000, 100.0), BTC=(120, 50_000.0)))
    assert adjust(bot, sol, 100.0) > 0
    assert adjust(bot, btc, 50_000.0) > 0      # the old global cooldown blocked this one
    assert adjust(bot, sol, 100.0) is None     # SOL itself is in cooldown
    assert "SOL" in bot._pending               # applied after the cooldown if still needed


def test_small_drift_is_ignored_and_open_orders_block(bot, monkeypatch):
    t = trade("SOL", 9.5, 100.0)  # 5 % below target: inside the 10 % drift tolerance
    _Trade.open_trades = [t]
    bot.prices["SOL"] = 100.0
    loop(bot, monkeypatch, payload(T0, 1_000_000, SOL=(10_000, 100.0)))
    assert adjust(bot, t, 100.0) is None
    t2 = trade("SOL", 5.0, 100.0, open_orders=True)
    assert adjust(bot, t2, 100.0) is None


def test_fidelity_row_is_written(bot, monkeypatch):
    t = trade("SOL", 9.0, 100.0)
    _Trade.open_trades = [t]
    bot.prices["SOL"] = 100.0
    loop(bot, monkeypatch, payload(T0, 1_000_000, SOL=(10_000, 100.0), BTC=(100, 50_000.0)))
    rows = (bot.data_dir / "fidelity.csv").read_text().splitlines()
    assert rows[0].startswith("time_utc")
    # target SOL 1000, mine 900; target BTC 5000, mine 0 -> (100 + 5000) / 1000
    assert rows[1].split(",")[3:] == ["5.1", "1", "0"]


def test_portfolio_equity_and_one_fetch_per_loop(bot, monkeypatch):
    state = payload(T0, 1_000_000, SOL=(1000, 100.0))
    state['marginSummary']['accountValue'] = '0'  # unified wallet's main perp balance
    calls = []

    def fetch(address):
        calls.append(address)
        return state

    monkeypatch.setattr(strat, 'fetch_user_state', fetch)
    bot.bot_loop_start(datetime.now(timezone.utc))
    assert bot._ok and bot._copied_equity == 1_000_000
    assert signal(bot, 'SOL') == 1
    stake = bot.custom_stake_amount('SOL/USDC:USDC', None, 100.0, 16, 5, 1e9, 3, None, 'long')
    assert stake * 3 / 100.0 == pytest.approx(1.0)
    assert calls == [ADDR]

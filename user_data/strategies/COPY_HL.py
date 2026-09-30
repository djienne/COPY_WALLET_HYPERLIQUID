import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from freqtrade.persistence import Trade
from freqtrade.strategy import IntParameter, IStrategy

# copy_core.py sits next to this file; freqtrade puts this directory on sys.path while loading.
from copy_core import (PositionTracker, append_csv, fetch_user_state, is_significant,
                       scale_to_me, stake_for_delta)

logger = logging.getLogger(__name__)

DATA_ROOT = Path(__file__).resolve().parent / 'position_data'
# Exits not decided by copying: after one, wait for the trader's next move on the coin.
# sold_on_exchange = position closed outside freqtrade (e.g. exchange liquidation, manual close).
FORCED_EXITS = {'stop_loss', 'stoploss_on_exchange', 'trailing_stop_loss', 'liquidation',
                'sold_on_exchange'}
FIDELITY_FIELDS = ['time_utc', 'my_equity', 'copied_equity', 'abs_deviation_over_equity',
                   'n_missing', 'n_extra']


def coin_of(pair: str) -> str:
    # Hyperliquid coin name == pair base for the whitelisted coins. k-prefixed coins
    # (kPEPE, kSHIB, ...) would need an explicit map before adding them to the whitelist.
    return pair.split('/')[0]


class COPY_HL(IStrategy):
    """Copies the long positions of a Hyperliquid wallet, scaled by account value.

    Target for every copied long: my_value = copied_value * my_equity / copied_equity
    (see copy_core.py). Positions are compared with the trader's state every loop;
    anything that fails (API, bad data) results in no action.
    """
    minimal_roi = {
        "0": 5000.0  # Effectively disables ROI
    }
    stoploss = -0.95
    timeframe = '1h'
    startup_candle_count: int = 0
    can_short: bool = False
    process_only_new_candles: bool = False
    position_adjustment_enable = True

    # Leverage per position. It sets the margin each position uses, NOT the exposure
    # (the exposure is copied). Margin is isolated, so lower = liquidation further away:
    # about -26..-32 % price move at 3x vs -11..-16 % at 6x, depending on the coin.
    # It must still be >= the trader's exposure / equity, otherwise the margin does not fit.
    LEV = IntParameter(1, 6, default=3, space='buy', optimize=False)
    change_threshold = 0.5       # % of account value; smaller positions and changes are ignored
    adjustment_threshold = 10.0  # % of target; drift tolerated when the trader did not act

    # Optional order type mapping.
    order_types = {
        'entry': 'market',
        'exit': 'market',
        'stoploss': 'market',
        'stoploss_on_exchange': False
    }

    # Optional order time in force.
    order_time_in_force = {
        'entry': 'gtc',
        'exit': 'gtc'
    }

    def bot_start(self, **kwargs) -> None:
        self.address = str(self.config.get('copy_address', '')).strip().lower()
        # Minimum time between two resizes of the same pair. 120 s in live, where balances were
        # seen lagging > 1 min behind fills; it also rate-limits retries of orders freqtrade refuses.
        self.cooldown_s = 120 if self.config['runmode'].value == 'live' else 5

        # Per-loop snapshot, filled by bot_loop_start and read by every callback.
        self._ok = False             # True only if this loop's data is complete and sane
        self._positions = {}         # coin -> PositionSnapshot of the copied account
        self._changes = {}           # coin -> PositionChange detected this loop
        self._held = set()           # coins I hold
        self._copied_equity = 0.0
        self._my_equity = 0.0
        # Across loops.
        self._pending = set()        # held coins the trader resized; copy at the smaller tolerance
        self._last_adjust = {}       # pair -> time.monotonic() of the last adjustment

        self.tracker = None
        if not re.fullmatch(r'0x[0-9a-f]{40}', self.address):
            logger.error(f"copy_address '{self.address}' is not a 0x-prefixed 40-hex-digit address "
                         "(set it in config.json or env FREQTRADE__COPY_ADDRESS). The bot will not trade.")
            return
        self.data_dir = DATA_ROOT / self.address
        self.tracker = PositionTracker(data_dir=self.data_dir)

    # --- Per-loop snapshot ----------------------------------------------------------------

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        self._ok = False
        self._changes = {}
        if self.tracker is None:
            return
        try:
            state = fetch_user_state(self.address)
        except Exception as e:
            logger.error(f"Hyperliquid fetch failed, no action this loop: {e}")
            return

        changes = self.tracker.track_positions(state)
        self.tracker.print_changes(changes)
        self._changes = {c.coin: c for c in changes}
        self._positions = self.tracker.last_positions
        self._copied_equity = state['equity']
        if self._copied_equity <= 0:
            logger.error(f"Copied wallet {self.address} has account value {self._copied_equity}. "
                         "Wrong address or empty wallet: not trading.")
            return

        open_trades = Trade.get_trades_proxy(is_open=True)
        self._held = {coin_of(t.pair) for t in open_trades}
        self._my_equity = self._account_value(open_trades)
        if self._my_equity <= 0:
            logger.error(f"My account value is {self._my_equity}: not trading.")
            return

        self._pending &= self._held
        self._pending |= {c.coin for c in changes
                          if c.change_type in ('increased', 'decreased') and c.coin in self._held}
        self._ok = True
        self._report(open_trades)

    def _rate(self, pair: str):
        try:
            return float(self.dp.ticker(pair)['last'])
        except Exception:
            return None

    def _account_value(self, open_trades) -> float:
        """My account value incl. unrealized PnL, same definition as Hyperliquid accountValue.
        Live: the wallet total is accountValue (ccxt hyperliquid.fetch_balance).
        Dry run: freqtrade's wallet total excludes unrealized PnL, so it is added here."""
        self.wallets.update()
        equity = float(self.wallets.get_total(self.config['stake_currency']))
        if self.config['runmode'].value != 'live':
            for t in open_trades:
                rate = self._rate(t.pair)
                if rate:
                    equity += (rate - t.open_rate) * t.amount
        return equity

    def _wanted(self, coin: str) -> bool:
        """The trader holds a significant long on this coin. Hysteresis: enter above
        change_threshold, exit only below half of it, so a position hovering at the
        threshold does not churn in and out."""
        pos = self._positions.get(coin)
        threshold = self.change_threshold / 2 if coin in self._held else self.change_threshold
        return (pos is not None and pos.size > 0
                and is_significant(pos.position_value, self._copied_equity, threshold))

    def _report(self, open_trades) -> None:
        """Log target vs actual per coin, warn if the margin does not fit, and append one
        copy-fidelity row: sum |target - actual| / my_equity over whitelisted coins."""
        whitelist = {coin_of(p) for p in self.dp.current_whitelist()}
        mine = {coin_of(t.pair): t for t in open_trades}
        scale = self._my_equity / self._copied_equity
        logger.info("=" * 80)
        logger.info(f"Copied account: ${self._copied_equity:,.2f} | Mine: ${self._my_equity:,.2f} "
                    f"| Scale: {scale:.6f}x | trader positions outside whitelist: "
                    f"{len(set(self._positions) - whitelist)}")

        deviation = total_target = 0.0
        n_missing = n_extra = 0
        for coin in sorted(set(self._positions) | set(mine)):
            pos = self._positions.get(coin)
            trade = mine.get(coin)
            # Value both sides at the trader's mark price when there is one (price cancels
            # out of target vs actual); otherwise at my ticker price.
            price = pos.position_value / abs(pos.size) if pos else (self._rate(trade.pair) or 0.0)
            actual = trade.amount * price if trade else 0.0
            wanted = self._wanted(coin)
            target = pos.position_value * scale if wanted else 0.0
            if coin not in whitelist and trade is None:
                continue
            if pos is not None:
                side = 'LONG' if pos.size > 0 else 'SHORT'
                logger.info(f"  {coin:>8} {side:>5} | copied ${pos.position_value:>12,.2f} "
                            f"({pos.position_value / self._copied_equity * 100:5.2f}%) | "
                            f"target ${target:>9,.2f} | mine ${actual:>9,.2f}")
            else:
                logger.info(f"  {coin:>8} not held by trader | mine ${actual:>9,.2f} (should exit)")
            if coin not in whitelist:
                continue
            total_target += target
            deviation += abs(target - actual)
            n_missing += int(wanted and trade is None)
            n_extra += int(trade is not None and not wanted)

        margin_needed = total_target / self.LEV.value
        if margin_needed > 0.99 * self._my_equity:
            logger.warning(f"Copied exposure needs ${margin_needed:,.2f} margin at {self.LEV.value}x "
                           f"but my account value is ${self._my_equity:,.2f}: positions will be "
                           "undersized. Raise LEV or copy a less leveraged wallet.")
        logger.info(f"Copy fidelity: sum|target-actual|/equity = {deviation / self._my_equity:.3f} "
                    f"| missing {n_missing} | extra {n_extra}")
        logger.info("=" * 80)
        append_csv(str(self.data_dir / 'fidelity.csv'), FIDELITY_FIELDS,
                   [[datetime.now(timezone.utc).isoformat(timespec='seconds'),
                     round(self._my_equity, 2), round(self._copied_equity, 2),
                     round(deviation / self._my_equity, 5), n_missing, n_extra]])

    # --- Signals ----------------------------------------------------------------------------

    def populate_indicators(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        df['signal'] = 2  # 1 = enter, 0 = exit, 2 = do nothing
        if not self._ok:
            return df
        try:
            df['signal'] = self._signal(metadata['pair'])
        except Exception as e:
            logger.error(f"Signal error for {metadata['pair']}, doing nothing: {e}")
        return df

    def _signal(self, pair: str) -> int:
        """Compare what the trader holds with what I hold, for this coin only.
        Entry/exit do not depend on catching the change event: a missed event is
        simply seen again on the next loop."""
        coin = coin_of(pair)
        held = coin in self._held
        wanted = self._wanted(coin)
        if held and not wanted:
            logger.info(f"{coin}: trader has no significant long, exiting.")
            return 0
        if wanted and not held:
            if self._blocked_after_stop(pair, coin):
                return 2
            logger.info(f"{coin}: trader holds a significant long, entering.")
            return 1
        return 2

    def _blocked_after_stop(self, pair: str, coin: str) -> bool:
        """After my stop-loss or liquidation, wait for the trader's next size change on the coin
        instead of buying straight back into the move."""
        closed = [t for t in Trade.get_trades_proxy(pair=pair, is_open=False) if t.close_date_utc]
        if not closed:
            return False
        last = max(closed, key=lambda t: t.close_date_utc)
        if last.exit_reason not in FORCED_EXITS:
            return False
        closed_ms = last.close_date_utc.timestamp() * 1000
        if self.tracker.last_size_change_ms.get(coin, 0) > closed_ms:
            return False
        logger.info(f"{coin}: last trade ended by {last.exit_reason}; waiting for the trader's "
                    "next change on this coin before re-entering.")
        return True

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[dataframe['signal'] == 1, 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[dataframe['signal'] == 0, 'exit_long'] = 1
        return dataframe

    # --- Sizing -----------------------------------------------------------------------------

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str,
                            **kwargs) -> float:
        # Returning None cancels the entry. On an exception freqtrade would fall back to
        # proposed_stake, hence the try/except. Freqtrade clamps the result to
        # [min_stake, max_stake] itself, and refuses if min_stake is > 30 % above it.
        try:
            if not self._ok:
                return None
            coin = coin_of(pair)
            if not self._wanted(coin):
                return None
            target_coins = scale_to_me(self._positions[coin].size, self._copied_equity, self._my_equity)
            # freqtrade buys stake / current_rate * leverage coins -> exactly target_coins.
            stake = target_coins * current_rate / leverage
            logger.info(f"Entry {pair}: target {target_coins:.6g} coins -> stake ${stake:,.2f} at {leverage}x")
            return stake
        except Exception as e:
            logger.error(f"Error in custom_stake_amount for {pair}: {e}")
            return None

    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: float | None, max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs
                              ) -> float | None | tuple[float | None, str | None]:
        """Resize towards target_coins = copied_size * my_equity / copied_equity.
        Tolerance on |target - actual| value: change_threshold % of my equity while the trader
        has resized this coin and I have not caught up ('pending'), otherwise
        adjustment_threshold % of the target (avoids churn on equity-ratio noise)."""
        # Freqtrade also calls this while orders are open; a returned stake would cancel them.
        if not self._ok or trade.has_open_orders or trade.amount <= 0:
            return None
        coin = coin_of(trade.pair)
        if not self._wanted(coin):
            return None  # the exit signal handles it
        now = time.monotonic()
        if now - self._last_adjust.get(trade.pair, float('-inf')) < self.cooldown_s:
            return None  # also keeps 'pending': the trader's change is applied after the cooldown

        pos = self._positions[coin]
        mark = pos.position_value / pos.size
        target_coins = scale_to_me(pos.size, self._copied_equity, self._my_equity)
        delta_coins = target_coins - trade.amount
        pending = coin in self._pending
        if pending:
            tolerance = self.change_threshold / 100.0 * self._my_equity
        else:
            tolerance = self.adjustment_threshold / 100.0 * target_coins * mark
        # Below tolerance, or below the exchange's minimum order: nothing to do (pending done).
        min_value = (min_stake or 0.0) * trade.leverage
        if abs(delta_coins) * mark <= max(tolerance, min_value):
            self._pending.discard(coin)
            return None

        stake = stake_for_delta(delta_coins, current_rate, trade.leverage, trade.amount, trade.stake_amount)
        if stake == 0:
            return None
        self._last_adjust[trade.pair] = now
        logger.info(f"Adjust {trade.pair}: {trade.amount:.6g} -> {target_coins:.6g} coins "
                    f"({'trader resized' if pending else 'drift'}), stake {stake:+,.2f}")
        return stake

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None, side: str,
                 **kwargs) -> float:
        return min(self.LEV.value, max_leverage)

"""
Core of the Hyperliquid copy-trading bot, free of freqtrade imports so that the
strategy (COPY_HL.py), the CLI (track_account.py) and the tests run the same code.

Copy model: for every coin, hold

    my_position_value = copied_position_value * my_equity / copied_equity

i.e. copy the trader's exposure ratio (position value / account value). Both
equities include unrealized PnL. Copied equity is Hyperliquid's portfolio value,
including spot assets; a unified account's perp accountValue is not its equity.
"""
import csv
import logging
import math
import os
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
POSITION_FIELDS = ['coin', 'size', 'entry_price', 'position_value',
                   'unrealized_pnl', 'leverage', 'margin_used', 'timestamp', 'human_time']
CHANGE_FIELDS = ['coin', 'change_type', 'old_size', 'new_size',
                 'old_position_value', 'new_position_value', 'timestamp', 'human_time']


def fetch_user_state(address: str, timeout: float = 10.0) -> Dict[str, Any]:
    """Perp positions and fresh portfolio equity from public Info endpoints (read-only).

    Raises on network error, HTTP error or unexpected payload, so callers can skip
    the loop instead of acting on bad data.
    """
    resp = requests.post(HL_INFO_URL, json={"type": "clearinghouseState", "user": address},
                         timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if (not isinstance(data, dict) or not isinstance(data.get("assetPositions"), list)
            or "accountValue" not in data.get("marginSummary", {})):
        raise ValueError(f"Unexpected clearinghouseState payload: {str(data)[:200]}")
    resp = requests.post(HL_INFO_URL, json={"type": "portfolio", "user": address}, timeout=timeout)
    resp.raise_for_status()
    # Use the same denominator in every account mode. Do not add perp/spot balances:
    # they overlap in unified accounts. 'day' also includes non-USDC spot assets.
    try:
        timestamp, value = dict(resp.json())["day"]["accountValueHistory"][-1]
        data["equity"] = float(value)
        age = time.time() - float(timestamp) / 1000.0
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise ValueError("Missing or invalid portfolio day equity point") from e
    if not math.isfinite(data["equity"]) or not math.isfinite(age) or not -5 <= age <= 300:
        raise ValueError("Portfolio equity is nonfinite or its timestamp is outside the freshness window")
    logger.debug("Account snapshot fetched: portfolio equity %.2f, age %.1fs", data["equity"], age)
    return data


def append_csv(path: str, header: List[str], rows: List[list]) -> None:
    """Append rows to a CSV file, writing the header when the file is new."""
    if not rows:
        return
    try:
        new_file = not os.path.exists(path)
        with open(path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(header)
            writer.writerows(rows)
    except OSError as e:
        logger.error(f"Failed to append to {path}: {e}")


# --- Sizing -------------------------------------------------------------------------------

def scale_to_me(quantity: float, copied_equity: float, my_equity: float) -> Optional[float]:
    """quantity * my_equity / copied_equity. The copy is linear, so this maps a copied
    position size [coins] or value [USDC] to my target. None if the copied account is empty."""
    if copied_equity <= 0:
        return None
    return quantity * my_equity / copied_equity


def is_significant(copied_value: float, copied_equity: float, threshold_pc: float) -> bool:
    """True if the position is more than threshold_pc % of the copied account value."""
    return copied_equity > 0 and copied_value / copied_equity * 100.0 > threshold_pc


def stake_for_delta(delta_coins: float, rate: float, leverage: float,
                    trade_amount: float, trade_stake: float) -> float:
    """Stake (margin, USDC) to return from adjust_trade_position so that my position
    changes by delta_coins. 0.0 means no action. Working in coins makes the result
    independent of which price (mark, order book level) each side uses.

    Increase: freqtrade buys stake / rate * leverage coins (execute_entry, same `rate`
      it passes to the callback)  ->  stake = delta * rate / leverage.
    Decrease: freqtrade sells |stake| * trade_amount / trade_stake coins, i.e. it converts
      at the average entry price (check_and_call_adjust_trade_position)
      ->  stake = -|delta| * trade_stake / trade_amount, capped at the whole position.
    The sign of the result always equals the sign of delta_coins.
    """
    if delta_coins >= 0:
        return delta_coins * rate / leverage if rate > 0 and leverage > 0 else 0.0
    if trade_amount <= 0 or trade_stake <= 0:
        return 0.0
    return -min(-delta_coins, trade_amount) * trade_stake / trade_amount


# --- Position tracking --------------------------------------------------------------------

@dataclass
class PositionSnapshot:
    coin: str
    size: float
    entry_price: float
    position_value: float
    unrealized_pnl: float
    leverage: float
    margin_used: float
    timestamp: int


@dataclass
class PositionChange:
    coin: str
    change_type: str  # opened_long, opened_short, closed, increased, decreased, flipped, modified
    old_size: Optional[float]
    new_size: float
    old_position_value: Optional[float]
    new_position_value: float
    timestamp: int
    human_time: str


class PositionTracker:
    """Detects position changes of the copied account between successive snapshots.

    On disk (data_dir): last_positions.csv (latest snapshot, rewritten),
    positions_history.csv and changes_log.csv (append-only logs, never read back
    except to recover the last size-change time per coin).
    """

    def __init__(self, data_dir: str = "position_data"):
        self.data_dir = str(data_dir)
        self.positions_file = os.path.join(self.data_dir, "positions_history.csv")
        self.changes_file = os.path.join(self.data_dir, "changes_log.csv")
        self.last_positions_file = os.path.join(self.data_dir, "last_positions.csv")

        self.last_positions: Dict[str, PositionSnapshot] = {}
        # coin -> server time [ms] of the trader's last size change (open/add/reduce/close/flip)
        self.last_size_change_ms: Dict[str, int] = {}

        os.makedirs(self.data_dir, exist_ok=True)
        self._load_data()

    def _position_row(self, pos: PositionSnapshot) -> list:
        return [pos.coin, pos.size, pos.entry_price, pos.position_value, pos.unrealized_pnl,
                pos.leverage, pos.margin_used, pos.timestamp, self._timestamp_to_human(pos.timestamp)]

    def _save_last_positions(self) -> None:
        """Save last positions to CSV"""
        try:
            with open(self.last_positions_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(POSITION_FIELDS)
                writer.writerows(self._position_row(p) for p in self.last_positions.values())
        except OSError as e:
            logger.error(f"Failed to save last positions: {e}")

    def _load_data(self) -> None:
        """Load the last snapshot and the last size-change time per coin"""
        try:
            if os.path.exists(self.last_positions_file):
                with open(self.last_positions_file, 'r', newline='', encoding='utf-8') as f:
                    for row in csv.DictReader(f):
                        self.last_positions[row['coin']] = PositionSnapshot(
                            coin=row['coin'],
                            size=float(row['size']),
                            entry_price=float(row['entry_price']),
                            position_value=float(row['position_value']),
                            unrealized_pnl=float(row['unrealized_pnl']),
                            leverage=float(row['leverage']),
                            margin_used=float(row['margin_used']),
                            timestamp=int(row['timestamp'])
                        )
            if os.path.exists(self.changes_file):
                with open(self.changes_file, 'r', newline='', encoding='utf-8') as f:
                    for row in csv.DictReader(f):
                        if row['change_type'] != 'modified':
                            self.last_size_change_ms[row['coin']] = int(row['timestamp'])
        except (OSError, ValueError, KeyError) as e:
            logger.warning(f"Failed to load tracking data from {self.data_dir}: {e}")

        logger.info(f"Tracker in {self.data_dir}/: {len(self.last_positions)} positions in last snapshot")

    def _timestamp_to_human(self, timestamp: int) -> str:
        """Convert timestamp to human readable format"""
        return datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')

    def _extract_positions(self, data: Dict[str, Any]) -> Dict[str, PositionSnapshot]:
        """Extract position data from the JSON response"""
        positions = {}
        timestamp = data.get('time', 0)

        for asset_pos in data.get('assetPositions', []):
            if asset_pos['type'] == 'oneWay' and 'position' in asset_pos:
                pos = asset_pos['position']
                coin = pos['coin']

                # Convert size to float, handle both string and numeric values
                size = float(pos['szi'])

                # Skip positions with zero size
                if size == 0:
                    continue

                leverage_value = pos['leverage']['value'] if isinstance(pos['leverage'], dict) else pos['leverage']

                positions[coin] = PositionSnapshot(
                    coin=coin,
                    size=size,
                    entry_price=float(pos['entryPx']),
                    position_value=float(pos['positionValue']),
                    unrealized_pnl=float(pos['unrealizedPnl']),
                    leverage=float(leverage_value),
                    margin_used=float(pos['marginUsed']),
                    timestamp=timestamp
                )

        return positions

    def _detect_changes(self, current_positions: Dict[str, PositionSnapshot],
                        payload_time: Optional[int] = None) -> List[PositionChange]:
        """Detect changes between current and last positions.

        payload_time is the server-provided timestamp (ms) from the source payload.
        It is used as the canonical timestamp so that close-all events (where
        current_positions is empty) still get the correct time, not now().
        """
        changes = []
        if payload_time is not None:
            timestamp = int(payload_time)
        elif current_positions:
            timestamp = list(current_positions.values())[0].timestamp
        elif self.last_positions:
            timestamp = list(self.last_positions.values())[0].timestamp
        else:
            timestamp = int(datetime.now().timestamp() * 1000)
        human_time = self._timestamp_to_human(timestamp)

        # Check for closed positions
        for coin in self.last_positions:
            if coin not in current_positions:
                old_pos = self.last_positions[coin]
                changes.append(PositionChange(
                    coin=coin,
                    change_type='closed',
                    old_size=old_pos.size,
                    new_size=0.0,
                    old_position_value=old_pos.position_value,
                    new_position_value=0.0,
                    timestamp=timestamp,
                    human_time=human_time
                ))

        # Check for new, modified, increased, or decreased positions
        for coin, current_pos in current_positions.items():
            if coin not in self.last_positions:
                # New position opened
                position_type = "long" if current_pos.size > 0 else "short"
                changes.append(PositionChange(
                    coin=coin,
                    change_type=f'opened_{position_type}',
                    old_size=None,
                    new_size=current_pos.size,
                    old_position_value=None,
                    new_position_value=current_pos.position_value,
                    timestamp=timestamp,
                    human_time=human_time
                ))
            else:
                old_pos = self.last_positions[coin]

                # Check for size changes (significant changes only)
                if abs(current_pos.size - old_pos.size) > 1e-8:
                    change_type = self._determine_change_type(old_pos.size, current_pos.size)
                # Check for significant modifications (leverage, entry price changes)
                elif (abs(current_pos.leverage - old_pos.leverage) > 1e-8 or
                      abs(current_pos.entry_price - old_pos.entry_price) > 1e-6):
                    change_type = 'modified'
                else:
                    # Ignore pure P&L changes (position_value, unrealized_pnl, margin_used changes
                    # without size/leverage/entry_price changes are just market movements)
                    continue

                changes.append(PositionChange(
                    coin=coin,
                    change_type=change_type,
                    old_size=old_pos.size,
                    new_size=current_pos.size,
                    old_position_value=old_pos.position_value,
                    new_position_value=current_pos.position_value,
                    timestamp=timestamp,
                    human_time=human_time
                ))

        return changes

    def _determine_change_type(self, old_size: float, new_size: float) -> str:
        """Determine the type of change considering long/short positions"""
        # Check for direction flip (long to short or short to long)
        if (old_size > 0 and new_size < 0) or (old_size < 0 and new_size > 0):
            return 'flipped'

        # Same direction changes
        if old_size > 0 and new_size > 0:  # Both long
            return 'increased' if new_size > old_size else 'decreased'
        elif old_size < 0 and new_size < 0:  # Both short
            # For shorts: more negative = larger short position
            return 'increased' if abs(new_size) > abs(old_size) else 'decreased'

        return 'modified'

    def track_positions(self, position_data: Dict[str, Any]) -> List[PositionChange]:
        """Compare a clearinghouseState payload with the last snapshot, log and return the changes."""
        current_positions = self._extract_positions(position_data)

        # Thread the payload's server-side timestamp through so close-all events
        # still use the right time rather than now().
        changes = self._detect_changes(current_positions, payload_time=position_data.get('time'))

        changed_coins = {c.coin for c in changes}
        append_csv(self.positions_file, POSITION_FIELDS,
                   [self._position_row(p) for coin, p in current_positions.items() if coin in changed_coins])
        append_csv(self.changes_file, CHANGE_FIELDS,
                   [[c.coin, c.change_type, '' if c.old_size is None else c.old_size, c.new_size,
                     '' if c.old_position_value is None else c.old_position_value,
                     c.new_position_value, c.timestamp, c.human_time] for c in changes])

        for c in changes:
            if c.change_type != 'modified':
                self.last_size_change_ms[c.coin] = c.timestamp

        self.last_positions = deepcopy(current_positions)
        self._save_last_positions()
        return changes

    def print_changes(self, changes: List[PositionChange]) -> None:
        """Log detected changes in a readable format"""
        if not changes:
            logger.info("No position changes detected.")
            return

        logger.info(f"=== Position Changes Detected ({len(changes)} changes) ===")
        for change in changes:
            logger.info(f"[{change.human_time}] {change.coin} - {change.change_type.upper()}")

            if change.change_type.startswith('opened'):
                direction = "LONG" if change.new_size > 0 else "SHORT"
                logger.info(f"  New {direction} position: {abs(change.new_size):,.4f} (${change.new_position_value:,.2f})")
            elif change.change_type == 'closed':
                old_direction = "LONG" if change.old_size > 0 else "SHORT"
                logger.info(f"  Closed {old_direction} position: {abs(change.old_size):,.4f} (was ${change.old_position_value:,.2f})")
            elif change.change_type == 'flipped':
                old_direction = "LONG" if change.old_size > 0 else "SHORT"
                new_direction = "LONG" if change.new_size > 0 else "SHORT"
                logger.info(f"  Position flipped from {old_direction} to {new_direction}")
                logger.info(f"  Size: {change.old_size:,.4f} → {change.new_size:,.4f}")
                logger.info(f"  Value: ${change.old_position_value:,.2f} → ${change.new_position_value:,.2f}")
            elif change.change_type in ['increased', 'decreased']:
                direction = "LONG" if change.new_size > 0 else "SHORT"
                size_diff = change.new_size - change.old_size
                value_diff = change.new_position_value - change.old_position_value
                logger.info(f"  {direction} position {change.change_type}")
                logger.info(f"  Size: {change.old_size:,.4f} → {change.new_size:,.4f} ({size_diff:+,.4f})")
                logger.info(f"  Value: ${change.old_position_value:,.2f} → ${change.new_position_value:,.2f} ({value_diff:+,.2f})")
            elif change.change_type == 'modified':
                direction = "LONG" if change.new_size > 0 else "SHORT"
                logger.info(f"  {direction} position modified (same size: {change.new_size:,.4f})")
                logger.info(f"  Value: ${change.old_position_value:,.2f} → ${change.new_position_value:,.2f}")

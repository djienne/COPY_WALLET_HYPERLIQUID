import pandas as pd
from freqtrade.strategy import (IStrategy, IntParameter)
from freqtrade.persistence import Trade
import logging
import os
import json
import time
from pathlib import Path
import json
import os
import csv
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from copy import deepcopy

logger = logging.getLogger(__name__)

ADDRESS_TO_TRACK_TOP = "0x4b66f4048a0a90fd5ff44abbe5d68332656b78b8"

#####################################################################################################################################################################################################
# Classes used to manage the copied wallet position tracking
#####################################################################################################################################################################################################

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
    change_type: str
    old_size: Optional[float]
    new_size: float
    old_position_value: Optional[float]
    new_position_value: float
    timestamp: int
    human_time: str

class PositionTracker:
    def __init__(self, data_dir: str = "position_data"):
        self.data_dir = data_dir
        self.positions_file = os.path.join(data_dir, "positions_history.csv")
        self.changes_file = os.path.join(data_dir, "changes_log.csv")
        self.last_positions_file = os.path.join(data_dir, "last_positions.csv")
        
        self.position_history: Dict[str, List[PositionSnapshot]] = {}
        self.last_positions: Dict[str, PositionSnapshot] = {}
        self.changes_log: List[PositionChange] = []
        
        # Create data directory if it doesn't exist
        os.makedirs(data_dir, exist_ok=True)
        
        # Load existing data if available
        self._load_data()
        
    def _save_positions_history(self) -> None:
        """Save position history to CSV"""
        try:
            with open(self.positions_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'coin', 'size', 'entry_price', 'position_value', 
                    'unrealized_pnl', 'leverage', 'margin_used', 'timestamp', 'human_time'
                ])
                
                for coin, positions in self.position_history.items():
                    for pos in positions:
                        writer.writerow([
                            pos.coin, pos.size, pos.entry_price, pos.position_value,
                            pos.unrealized_pnl, pos.leverage, pos.margin_used, 
                            pos.timestamp, self._timestamp_to_human(pos.timestamp)
                        ])
        except Exception as e:
            logger.info(f"Warning: Failed to save positions history: {e}")
    
    def _save_last_positions(self) -> None:
        """Save last positions to CSV"""
        try:
            with open(self.last_positions_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'coin', 'size', 'entry_price', 'position_value', 
                    'unrealized_pnl', 'leverage', 'margin_used', 'timestamp', 'human_time'
                ])
                
                for pos in self.last_positions.values():
                    writer.writerow([
                        pos.coin, pos.size, pos.entry_price, pos.position_value,
                        pos.unrealized_pnl, pos.leverage, pos.margin_used, 
                        pos.timestamp, self._timestamp_to_human(pos.timestamp)
                    ])
        except Exception as e:
            logger.info(f"Warning: Failed to save last positions: {e}")
    
    def _save_changes_log(self) -> None:
        """Save changes log to CSV"""
        try:
            with open(self.changes_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'coin', 'change_type', 'old_size', 'new_size', 
                    'old_position_value', 'new_position_value', 'timestamp', 'human_time'
                ])
                
                for change in self.changes_log:
                    writer.writerow([
                        change.coin, change.change_type, change.old_size or '', change.new_size,
                        change.old_position_value or '', change.new_position_value, 
                        change.timestamp, change.human_time
                    ])
        except Exception as e:
            logger.info(f"Warning: Failed to save changes log: {e}")
    
    def _save_data(self) -> None:
        """Save all tracking data to CSV files"""
        self._save_positions_history()
        self._save_last_positions()
        self._save_changes_log()
    
    def _load_positions_history(self) -> None:
        """Load position history from CSV"""
        if not os.path.exists(self.positions_file):
            return
            
        try:
            with open(self.positions_file, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                self.position_history = {}
                
                for row in reader:
                    coin = row['coin']
                    if coin not in self.position_history:
                        self.position_history[coin] = []
                    
                    pos = PositionSnapshot(
                        coin=coin,
                        size=float(row['size']),
                        entry_price=float(row['entry_price']),
                        position_value=float(row['position_value']),
                        unrealized_pnl=float(row['unrealized_pnl']),
                        leverage=float(row['leverage']),
                        margin_used=float(row['margin_used']),
                        timestamp=int(row['timestamp'])
                    )
                    self.position_history[coin].append(pos)
                    
        except Exception as e:
            logger.info(f"Warning: Failed to load position history: {e}")
    
    def _load_last_positions(self) -> None:
        """Load last positions from CSV"""
        if not os.path.exists(self.last_positions_file):
            return
            
        try:
            with open(self.last_positions_file, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                self.last_positions = {}
                
                for row in reader:
                    coin = row['coin']
                    pos = PositionSnapshot(
                        coin=coin,
                        size=float(row['size']),
                        entry_price=float(row['entry_price']),
                        position_value=float(row['position_value']),
                        unrealized_pnl=float(row['unrealized_pnl']),
                        leverage=float(row['leverage']),
                        margin_used=float(row['margin_used']),
                        timestamp=int(row['timestamp'])
                    )
                    self.last_positions[coin] = pos
                    
        except Exception as e:
            logger.info(f"Warning: Failed to load last positions: {e}")
    
    def _load_changes_log(self) -> None:
        """Load changes log from CSV"""
        if not os.path.exists(self.changes_file):
            return
            
        try:
            with open(self.changes_file, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                self.changes_log = []
                
                for row in reader:
                    old_size = float(row['old_size']) if row['old_size'] else None
                    old_pos_value = float(row['old_position_value']) if row['old_position_value'] else None
                    
                    change = PositionChange(
                        coin=row['coin'],
                        change_type=row['change_type'],
                        old_size=old_size,
                        new_size=float(row['new_size']),
                        old_position_value=old_pos_value,
                        new_position_value=float(row['new_position_value']),
                        timestamp=int(row['timestamp']),
                        human_time=row['human_time']
                    )
                    self.changes_log.append(change)
                    
        except Exception as e:
            logger.info(f"Warning: Failed to load changes log: {e}")
    
    def _load_data(self) -> None:
        """Load all tracking data from CSV files"""
        self._load_positions_history()
        self._load_last_positions()
        self._load_changes_log()
        
        if self.last_positions or self.changes_log:
            logger.info(f"Loaded tracking data: {len(self.last_positions)} current positions, "
                  f"{len(self.changes_log)} historical changes from {self.data_dir}/")
        else:
            logger.info(f"No existing data found. Starting with fresh tracking data in {self.data_dir}/")
    
    def export_to_json(self, filename: str = None) -> str:
        """Export tracking data to JSON format for easy viewing"""
        if filename is None:
            filename = os.path.join(self.data_dir, f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        
        # Convert dataclasses to dictionaries for JSON serialization
        export_data = {
            'position_history': {
                coin: [asdict(pos) for pos in positions] 
                for coin, positions in self.position_history.items()
            },
            'last_positions': {
                coin: asdict(pos) for coin, pos in self.last_positions.items()
            },
            'changes_log': [asdict(change) for change in self.changes_log],
            'export_timestamp': datetime.now().isoformat(),
            'total_tracked_coins': len(self.position_history),
            'total_changes': len(self.changes_log)
        }
        
        try:
            with open(filename, 'w') as f:
                json.dump(export_data, f, indent=2, default=str)
            logger.info(f"Data exported to {filename}")
            return filename
        except Exception as e:
            logger.info(f"Failed to export data: {e}")
            return ""
        
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
                
                snapshot = PositionSnapshot(
                    coin=coin,
                    size=size,
                    entry_price=float(pos['entryPx']),
                    position_value=float(pos['positionValue']),
                    unrealized_pnl=float(pos['unrealizedPnl']),
                    leverage=float(leverage_value),
                    margin_used=float(pos['marginUsed']),
                    timestamp=timestamp
                )
                
                positions[coin] = snapshot
                
        return positions
    
    def _detect_changes(self, current_positions: Dict[str, PositionSnapshot]) -> List[PositionChange]:
        """Detect changes between current and last positions"""
        changes = []
        timestamp = list(current_positions.values())[0].timestamp if current_positions else int(datetime.now().timestamp() * 1000)
        human_time = self._timestamp_to_human(timestamp)
        
        # Check for closed positions
        for coin in self.last_positions:
            if coin not in current_positions:
                old_pos = self.last_positions[coin]
                change = PositionChange(
                    coin=coin,
                    change_type='closed',
                    old_size=old_pos.size,
                    new_size=0.0,
                    old_position_value=old_pos.position_value,
                    new_position_value=0.0,
                    timestamp=timestamp,
                    human_time=human_time
                )
                changes.append(change)
        
        # Check for new, modified, increased, or decreased positions
        for coin, current_pos in current_positions.items():
            if coin not in self.last_positions:
                # New position opened
                position_type = "long" if current_pos.size > 0 else "short"
                change = PositionChange(
                    coin=coin,
                    change_type=f'opened_{position_type}',
                    old_size=None,
                    new_size=current_pos.size,
                    old_position_value=None,
                    new_position_value=current_pos.position_value,
                    timestamp=timestamp,
                    human_time=human_time
                )
                changes.append(change)
            else:
                old_pos = self.last_positions[coin]
                
                # Check for size changes (significant changes only)
                if abs(current_pos.size - old_pos.size) > 1e-8:
                    change_type = self._determine_change_type(old_pos.size, current_pos.size)
                    
                    change = PositionChange(
                        coin=coin,
                        change_type=change_type,
                        old_size=old_pos.size,
                        new_size=current_pos.size,
                        old_position_value=old_pos.position_value,
                        new_position_value=current_pos.position_value,
                        timestamp=timestamp,
                        human_time=human_time
                    )
                    changes.append(change)
                
                # Check for significant modifications (leverage, entry price changes)
                elif (abs(current_pos.leverage - old_pos.leverage) > 1e-8 or 
                      abs(current_pos.entry_price - old_pos.entry_price) > 1e-6):  # Higher threshold for entry price
                    change = PositionChange(
                        coin=coin,
                        change_type='modified',
                        old_size=old_pos.size,
                        new_size=current_pos.size,
                        old_position_value=old_pos.position_value,
                        new_position_value=current_pos.position_value,
                        timestamp=timestamp,
                        human_time=human_time
                    )
                    changes.append(change)
                
                # Ignore pure P&L changes (position_value, unrealized_pnl, margin_used changes 
                # without size/leverage/entry_price changes are just market movements)
        
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
        """
        Main function to track positions and detect changes
        
        Args:
            position_data: JSON data containing position information
            
        Returns:
            List of detected changes
        """
        # Extract current positions
        current_positions = self._extract_positions(position_data)
        
        # Detect changes
        changes = self._detect_changes(current_positions)
        
        # Only update history if there are actual position changes (not just P&L updates)
        if changes:
            timestamp = position_data.get('time', int(datetime.now().timestamp() * 1000))
            for coin, position in current_positions.items():
                if coin not in self.position_history:
                    self.position_history[coin] = []
                
                # Only add to history if this represents a significant change
                # (new position, size change, leverage change, etc.)
                should_add_to_history = any(
                    change.coin == coin and change.change_type in [
                        'opened_long', 'opened_short', 'closed', 'increased', 
                        'decreased', 'flipped', 'modified'
                    ] for change in changes
                )
                
                if should_add_to_history:
                    self.position_history[coin].append(position)
        
        # Always log changes (even if empty for completeness)
        self.changes_log.extend(changes)
        
        # Always update last positions (for tracking future changes)
        self.last_positions = deepcopy(current_positions)
        
        # Save data to file after each update (but only if there were changes)
        if changes:
            self._save_data()
        else:
            # Still need to save last_positions for change detection, but not full history
            self._save_last_positions()
        
        return changes
    
    def print_changes(self, changes: List[PositionChange]) -> None:
        """Print detected changes in a readable format"""
        if not changes:
            logger.info("No position changes detected.")
            return
            
        logger.info(f"\n=== Position Changes Detected ({len(changes)} changes) ===")
        for change in changes:
            position_info = self._get_position_info(change.new_size if change.new_size != 0 else change.old_size)
            
            logger.info(f"\n[{change.human_time}] {change.coin} - {change.change_type.upper()}")
            
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
                
                # For display purposes, show absolute values but indicate direction
                logger.info(f"  {direction} position {change.change_type}")
                logger.info(f"  Size: {change.old_size:,.4f} → {change.new_size:,.4f} ({size_diff:+,.4f})")
                logger.info(f"  Value: ${change.old_position_value:,.2f} → ${change.new_position_value:,.2f} ({value_diff:+,.2f})")
            elif change.change_type == 'modified':
                direction = "LONG" if change.new_size > 0 else "SHORT"
                logger.info(f"  {direction} position modified (same size: {change.new_size:,.4f})")
                logger.info(f"  Value: ${change.old_position_value:,.2f} → ${change.new_position_value:,.2f}")
    
    def _get_position_info(self, size: float) -> str:
        """Get position direction info"""
        if size > 0:
            return "LONG"
        elif size < 0:
            return "SHORT"
        else:
            return "CLOSED"
    
    def get_current_positions(self) -> Dict[str, PositionSnapshot]:
        """Get current positions"""
        return self.last_positions.copy()
    
    def get_position_history(self, coin: Optional[str] = None) -> Dict[str, List[PositionSnapshot]]:
        """Get position history for a specific coin or all coins"""
        if coin:
            return {coin: self.position_history.get(coin, [])}
        return self.position_history.copy()
    
    def clear_data(self, confirm: bool = False) -> None:
        """Clear all tracking data (use with caution)"""
        if not confirm:
            logger.info("Use clear_data(confirm=True) to actually clear the data.")
            return
            
        self.position_history = {}
        self.last_positions = {}
        self.changes_log = []
        
        # Remove CSV files
        files_to_remove = [self.positions_file, self.changes_file, self.last_positions_file]
        for file_path in files_to_remove:
            if os.path.exists(file_path):
                os.remove(file_path)
        
        logger.info(f"All tracking data cleared from {self.data_dir}/")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about tracked data"""
        stats = {
            'total_coins_tracked': len(self.position_history),
            'current_active_positions': len(self.last_positions),
            'total_changes': len(self.changes_log),
            'data_directory': self.data_dir,
            'csv_files': {
                'positions_history': os.path.exists(self.positions_file),
                'changes_log': os.path.exists(self.changes_file),
                'last_positions': os.path.exists(self.last_positions_file)
            }
        }
        
        if self.changes_log:
            stats['first_change'] = self._timestamp_to_human(self.changes_log[0].timestamp)
            stats['last_change'] = self._timestamp_to_human(self.changes_log[-1].timestamp)
        
        return stats

############################################################################################################################################################################################################
# End of classes usesd to manage the copied wallet position tracking
############################################################################################################################################################################################################

## freqtrade strategy class

class COPY_HL(IStrategy):
    global ADDRESS_TO_TRACK_TOP
    minimal_roi = {
        "0": 5000.0  # Effectively disables ROI
    }
    stoploss = -0.95
    timeframe = '1h'
    startup_candle_count: int = 0
    can_short: bool = False
    process_only_new_candles: bool = False
    position_adjustment_enable = True

    # Tunable parameters
    LEV = IntParameter(1, 6, default=6, space='buy', optimize=False)  # Leverage to use
    change_threshold = 0.5 # in %
    adjustement_threshold = 10.0 # in %
    ADDRESS_TO_TRACK = ADDRESS_TO_TRACK_TOP

                            return df
                        else:
                            logger.info(f"Not opening position on {coin_ticker} because position size in copied account is too small compared to the copied account equity ({ratio_pc:.2f} , less than 1%)")
                    elif 'closed' in chg.change_type:
                        df['signal'] = 0
                        return df
        else: # Handle missed entries/exits when no changes detected
            df = self._check_missed_entry_or_exit(coin_ticker, df)

        # check if mistaken Long that is actually a Short -> send exit signal
        df = self.check_mistaken_short(df, coin_ticker)

        return df
    
    def _check_missed_entry_or_exit(self, coin_ticker, df):
        """Helper method to check for missed positions"""
        try:
            my_trades = Trade.get_trades_proxy(is_open=True)
            my_current_opened_tickers = [tr.pair.replace("/USDC:USDC", "") for tr in my_trades]
            
            # Check for missed entries
            if coin_ticker in self.current_positions_to_copy:
                if coin_ticker not in my_current_opened_tickers:
                    is_short = float(self.current_positions_to_copy[coin_ticker].size)<0.0
                    if self._is_position_significant(coin_ticker) and not is_short:
                        df['signal'] = 1
                        logger.info(f"Missed entry detected for {coin_ticker}. Sending entry signal.")
            
            # Check for missed exits
            #   not in current positions to copy, but somehow in my current position
            if coin_ticker not in self.current_positions_to_copy and coin_ticker in my_current_opened_tickers:
                    df['signal'] = 0
                    logger.info(f"Missed exit detected for {coin_ticker}. Sending exit signal.")

            #   in current positions to copy but not really because very small amount, but somehow in my current position
            if coin_ticker in self.current_positions_to_copy and coin_ticker in my_current_opened_tickers:
                    if not self._is_position_significant(coin_ticker) :
                        df['signal'] = 0
                        logger.info(f"Missed exit detected for {coin_ticker}. Sending exit signal.")
                
        except Exception as e:
            logger.error(f"Error checking missed positions for {coin_ticker}: {e}")
        
        return df
    
    def _is_position_significant(self, coin_ticker):
        """Check if position is significant enough to copy"""
        try:
            if not self._cached_perp_data:
                return None
                
            perp_data = self.GET_PERP_ACCOUNT_STATUS(self.ADDRESS_TO_TRACK)
            copied_account_value = float(perp_data['marginSummary']['accountValue'])
            #logger.info(f"copied account value: {copied_account_value}")
            min_threshold = copied_account_value / (100.0/self.change_threshold)  # 1% threshold

            position_value = self.current_positions_to_copy[coin_ticker].position_value

            return position_value > min_threshold
        except Exception as e:
            logger.error(f"Error checking position significance: {e}")
            return None

    def check_mistaken_short(self, df, coin_ticker):
        """
        """
        if coin_ticker in self.current_positions_to_copy:
            my_trades = Trade.get_trades_proxy(is_open=True)
            my_current_opened_tickers = [tr.pair.replace("/USDC:USDC", "") for tr in my_trades]
            if coin_ticker in my_current_opened_tickers:
                is_short = float(self.current_positions_to_copy[coin_ticker].size) < 0.0
                if is_short:
                    df['signal'] = 0
                    logger.info(f"There was a short mistaken as a long on {coin_ticker}. This code is LONG ONLY. Exiting immediatly.")
                    return df
        return df

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[dataframe['signal'] == 1, 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[dataframe['signal'] == 0, 'exit_long'] = 1
        return dataframe

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str,
                            **kwargs) -> float:
        # Called before entering a trade, makes it possible to manage your position size when placing a new trade.
        # Returning 0 or None will prevent trades from being placed -> ACTS ALSO LIKE AN ENTRY CONFIRMATION
        # Freqtrade will fall back to the proposed_stake value should your code raise an exception. The exception itself will be logged.
        # You do not have to ensure that min_stake <= returned_value <= max_stake. Trades will succeed as the returned value will be clamped to supported range and this action will be logged.

        coin_ticker = pair.replace("/USDC:USDC", "")
        self.wallets.update()

        if not self._got_perp_data_account_state_successfully :
            return None
        
        try:
            if not self._cached_perp_data:
                logger.error("No cached perp data available")
                return None
            
            perp_data = self.GET_PERP_ACCOUNT_STATUS(self.ADDRESS_TO_TRACK)
            copied_account_value = float(perp_data['marginSummary']['accountValue'])
            my_account_value = float(self.get_stake_total())
            scale_factor = my_account_value / copied_account_value

            # Look in both position changes and current positions

            position_value_in_copied_account = None
            
            # First check position changes
            for chg in self.copied_account_position_changes:
                if coin_ticker == chg.coin:
                    position_value_in_copied_account = float(chg.new_position_value)
                    break
            
            # If not found in changes, check current positions
            if position_value_in_copied_account is None:
                if coin_ticker in self.current_positions_to_copy:
                    position_value_in_copied_account = self.current_positions_to_copy[coin_ticker].position_value
            
            if position_value_in_copied_account is None:
                logger.warning(f"No position value found for {coin_ticker}")
                return None
            
            ratio_pc = position_value_in_copied_account/copied_account_value * 100.0
            if ratio_pc<self.change_threshold:
                logger.info(f"Not opening position on {pair} because position size in copied account is too small compared to the copied account equity({ratio_pc:.1f}% < 1%)")
                return None

            dust_USDC = 0.51
            returned_val = position_value_in_copied_account * scale_factor
            returned_val = (returned_val / leverage) - dust_USDC

            if returned_val < min_stake:
                returned_val = min_stake

            logger.info(f"Calculated stake for {pair}: {returned_val}")
            return returned_val
            
        except Exception as e:
            logger.error(f"Error in custom_stake_amount: {e}")
            return None
    
    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                                current_rate: float, current_profit: float,
                                min_stake: float | None, max_stake: float,
                                current_entry_rate: float, current_exit_rate: float,
                                current_entry_profit: float, current_exit_profit: float,
                                **kwargs
                                ) -> float | None | tuple[float | None, str | None]:
        # :return float: Stake amount to adjust your trade,
        #                Positive values to increase position, Negative values to decrease position.
        #                Return None for no action.
        #                Optionally, return a tuple with a 2nd element with an order reason

        coin_ticker = trade.pair.replace("/USDC:USDC", "")
        self.wallets.update()

        dust_USDC = 0.51

        if not self._got_perp_data_account_state_successfully :
            return None

        if self._time_of_change is not None:
            if datetime.now() > self._time_of_change + timedelta(seconds=self._cooldown_seconds_after_position_change):
                self._is_cooldown_after_position_change = False

        if self._is_cooldown_after_position_change:
            logger.info(f"Not doing position size change because of the cooldown of {self._cooldown_seconds_after_position_change} seconds.") 
            return None

        try:
            if not self._cached_perp_data:
                return None
            
            perp_data = self.GET_PERP_ACCOUNT_STATUS(self.ADDRESS_TO_TRACK)
            
            if self.copied_account_position_changes:
                copied_account_value = float(perp_data['marginSummary']['accountValue'])
                my_account_value = float(self.get_stake_total())
                scale_factor = my_account_value / copied_account_value

                # for detected changes in the copied account
                for chg in self.copied_account_position_changes:
                    if coin_ticker == chg.coin:
                        # Check if change is significant (>0.5% of account), otherwise skip doing adjustment by returning None
                        change_ratio_pc = abs(float(chg.old_position_value) - float(chg.new_position_value)) / copied_account_value * 100.0
                        if change_ratio_pc < self.change_threshold:
                            logger.info(f"Not increasing or decreasing position on {trade.pair} because position change in copied account is too small compared to the copied account equity({change_ratio_pc:.1f}% < 1%)")
                            return None

                        delta_stake = abs(float(chg.old_position_value) - float(chg.new_position_value)) * scale_factor

                        self._time_of_change = datetime.now()
                        self._is_cooldown_after_position_change = True 
                        
                        if 'increased' in chg.change_type:
                            return delta_stake / trade.leverage - dust_USDC
                        elif 'decreased' in chg.change_type:
                            return -1.0 * delta_stake / trade.leverage - dust_USDC
                    
            # for already opened positions, if difference with what it should be in copied account (and scaled) is too large (>10%), adjust to match
            if self.matching_positions_check_output:
                for pos in self.matching_positions_check_output:
                    logger.info(f"{pos['coin']} → Difference: {pos['diff_pc']:.2f}%   (my total value: {pos['my_value']:.1f}) ; ||>{self.adjustement_threshold:.0f}% will trigger a size correction.")
                    if pos['coin']==coin_ticker:
                        if abs(pos['diff_pc'])>self.adjustement_threshold:
                            #logger.info(pos['diff_pc'])
                            delta_stake = pos['my_value']/(1.0 + pos['diff_pc']/100.0)-pos['my_value']
                            logger.info(delta_stake)
                            logger.info(delta_stake / trade.leverage)
                            self._time_of_change = datetime.now()
                            self._is_cooldown_after_position_change = True
                            return delta_stake / trade.leverage - dust_USDC
            return None
            
        except Exception as e:
            logger.error(f"Error in adjust_trade_position: {e}")
            return None
        
    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None, side: str,
                 **kwargs) -> float:
        lev = min(self.LEV.value, max_leverage)
        return lev
import json
import os
import csv
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from copy import deepcopy

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
    change_type: str  # 'opened', 'closed', 'increased', 'decreased', 'modified'
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
            print(f"Warning: Failed to save positions history: {e}")
    
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
            print(f"Warning: Failed to save last positions: {e}")
    
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
            print(f"Warning: Failed to save changes log: {e}")
    
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
            print(f"Warning: Failed to load position history: {e}")
    
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
            print(f"Warning: Failed to load last positions: {e}")
    
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
            print(f"Warning: Failed to load changes log: {e}")
    
    def _load_data(self) -> None:
        """Load all tracking data from CSV files"""
        self._load_positions_history()
        self._load_last_positions()
        self._load_changes_log()
        
        if self.last_positions or self.changes_log:
            print(f"Loaded tracking data: {len(self.last_positions)} current positions, "
                  f"{len(self.changes_log)} historical changes from {self.data_dir}/")
        else:
            print(f"No existing data found. Starting with fresh tracking data in {self.data_dir}/")
    
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
            print(f"Data exported to {filename}")
            return filename
        except Exception as e:
            print(f"Failed to export data: {e}")
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
            print("No position changes detected.")
            return
            
        print(f"\n=== Position Changes Detected ({len(changes)} changes) ===")
        for change in changes:
            position_info = self._get_position_info(change.new_size if change.new_size != 0 else change.old_size)
            
            print(f"\n[{change.human_time}] {change.coin} - {change.change_type.upper()}")
            
            if change.change_type.startswith('opened'):
                direction = "LONG" if change.new_size > 0 else "SHORT"
                print(f"  New {direction} position: {abs(change.new_size):,.4f} (${change.new_position_value:,.2f})")
            elif change.change_type == 'closed':
                old_direction = "LONG" if change.old_size > 0 else "SHORT"
                print(f"  Closed {old_direction} position: {abs(change.old_size):,.4f} (was ${change.old_position_value:,.2f})")
            elif change.change_type == 'flipped':
                old_direction = "LONG" if change.old_size > 0 else "SHORT"
                new_direction = "LONG" if change.new_size > 0 else "SHORT"
                print(f"  Position flipped from {old_direction} to {new_direction}")
                print(f"  Size: {change.old_size:,.4f} → {change.new_size:,.4f}")
                print(f"  Value: ${change.old_position_value:,.2f} → ${change.new_position_value:,.2f}")
            elif change.change_type in ['increased', 'decreased']:
                direction = "LONG" if change.new_size > 0 else "SHORT"
                size_diff = change.new_size - change.old_size
                value_diff = change.new_position_value - change.old_position_value
                
                # For display purposes, show absolute values but indicate direction
                print(f"  {direction} position {change.change_type}")
                print(f"  Size: {change.old_size:,.4f} → {change.new_size:,.4f} ({size_diff:+,.4f})")
                print(f"  Value: ${change.old_position_value:,.2f} → ${change.new_position_value:,.2f} ({value_diff:+,.2f})")
            elif change.change_type == 'modified':
                direction = "LONG" if change.new_size > 0 else "SHORT"
                print(f"  {direction} position modified (same size: {change.new_size:,.4f})")
                print(f"  Value: ${change.old_position_value:,.2f} → ${change.new_position_value:,.2f}")
    
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
            print("Use clear_data(confirm=True) to actually clear the data.")
            return
            
        self.position_history = {}
        self.last_positions = {}
        self.changes_log = []
        
        # Remove CSV files
        files_to_remove = [self.positions_file, self.changes_file, self.last_positions_file]
        for file_path in files_to_remove:
            if os.path.exists(file_path):
                os.remove(file_path)
        
        print(f"All tracking data cleared from {self.data_dir}/")
    
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


def GET_CURRENT_PERP_ACCOUNT_STATUS(address):
    from hyperliquid.info import Info
    from hyperliquid.utils import constants

    # Fetch spot metadata
    info = Info(constants.MAINNET_API_URL, skip_ws=True)

    perp_user_state = info.user_state(address)

    return perp_user_state

# Example usage function
def main():
    # Initialize tracker
    tracker = PositionTracker()
    
    # Sample data (your provided JSON)
    address = "0x4b66f4048a0a90fd5ff44abbe5d68332656b78b8"
    sample_data = GET_CURRENT_PERP_ACCOUNT_STATUS(address)
    
    # First call - should detect all as new positions
    print("=== First Call ===")
    changes1 = tracker.track_positions(sample_data)
    print(changes1)
    tracker.print_changes(changes1)
    
    # Show current positions
    print("\n=== Current Positions ===")
    current = tracker.get_current_positions()
    for coin, pos in current.items():
        print(f"{coin}: {pos.size:,.4f} @ ${pos.entry_price:,.2f} (${pos.position_value:,.2f})")

if __name__ == "__main__":
    main()
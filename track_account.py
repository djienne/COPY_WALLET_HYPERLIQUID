"""Watch a Hyperliquid wallet's perp positions from the command line (read-only).

    python track_account.py <address>

Uses the same tracker as the strategy (user_data/strategies/copy_core.py). The snapshot
is kept in ./position_data/<address>/, separate from the bot's, so running this never
consumes the bot's change events. Run it repeatedly to see changes between runs.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "user_data" / "strategies"))
from copy_core import PositionTracker, fetch_user_state  # noqa: E402


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    address = sys.argv[1].strip().lower()

    tracker = PositionTracker(data_dir=str(Path("position_data") / address))
    state = fetch_user_state(address)
    equity = float(state["marginSummary"]["accountValue"])
    print(f"Account value: ${equity:,.2f}")
    tracker.print_changes(tracker.track_positions(state))

    print("\n=== Current Positions ===")
    for coin, pos in tracker.last_positions.items():
        share = f"{pos.position_value / equity * 100:6.2f}% of account" if equity > 0 else ""
        print(f"{coin}: {pos.size:,.4f} @ ${pos.entry_price:,.2f} (${pos.position_value:,.2f}) {share}")


if __name__ == "__main__":
    main()

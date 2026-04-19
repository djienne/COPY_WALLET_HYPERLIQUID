"""
Shared test harness. Imports the PositionTracker and PositionSnapshot/Change
dataclasses from the in-repo helper `track_account.py`, which is a pure Python
copy of the classes used by the freqtrade strategy (`user_data/strategies/COPY_HL.py`).

We import from `track_account.py` instead of the strategy file because the
strategy file has a `from freqtrade.strategy import IStrategy` at the top,
which pulls in the full freqtrade runtime and is not available in a plain
pytest environment.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

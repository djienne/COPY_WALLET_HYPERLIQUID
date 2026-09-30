"""
Shared test harness: puts user_data/strategies on sys.path so tests import the real
code (copy_core.py, and COPY_HL.py via the freqtrade stubs in test_strategy_glue.py).
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
STRATEGIES = os.path.join(ROOT, "user_data", "strategies")
for path in (ROOT, STRATEGIES):
    if path not in sys.path:
        sys.path.insert(0, path)

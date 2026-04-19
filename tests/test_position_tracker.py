"""
Unit tests for PositionTracker. Read-only, no network, no credentials.

We exercise the tracker that lives in `track_account.py` because importing
the strategy file pulls in the full freqtrade runtime. The tracker in
`track_account.py` is kept semantically equivalent to the one inside
`user_data/strategies/COPY_HL.py` — if they diverge, these tests will not
catch regressions in the strategy copy.
"""
import tempfile
import shutil

import pytest

from track_account import PositionTracker, PositionSnapshot


def _mk_payload(time_ms, *positions):
    """positions: iterable of (coin, szi, entry, value, upnl, lev, margin)."""
    return {
        "time": time_ms,
        "assetPositions": [
            {
                "type": "oneWay",
                "position": {
                    "coin": c,
                    "szi": str(sz),
                    "entryPx": str(ep),
                    "positionValue": str(pv),
                    "unrealizedPnl": str(upnl),
                    "leverage": {"type": "cross", "value": lev},
                    "marginUsed": str(mu),
                },
            }
            for (c, sz, ep, pv, upnl, lev, mu) in positions
        ],
        "marginSummary": {"accountValue": "1000.0"},
    }


@pytest.fixture
def tracker():
    tmp = tempfile.mkdtemp()
    try:
        yield PositionTracker(data_dir=tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_open_long(tracker):
    changes = tracker.track_positions(
        _mk_payload(1_700_000_000_000, ("BTC", 0.5, 60000, 30000, 0, 5, 6000))
    )
    assert len(changes) == 1
    assert changes[0].change_type == "opened_long"
    assert "BTC" in tracker.last_positions


def test_size_increase(tracker):
    tracker.track_positions(
        _mk_payload(1_700_000_000_000, ("BTC", 0.5, 60000, 30000, 0, 5, 6000))
    )
    changes = tracker.track_positions(
        _mk_payload(1_700_000_060_000, ("BTC", 0.7, 60000, 42000, 0, 5, 8400))
    )
    assert [c.change_type for c in changes] == ["increased"]


def test_size_decrease(tracker):
    tracker.track_positions(
        _mk_payload(1_700_000_000_000, ("BTC", 0.7, 60000, 42000, 0, 5, 8400))
    )
    changes = tracker.track_positions(
        _mk_payload(1_700_000_060_000, ("BTC", 0.2, 60000, 12000, 0, 5, 2400))
    )
    assert [c.change_type for c in changes] == ["decreased"]


def test_long_to_short_flip(tracker):
    tracker.track_positions(
        _mk_payload(1_700_000_000_000, ("BTC", 0.5, 60000, 30000, 0, 5, 6000))
    )
    changes = tracker.track_positions(
        _mk_payload(1_700_000_060_000, ("BTC", -0.1, 60000, 6000, 0, 5, 1200))
    )
    assert [c.change_type for c in changes] == ["flipped"]


def test_leverage_change_without_size_change(tracker):
    tracker.track_positions(
        _mk_payload(1_700_000_000_000, ("BTC", -0.1, 60000, 6000, 0, 5, 1200))
    )
    changes = tracker.track_positions(
        _mk_payload(1_700_000_060_000, ("BTC", -0.1, 60000, 6000, 0, 10, 1200))
    )
    assert [c.change_type for c in changes] == ["modified"]


def test_pure_pnl_delta_is_ignored(tracker):
    tracker.track_positions(
        _mk_payload(1_700_000_000_000, ("BTC", -0.1, 60000, 6000, 0, 10, 1200))
    )
    changes = tracker.track_positions(
        _mk_payload(1_700_000_060_000, ("BTC", -0.1, 60000, 6500, 500, 10, 1200))
    )
    assert changes == []


def test_close_event(tracker):
    tracker.track_positions(
        _mk_payload(1_700_000_000_000, ("BTC", -0.1, 60000, 6000, 0, 5, 1200))
    )
    changes = tracker.track_positions(_mk_payload(1_700_000_060_000))
    assert [c.change_type for c in changes] == ["closed"]


def test_zero_size_skipped_in_extraction(tracker):
    changes = tracker.track_positions(
        _mk_payload(1_700_000_000_000, ("ETH", 0, 3000, 0, 0, 5, 0))
    )
    assert all(c.coin != "ETH" for c in changes)


def test_csv_round_trip(tmp_path):
    t1 = PositionTracker(data_dir=str(tmp_path))
    t1.track_positions(
        _mk_payload(1_700_000_000_000, ("SOL", 10, 150, 1500, 0, 3, 500))
    )
    t2 = PositionTracker(data_dir=str(tmp_path))
    assert "SOL" in t2.last_positions
    assert t2.last_positions["SOL"].size == pytest.approx(10.0)


def test_numeric_leverage_handled(tracker):
    raw = {
        "time": 1_700_000_000_000,
        "assetPositions": [
            {
                "type": "oneWay",
                "position": {
                    "coin": "XRP",
                    "szi": "100",
                    "entryPx": "0.5",
                    "positionValue": "50",
                    "unrealizedPnl": "0",
                    "leverage": 7,
                    "marginUsed": "7",
                },
            }
        ],
    }
    extracted = tracker._extract_positions(raw)
    assert extracted["XRP"].leverage == 7.0


def test_determine_change_type_short_sizes(tracker):
    assert tracker._determine_change_type(-1.0, -2.0) == "increased"
    assert tracker._determine_change_type(-2.0, -1.0) == "decreased"


def test_close_all_uses_payload_time_not_now(tracker):
    """Regression guard for the timestamp-fallback bug.

    When `current_positions` is empty but there WAS a prior position,
    the change must carry the server payload's `time` value, not
    `datetime.now()`.
    """
    server_time_ms = 1_700_000_123_456  # arbitrary server clock
    tracker.last_positions = {
        "FOO": PositionSnapshot("FOO", 1, 1, 1, 0, 1, 1, 1_000_000_000_000)
    }
    changes = tracker._detect_changes({}, payload_time=server_time_ms)
    assert len(changes) == 1
    assert changes[0].change_type == "closed"
    assert changes[0].timestamp == server_time_ms

"""Portfolio equity is the sizing denominator, including for unified accounts."""
import time
from types import SimpleNamespace

import pytest

from copy_core import fetch_user_state


@pytest.fixture
def responses(monkeypatch):
    now = 1_790_800_000.0
    monkeypatch.setattr(time, 'time', lambda: now)
    data = {
        'clearinghouseState': {'assetPositions': [], 'marginSummary': {'accountValue': '0'}},
        'portfolio': [['day', {'accountValueHistory': [[now * 1000, '267000']]}]],
    }
    calls = []

    def post(url, *, json, timeout):
        calls.append(json)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data[json['type']])

    monkeypatch.setattr('copy_core.requests.post', post)
    return data, calls


def test_unified_equity_uses_portfolio_with_zero_perp_balance(responses):
    _, calls = responses
    state = fetch_user_state('0x' + 'ab' * 20)
    assert state['equity'] == 267000.0
    assert state['marginSummary']['accountValue'] == '0'
    assert [c['type'] for c in calls] == ['clearinghouseState', 'portfolio']


def test_stale_portfolio_equity_raises(responses):
    data, _ = responses
    data['portfolio'][0][1]['accountValueHistory'][-1][0] -= 301_000
    with pytest.raises(ValueError):
        fetch_user_state('0x' + 'ab' * 20)


def test_missing_day_equity_raises(responses):
    data, _ = responses
    data['portfolio'][0][0] = 'perpDay'
    with pytest.raises(ValueError):
        fetch_user_state('0x' + 'ab' * 20)


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-Infinity'])
def test_nonfinite_equity_raises(responses, value):
    data, _ = responses
    data['portfolio'][0][1]['accountValueHistory'][-1][1] = value
    with pytest.raises(ValueError):
        fetch_user_state('0x' + 'ab' * 20)


def test_missing_equity_point_raises(responses):
    data, _ = responses
    data['portfolio'][0][1]['accountValueHistory'] = []
    with pytest.raises(ValueError):
        fetch_user_state('0x' + 'ab' * 20)


def test_nonfinite_equity_timestamp_raises(responses):
    data, _ = responses
    data['portfolio'][0][1]['accountValueHistory'][-1][0] = float('nan')
    with pytest.raises(ValueError):
        fetch_user_state('0x' + 'ab' * 20)

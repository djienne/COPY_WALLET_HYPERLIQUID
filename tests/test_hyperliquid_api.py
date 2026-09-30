"""
Live-network checks against the public Hyperliquid Info endpoint (read-only, no credentials).

Skip them offline with:
    pytest -m "not network" tests/
"""
import json
import math
import os
import time
import urllib.error
import urllib.request

import pytest

from copy_core import HL_INFO_URL, fetch_user_state

pytestmark = pytest.mark.network

CONFIG = os.path.join(os.path.dirname(__file__), os.pardir, "user_data", "config.json")


def _post(payload, timeout=10.0):
    req = urllib.request.Request(HL_INFO_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        pytest.skip(f"Hyperliquid Info endpoint unreachable: {e}")


@pytest.fixture(autouse=True)
def pace_network_checks(monkeypatch):
    """Avoid bursts on the shared IP, including the two reads in fetch_user_state."""
    import requests
    post = requests.post

    def paced_post(*args, **kwargs):
        time.sleep(1.1)
        return post(*args, **kwargs)

    time.sleep(1.1)
    monkeypatch.setattr(requests, 'post', paced_post)


def _config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def test_whitelist_coins_are_listed_and_not_delisted():
    meta = _post({"type": "metaAndAssetCtxs"})
    universe = {u["name"]: u for u in meta[0]["universe"]}
    config = _config()
    pairs = config["exchange"]["pair_whitelist"]
    # This deployment uses the fleet's file-backed RemotePairList, not a static list.
    if not pairs:
        for pairlist in config.get('pairlists', []):
            url = pairlist.get('pairlist_url', '')
            if pairlist.get('method') == 'RemotePairList' and url.startswith('file:'):
                with urllib.request.urlopen(url) as source:
                    pairs = json.load(source)['pairs'][:pairlist['number_assets']]
                break
    assert pairs, 'No configured pairs were checked'
    wl = [p.split("/")[0] for p in pairs]
    missing = [c for c in wl if c not in universe]
    delisted = [c for c in wl if universe.get(c, {}).get("isDelisted")]
    assert not missing, f"Missing on Hyperliquid: {missing}"
    assert not delisted, f"Delisted on Hyperliquid: {delisted}"


def test_fetch_user_state_shape():
    """Any address returns a valid (possibly empty) state; check what the tracker relies on."""
    state = fetch_user_state("0x" + "0" * 40)
    assert "time" in state
    assert math.isfinite(state['equity'])
    for ap in state["assetPositions"]:
        assert ap["type"] == "oneWay"
        for key in ("coin", "szi", "entryPx", "positionValue", "unrealizedPnl", "leverage", "marginUsed"):
            assert key in ap["position"], key


def test_copied_wallet_is_funded():
    address = os.environ.get("FREQTRADE__COPY_ADDRESS") or _config().get("copy_address")
    if not address:
        pytest.skip("copy_address not configured")
    equity = fetch_user_state(address.lower())["equity"]
    assert equity > 0, f"Copied wallet {address} is empty: the bot would not trade"

"""
Live-network checks against the public Hyperliquid Info endpoint (read-only, no credentials).

Skip them offline with:
    pytest -m "not network" tests/
"""
import json
import os
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


def _config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def test_whitelist_coins_are_listed_and_not_delisted():
    meta = _post({"type": "metaAndAssetCtxs"})
    universe = {u["name"]: u for u in meta[0]["universe"]}
    wl = [p.split("/")[0] for p in _config()["exchange"]["pair_whitelist"]]
    missing = [c for c in wl if c not in universe]
    delisted = [c for c in wl if universe.get(c, {}).get("isDelisted")]
    assert not missing, f"Missing on Hyperliquid: {missing}"
    assert not delisted, f"Delisted on Hyperliquid: {delisted}"


def test_fetch_user_state_shape():
    """Any address returns a valid (possibly empty) state; check what the tracker relies on."""
    state = fetch_user_state("0x" + "0" * 40)
    assert "time" in state
    for ap in state["assetPositions"]:
        assert ap["type"] == "oneWay"
        for key in ("coin", "szi", "entryPx", "positionValue", "unrealizedPnl", "leverage", "marginUsed"):
            assert key in ap["position"], key


def test_copied_wallet_is_funded():
    address = os.environ.get("FREQTRADE__COPY_ADDRESS") or _config().get("copy_address")
    if not address:
        pytest.skip("copy_address not configured")
    equity = float(fetch_user_state(address.lower())["marginSummary"]["accountValue"])
    assert equity > 0, f"Copied wallet {address} is empty: the bot would not trade"

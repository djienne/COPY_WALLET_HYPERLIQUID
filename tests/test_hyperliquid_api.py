"""
Live-network smoke tests against the public Hyperliquid Info endpoint.

Marked with `network` so CI / offline runs can skip them with:
    pytest -m "not network" tests/

These hit only public, read-only endpoints — no credentials.
"""
import json
import urllib.request
import urllib.error

import pytest

URL = "https://api.hyperliquid.xyz/info"
HEADERS = {"Content-Type": "application/json"}
TRACKED_ADDRESS = "0x4b66f4048a0a90fd5ff44abbe5d68332656b78b8"

pytestmark = pytest.mark.network


def _post(payload, timeout=10.0):
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(), headers=HEADERS, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        pytest.skip(f"Hyperliquid Info endpoint unreachable: {e}")


def test_meta_and_asset_ctxs_shape():
    meta = _post({"type": "metaAndAssetCtxs"})
    assert isinstance(meta, list) and len(meta) == 2
    assert isinstance(meta[0].get("universe"), list)
    assert isinstance(meta[1], list)
    assert len(meta[1]) >= len(meta[0]["universe"])
    # At least BTC should exist and have markPx
    btc_idx = next(i for i, u in enumerate(meta[0]["universe"]) if u["name"] == "BTC")
    assert "markPx" in meta[1][btc_idx]


def test_whitelist_coins_are_on_hyperliquid():
    cfg = json.load(
        open("user_data/config.json", encoding="utf-8")
    )
    meta = _post({"type": "metaAndAssetCtxs"})
    universe = {u["name"] for u in meta[0]["universe"]}
    wl = [p.split("/")[0] for p in cfg["exchange"]["pair_whitelist"]]
    missing = [c for c in wl if c not in universe]
    assert not missing, f"Missing on Hyperliquid: {missing}"


def test_clearinghouse_state_shape():
    cs = _post({"type": "clearinghouseState", "user": TRACKED_ADDRESS})
    assert "marginSummary" in cs
    assert "accountValue" in cs["marginSummary"]
    assert "assetPositions" in cs
    assert "time" in cs
    # Confirm shape the strategy's tracker relies on:
    for ap in cs["assetPositions"]:
        assert ap["type"] == "oneWay"
        pos = ap["position"]
        for key in ("coin", "szi", "entryPx", "positionValue",
                    "unrealizedPnl", "leverage", "marginUsed"):
            assert key in pos, key

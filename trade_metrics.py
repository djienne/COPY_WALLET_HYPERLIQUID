#!/usr/bin/env python3
"""
Trade metrics calculator for a Freqtrade-like SQLite database (Hyperliquid futures).

Features:
- Closed-trade metrics: P&L, #trades, win rate, max drawdown
- Open-trade unrealized PnL via Hyperliquid mark price (Info endpoint: metaAndAssetCtxs)
- Combined P&L (closed + open)
- Reads available_capital from a config to report % gain and % drawdown baseline
- Defaults: --db ./user_data/tradesv3.sqlite, --config ./user_data/config.json
- Cross-platform paths, colored output (Windows/Linux), and rounding to 2 decimals

Docs for mark price: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals
"""
import argparse
import sqlite3
import pandas as pd
import numpy as np
import json
import sys
from pathlib import Path
from typing import Dict, Any

try:
    from colorama import init as _colorama_init
    _COLORAMA = True
except Exception:
    _COLORAMA = False

# HTTP (requests with urllib fallback)
try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore
import urllib.request, urllib.error, urllib.parse

# ANSI color helpers
_DEF_RESET = "\033[0m"
_COLORS = {
    "hdr":  "\033[96m",  # cyan
    "ok":   "\033[92m",  # green
    "warn": "\033[93m",  # yellow
    "err":  "\033[91m",  # red
    "dim":  "\033[90m",  # grey
}

def _supports_color() -> bool:
    return sys.stdout.isatty() or _COLORAMA

def _color(txt, key="hdr", use_color=True):
    if not use_color:
        return str(txt)
    c = _COLORS.get(key, "")
    return f"{c}{txt}{_DEF_RESET}"

def fetch_hl_mark_prices(timeout: float = 7.0) -> Dict[str, float]:
    """
    Fetch coin -> mark price using Hyperliquid public Info endpoint (metaAndAssetCtxs).
    """
    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "metaAndAssetCtxs"}
    headers = {"Content-Type": "application/json"}
    data = json.dumps(payload).encode("utf-8")
    try:
        if requests is not None:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)  # type: ignore
            resp.raise_for_status()
            js = resp.json()
        else:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8")
                js = json.loads(body)
    except Exception:
        return {}

    # Response shape: [ {universe:[{name:...}, ...]}, [ {markPx:...}, ...] ]
    try:
        universe = js[0]["universe"]
        ctxs = js[1]
        coin_to_mark: Dict[str, float] = {}
        for idx, u in enumerate(universe):
            name = u.get("name")
            if name is None:
                continue
            if idx < len(ctxs):
                ctx = ctxs[idx]
                mp = ctx.get("markPx")
                if mp is not None:
                    try:
                        coin_to_mark[name] = float(mp)
                    except Exception:
                        pass
        return coin_to_mark
    except Exception:
        return {}

def load_trades(conn, table):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table});")]
    select_cols = ["id", "is_open", "open_date", "close_date",
                   "close_profit_abs", "realized_profit",
                   "fee_open_cost", "fee_close_cost",
                   "funding_fees", "stake_amount", "amount",
                   "open_rate", "close_rate", "pair", "is_short", "funding_fee_running"]
    select_cols = [c for c in select_cols if c in cols]
    date_cols = [c for c in ["open_date", "close_date"] if c in select_cols]
    q = f"SELECT {', '.join(select_cols)} FROM {table} ORDER BY COALESCE(close_date, open_date) ASC"
    df = pd.read_sql_query(q, conn, parse_dates=date_cols)
    return df

def _base_coin_from_pair(pair: str) -> str:
    if not pair:
        return ""
    return pair.split("/")[0]

def compute_pnl_series(df):
    if "close_profit_abs" in df.columns and df["close_profit_abs"].notna().any():
        pnl = df["close_profit_abs"].fillna(0.0)
    elif "realized_profit" in df.columns and df["realized_profit"].notna().any():
        pnl = df["realized_profit"].fillna(0.0)
    else:
        if set(["open_rate", "close_rate", "amount"]).issubset(df.columns):
            pnl = (df["close_rate"] - df["open_rate"]) * df["amount"]
            for fee_col in ["fee_open_cost", "fee_close_cost", "funding_fees"]:
                if fee_col in df.columns:
                    pnl = pnl - df[fee_col].fillna(0.0)
        else:
            pnl = pd.Series([0.0] * len(df), index=df.index)
    return pnl

def compute_open_unrealized_pnl(df_open: pd.DataFrame, coin_to_mark: Dict[str, float]) -> pd.Series:
    if df_open.empty:
        return pd.Series([], dtype=float)

    coins = df_open["pair"].astype(str).map(_base_coin_from_pair)
    marks = coins.map(lambda c: coin_to_mark.get(c, float("nan")))
    open_rate = df_open["open_rate"].astype(float)
    amount = df_open["amount"].astype(float)
    is_short = df_open["is_short"].astype(int) if "is_short" in df_open.columns else 0

    price_diff = (marks - open_rate)
    long_pnl = price_diff * amount
    short_pnl = (-price_diff) * amount
    core = pd.Series(np.where(is_short == 1, short_pnl, long_pnl), index=df_open.index)

    fees = df_open["fee_open_cost"].fillna(0.0) if "fee_open_cost" in df_open.columns else 0.0
    funding = df_open["funding_fee_running"].fillna(0.0) if "funding_fee_running" in df_open.columns else 0.0

    core[marks.isna()] = np.nan
    pnl = core - (fees if isinstance(fees, pd.Series) else 0.0) + (funding if isinstance(funding, pd.Series) else 0.0)
    return pnl

def calc_metrics(df, start_equity=None):
    df_closed = df[df["is_open"] == 0].copy() if "is_open" in df.columns else df.copy()
    num_trades = int(len(df_closed))
    pnl = compute_pnl_series(df_closed)
    total_pnl = float(pnl.sum())

    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    winrate_pct = float(100 * wins / num_trades) if num_trades else None

    equity = pnl.cumsum()
    if start_equity is not None:
        equity = equity + float(start_equity)

    peaks = equity.cummax() if len(equity) else pd.Series(dtype=float)
    drawdowns = peaks - equity if len(equity) else pd.Series(dtype=float)
    max_dd_abs = float(drawdowns.max()) if len(drawdowns) else 0.0

    max_dd_pct = None
    if len(equity):
        denom = peaks.replace(0, np.nan)
        dd_pct = ((peaks - equity) / denom) * 100.0
        max_dd_pct = float(dd_pct.max()) if dd_pct.notna().any() else None

    return {
        "num_trades": num_trades,
        "total_pnl": total_pnl,
        "wins": wins,
        "losses": losses,
        "winrate_pct": winrate_pct,
        "max_drawdown_abs": max_dd_abs,
        "max_drawdown_pct": max_dd_pct
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=False, default="./user_data/tradesv3.sqlite",
                    help="Path to SQLite database (default: ./user_data/tradesv3.sqlite)")
    ap.add_argument("--table", default="trades", help="Table name (default: trades)")
    ap.add_argument("--start-equity", type=float, default=None,
                    help="Optional starting equity to compute percent drawdown")
    ap.add_argument("--config", type=str, default="./user_data/config.json",
                    help="Path to config.json (default: ./user_data/config.json); uses available_capital as starting equity for %% gain if present")
    ap.add_argument("--no-color", action="store_true", help="Disable colored output")
    args = ap.parse_args()

    if _COLORAMA:
        try:
            _colorama_init()
        except Exception:
            pass
    use_color = (not args.no_color) and _supports_color()

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        raise SystemExit(f"Database file not found: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        df = load_trades(conn, args.table)
    finally:
        conn.close()

    # Read available_capital from config if present
    available_capital = None
    if args.config:
        cfg_path = Path(args.config).expanduser()
        if cfg_path.exists():
            try:
                with open(cfg_path, "r") as fh:
                    cfg = json.load(fh)
                if isinstance(cfg, dict) and cfg.get("available_capital"):
                    available_capital = float(cfg["available_capital"])
                    if args.start_equity is None:
                        args.start_equity = available_capital
            except Exception:
                pass

    # Closed-only metrics
    metrics = calc_metrics(df, start_equity=args.start_equity)

    # Open positions and unrealized PnL
    df_open = df[df["is_open"] == 1].copy() if "is_open" in df.columns else pd.DataFrame()
    total_unrealized = None
    open_positions_valued = 0
    if not df_open.empty:
        price_map = fetch_hl_mark_prices()
        if price_map:
            open_unrealized = compute_open_unrealized_pnl(df_open, price_map)
            open_positions_valued = int(open_unrealized.notna().sum())
            total_unrealized = float(open_unrealized.fillna(0.0).sum())

    metrics["open_trades"] = int(len(df_open))
    metrics["open_trades_priced"] = int(open_positions_valued)
    metrics["unrealized_pnl_open"] = float(total_unrealized) if total_unrealized is not None else None
    metrics["total_pnl_including_open"] = (metrics["total_pnl"] + total_unrealized) if total_unrealized is not None else None

    # starting capital
    if available_capital is not None:
        metrics["starting_capital"] = float(available_capital)
    elif args.start_equity is not None:
        metrics["starting_capital"] = float(args.start_equity)
    else:
        metrics["starting_capital"] = None

    # Percent gain vs available capital
    if available_capital and available_capital != 0:
        metrics["pnl_pct_of_available_capital"] = (metrics["total_pnl"] / available_capital) * 100.0
        if metrics["total_pnl_including_open"] is not None:
            metrics["pnl_pct_total_including_open"] = (metrics["total_pnl_including_open"] / available_capital) * 100.0

    # Round floats to 2 decimals
    for k, v in list(metrics.items()):
        if isinstance(v, float):
            metrics[k] = round(v, 2)

    # Pretty print
    print(_color("=== Trade Metrics ===", "hdr", use_color))

    sc = metrics.get("starting_capital")
    if sc is not None:
        print(f"{_color('Starting capital:', 'warn', use_color)} {sc}")
    else:
        print(f"{_color('Starting capital:', 'warn', use_color)} n/a")

    pnl = metrics.get("total_pnl", 0.0)
    pnl_key = "ok" if pnl >= 0 else "err"
    print(f"{_color('Total P&L (closed):', pnl_key, use_color)} {pnl}")

    if metrics.get("unrealized_pnl_open") is not None:
        u = metrics["unrealized_pnl_open"]
        u_key = "ok" if (u or 0) >= 0 else "err"
        priced = metrics.get("open_trades_priced", 0)
        print(f"{_color('Unrealized P&L (open):', u_key, use_color)} {u}  "
              f"{_color('(priced/total open):', 'dim', use_color)} {priced}/{metrics.get('open_trades', 0)}")
        t = metrics.get("total_pnl_including_open")
        t_key = "ok" if (t or 0) >= 0 else "err"
        print(f"{_color('Total P&L (closed + open):', t_key, use_color)} {t}")
    else:
        print(f"{_color('Unrealized P&L (open):', 'warn', use_color)} n/a  "
              f"{_color('(API fetch failed or no open trades)', 'dim', use_color)}")

    if metrics.get("pnl_pct_of_available_capital") is not None:
        sign_key = "ok" if metrics["pnl_pct_of_available_capital"] >= 0 else "err"
        print(f"{_color('% gain vs starting capital (closed):', sign_key, use_color)} {metrics['pnl_pct_of_available_capital']}%")
        if metrics.get("pnl_pct_total_including_open") is not None:
            sign_key2 = "ok" if metrics['pnl_pct_total_including_open'] >= 0 else "err"
            print(f"{_color('% gain vs starting capital (closed + open):', sign_key2, use_color)} {metrics['pnl_pct_total_including_open']}%")

    print(f"{_color('Trades:', 'hdr', use_color)} {metrics.get('num_trades')}  "
          f"{_color('Wins:', 'ok', use_color)} {metrics.get('wins')}  "
          f"{_color('Losses:', 'err', use_color)} {metrics.get('losses')}  "
          f"{_color('Win rate:', 'hdr', use_color)} {metrics.get('winrate_pct')}%")

    md_abs = metrics.get("max_drawdown_abs")
    md_pct = metrics.get("max_drawdown_pct")
    md_pct_str = f"{md_pct}%" if md_pct is not None else "n/a"
    print(f"{_color('Max drawdown (abs):', 'err', use_color)} {md_abs}  "
          f"{_color('Max drawdown (%):', 'err', use_color)} {md_pct_str}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Show performance for Freqtrade containers whose *container name* contains a configurable keyword.
- Uses docker ps to get container names + host port -> 8080
- If no host port is published, the container is listed with metrics as N/A
- Auth: hyperliquid.env beside this script, with process environment overrides
- Binary good/bad colors + CAGR from profit_all% since first trade (open or closed)
- NEW: DAYS column = days since first trade
- Configurable keyword filter via CONTAINER_KEYWORD parameter
"""

import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Iterable
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

TIMEOUT = 3  # seconds
CONTAINER_KEYWORD = "COPY"  # Filter containers containing this keyword

PORT_RE = re.compile(r"(?:\d{1,3}(?:\.\d{1,3}){3}:)?(\d+)->8080/tcp")

# Windows terminals and redirected PowerShell output often use a legacy encoding
# (for example cp1252).  Decorative Unicode characters should never prevent the
# performance data from being printed.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

# ANSI Color codes
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    # Text colors
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    # Bright colors
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'

def colorize_profit(value: Optional[float]) -> str:
    if value is None:
        return f"{Colors.DIM}-{Colors.RESET}"
    pct_str = f"{value:.2f}%"
    return f"{Colors.BRIGHT_GREEN}{pct_str}{Colors.RESET}" if value > 0 else f"{Colors.BRIGHT_RED}{pct_str}{Colors.RESET}"

def colorize_win_rate(value: Optional[float]) -> str:
    if value is None:
        return f"{Colors.DIM}-{Colors.RESET}"
    pct_str = f"{value:.2f}%"
    return f"{Colors.BRIGHT_GREEN}{pct_str}{Colors.RESET}" if value >= 50 else f"{Colors.BRIGHT_RED}{pct_str}{Colors.RESET}"

def colorize_profit_factor(pf_str: str) -> str:
    if pf_str == "-":
        return f"{Colors.DIM}-{Colors.RESET}"
    try:
        pf_val = float(pf_str)
        return f"{Colors.BRIGHT_GREEN}{pf_str}{Colors.RESET}" if pf_val >= 1.0 else f"{Colors.BRIGHT_RED}{pf_str}{Colors.RESET}"
    except ValueError:
        return f"{Colors.DIM}{pf_str}{Colors.RESET}"

def colorize_cagr(value: Optional[float]) -> str:
    if value is None:
        return f"{Colors.DIM}-{Colors.RESET}"
    pct_str = f"{value:.2f}%"
    return f"{Colors.BRIGHT_GREEN}{pct_str}{Colors.RESET}" if value > 10 else f"{Colors.BRIGHT_RED}{pct_str}{Colors.RESET}"

def colorize_drawdown(value: Optional[float]) -> str:
    if value is None:
        return f"{Colors.DIM}-{Colors.RESET}"
    pct_str = f"{value:.2f}%"
    return f"{Colors.WHITE}{pct_str}{Colors.RESET}" if value == 0 else f"{Colors.BRIGHT_RED}{pct_str}{Colors.RESET}"

def colorize_trades(trades: Any) -> str:
    if trades == "-":
        return f"{Colors.DIM}-{Colors.RESET}"
    try:
        return f"{int(trades)}"
    except (ValueError, TypeError):
        return f"{Colors.DIM}{trades}{Colors.RESET}"

def colorize_container_name(name: str) -> str:
    return f"{Colors.BOLD}{Colors.BRIGHT_BLUE}{name}{Colors.RESET}"

def colorize_port(port: Any) -> str:
    if port == "-":
        return f"{Colors.DIM}-{Colors.RESET}"
    return f"{Colors.BRIGHT_MAGENTA}{port}{Colors.RESET}"

def colorize_strategy(strategy: str) -> str:
    if strategy == "-":
        return f"{Colors.DIM}-{Colors.RESET}"
    return f"{Colors.YELLOW}{strategy}{Colors.RESET}"

def colorize_bot_name(bot: str) -> str:
    if bot == "-":
        return f"{Colors.DIM}-{Colors.RESET}"
    return f"{Colors.CYAN}{bot}{Colors.RESET}"

def colorize_days(days: Optional[int]) -> str:
    if days is None:
        return f"{Colors.DIM}-{Colors.RESET}"
    return f"{days}"

# ---- Robust date parsing helpers ----

def try_parse_dt(val: Any) -> Optional[int]:
    """
    Parse various datetime representations commonly returned by Freqtrade.
    Returns milliseconds since epoch (UTC) or None.
    Accepts ISO strings (with/without 'Z'), epoch seconds/ms (int/float), or dicts with common keys.
    """
    if val is None:
        return None
    # numeric
    if isinstance(val, (int, float)):
        if val > 1e12:      # ms
            return int(val)
        if val > 1e9:       # s
            return int(val * 1000)
        return None
    # string
    if isinstance(val, str):
        s = val.strip()
        try:
            if s.isdigit():
                return try_parse_dt(int(s))
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)
        except Exception:
            return None
    # dict or other: try common keys
    if isinstance(val, dict):
        for k in ("open_date", "open_at", "open_time", "opened_at", "date_open", "date"):
            ts = try_parse_dt(val.get(k))
            if ts:
                return ts
    return None

def extract_first_ts_from_any(obj: Any, keys: Iterable[str]) -> Optional[int]:
    """
    Search dict OR list-of-dicts for the first parseable timestamp in given keys.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        for k in keys:
            ts = try_parse_dt(obj.get(k))
            if ts:
                return ts
        return None
    if isinstance(obj, list):
        for item in obj:
            ts = extract_first_ts_from_any(item, keys)
            if ts:
                return ts
        return None
    return None

def extract_earliest_open_ts_from_trades(trades_list: Iterable[Dict[str, Any]]) -> Optional[int]:
    candidates: List[int] = []
    for t in trades_list or []:
        for key in ("open_date", "open_at", "open_time", "opened_at", "date_open", "open_timestamp"):
            ts = try_parse_dt(t.get(key))
            if ts:
                candidates.append(ts)
                break
    return min(candidates) if candidates else None

# ---- IO helpers ----

def load_api_auth() -> HTTPBasicAuth:
    keys = ("FREQTRADE__API_SERVER__USERNAME", "FREQTRADE__API_SERVER__PASSWORD")
    values = {}
    env_file = Path(__file__).with_name("hyperliquid.env")
    if env_file.is_file():
        # The generated file contains literal KEY=value entries; keep '=' and '#'
        # inside credentials intact, and accept matching surrounding quotes.
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if separator and key in keys:
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key] = value
    username, password = (os.environ.get(key, values.get(key)) for key in keys)
    if not username or not password:
        raise ValueError("Set FREQTRADE__API_SERVER__USERNAME and FREQTRADE__API_SERVER__PASSWORD "
                         "in hyperliquid.env beside show_PnL.py or in the process environment.")
    return HTTPBasicAuth(username, password)


def docker_containers() -> List[Dict[str, Optional[int]]]:
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"], text=True
        )
    except Exception:
        return []
    rows: List[Dict[str, Optional[int]]] = []
    for line in out.splitlines():
        name, *port_parts = line.split("\t", 1)
        ports = port_parts[0] if port_parts else ""
        m = PORT_RE.search(ports or "")
        port = int(m.group(1)) if m else None
        rows.append({"name": name.strip(), "port": port})
    return rows

def get_json(url: str, auth: HTTPBasicAuth) -> Optional[Any]:
    try:
        r = requests.get(url, auth=auth, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None

def get_first_trade_timestamp(base: str, auth: HTTPBasicAuth, prof: Optional[Dict[str, Any]]) -> Optional[int]:
    """
    Robustly find the timestamp (ms) of the earliest trade (open or closed).
    Try, in order:
      1) /status (dict OR list) fields like 'first_trade_date' or 'first_trade_timestamp'
      2) hints on /profit
      3) /trades (any shape) → earliest open date across items
      4) /closed_trades then /open_trades (fallbacks)
    """
    # 1) /status
    status = get_json(f"{base}/status", auth)
    ts = extract_first_ts_from_any(status, ("first_trade_date", "first_trade_timestamp", "first_trade"))
    if ts:
        return ts

    # 2) /profit hints
    if isinstance(prof, dict):
        ts = extract_first_ts_from_any(prof, ("first_trade_date", "first_trade_timestamp"))
        if ts:
            return ts

    # 3) /trades
    for url in (
        f"{base}/trades?limit=5000",  # big net to find earliest
        f"{base}/trades",
    ):
        data = get_json(url, auth)
        if data:
            trades_list = (data.get("trades") or data.get("data") or data) if isinstance(data, (dict, list)) else []
            if not isinstance(trades_list, list):
                trades_list = []
            ts = extract_earliest_open_ts_from_trades(trades_list)
            if ts:
                return ts

    # 4) explicit closed/open endpoints
    for endpoint in ("closed_trades", "open_trades"):
        data = get_json(f"{base}/{endpoint}", auth)
        if data:
            items = (data.get(endpoint) or data.get("data") or data) if isinstance(data, (dict, list)) else []
            if not isinstance(items, list):
                items = []
            ts = extract_earliest_open_ts_from_trades(items)
            if ts:
                return ts

    return None

def calculate_cagr(profit_all_percent: Optional[float], first_trade_timestamp_ms: Optional[int]) -> Optional[float]:
    """
    CAGR from current PnL of ALL trades (profit_all_percent) and time since first trade.
    Annualizes even for short histories. Returns % or None if inputs invalid.
    """
    if profit_all_percent is None or first_trade_timestamp_ms is None:
        return None
    try:
        first_trade_date = datetime.fromtimestamp(first_trade_timestamp_ms / 1000, tz=timezone.utc)
        current_date = datetime.now(timezone.utc)
        elapsed_days = (current_date - first_trade_date).total_seconds() / 86400.0
        if elapsed_days <= 0:
            return None

        years_elapsed = elapsed_days / 365.25
        ending_value = 100.0 + float(profit_all_percent)
        if ending_value <= 0:
            return None  # nuked account case

        cagr = (pow(ending_value / 100.0, 1.0 / years_elapsed) - 1.0) * 100.0
        return cagr
    except Exception:
        return None

def days_since_first_trade(first_trade_timestamp_ms: Optional[int]) -> Optional[int]:
    if first_trade_timestamp_ms is None:
        return None
    try:
        first_trade_date = datetime.fromtimestamp(first_trade_timestamp_ms / 1000, tz=timezone.utc)
        current_date = datetime.now(timezone.utc)
        elapsed_days = (current_date - first_trade_date).total_seconds() / 86400.0
        if elapsed_days < 0:
            return None
        return int(elapsed_days)  # floor to whole days
    except Exception:
        return None

def pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{x:.2f}%"

def main() -> None:
    try:
        auth = load_api_auth()
    except (OSError, ValueError) as e:
        sys.exit(str(e))

    print(f"\n{Colors.BOLD}{Colors.BRIGHT_WHITE}Freqtrade Performance Monitor{Colors.RESET}")
    print(f"{Colors.DIM}Searching for containers with '{CONTAINER_KEYWORD}' in the name...{Colors.RESET}\n")

    conts = [c for c in docker_containers() if CONTAINER_KEYWORD.lower() in c["name"].lower()]
    if not conts:
        print(f"{Colors.BRIGHT_RED}No containers with '{CONTAINER_KEYWORD}' in the name were found.{Colors.RESET}")
        return

    print(f"{Colors.GREEN}Found {len(conts)} container(s) with '{CONTAINER_KEYWORD}' in the name{Colors.RESET}\n")

    rows: List[Dict[str, Any]] = []
    for c in conts:
        name, port = c["name"], c["port"]
        strategy = "-"
        bot_name = "-"

        if port is None:
            rows.append(
                {
                    "container": name,
                    "port": "-",
                    "bot": bot_name,
                    "strategy": strategy,
                    "trades": "-",
                    "win_rate": None,
                    "profit_all": None,
                    "profit_closed": None,
                    "pf": "-",
                    "max_dd": None,
                    "days": None,
                    "cagr": None,
                }
            )
            continue

        base = f"http://127.0.0.1:{port}/api/v1"
        cfg = get_json(f"{base}/show_config", auth) or {}
        bot_name = cfg.get("bot_name") or "-"
        strategy = cfg.get("strategy") or "-"

        prof = get_json(f"{base}/profit", auth)
        if not prof:
            rows.append(
                {
                    "container": name,
                    "port": port,
                    "bot": bot_name,
                    "strategy": strategy,
                    "trades": "-",
                    "win_rate": None,
                    "profit_all": None,
                    "profit_closed": None,
                    "pf": "-",
                    "max_dd": None,
                    "days": None,
                    "cagr": None,
                }
            )
            continue

        first_trade_ts = get_first_trade_timestamp(base, auth, prof)

        w = (prof.get("winning_trades") or 0) if isinstance(prof, dict) else 0
        l = (prof.get("losing_trades") or 0) if isinstance(prof, dict) else 0
        tc = (prof.get("trade_count") or 0) if isinstance(prof, dict) else 0
        closed = w + l
        win_rate = (w / closed * 100.0) if closed else None

        pf = (prof.get("profit_factor") if isinstance(prof, dict) else None)
        pf_str = "-" if pf is None else f"{pf:.2f}"

        mdd = (prof.get("max_drawdown") if isinstance(prof, dict) else None)
        mdd_pct = None if mdd is None else (mdd * 100 if isinstance(mdd, (int, float)) and abs(mdd) <= 1 else float(mdd))

        profit_all = (prof.get("profit_all_percent") if isinstance(prof, dict) else None)
        cagr = calculate_cagr(profit_all, first_trade_ts)
        days = days_since_first_trade(first_trade_ts)

        rows.append(
            {
                "container": name,
                "port": port,
                "bot": bot_name,
                "strategy": strategy,
                "trades": tc,
                "win_rate": win_rate,
                "profit_all": profit_all,
                "profit_closed": prof.get("profit_closed_percent") if isinstance(prof, dict) else None,
                "pf": pf_str,
                "max_dd": mdd_pct,
                "days": days,
                "cagr": cagr,
            }
        )

    headers = [
        ("CONTAINER", "container"),
        ("PORT", "port"),
        ("BOT", "bot"),
        ("STRATEGY", "strategy"),
        ("TRADES", "trades"),
        ("WIN RATE", "win_rate"),
        ("PROFIT ALL", "profit_all"),
        ("PROFIT CLOSED", "profit_closed"),
        ("PF", "pf"),
        ("MAX DD", "max_dd"),
        ("DAYS", "days"),
        ("CAGR", "cagr"),
    ]

    def cell(key: str, r: Dict[str, Any]) -> str:
        v = r.get(key)
        if key == "container":
            return colorize_container_name(str(v))
        if key == "port":
            return colorize_port(v)
        if key == "bot":
            return colorize_bot_name(str(v))
        if key == "strategy":
            return colorize_strategy(str(v))
        if key == "trades":
            return colorize_trades(v)
        if key == "win_rate":
            return colorize_win_rate(v)
        if key in ("profit_all", "profit_closed"):
            return colorize_profit(v)
        if key == "max_dd":
            return colorize_drawdown(v)
        if key == "pf":
            return colorize_profit_factor(str(v))
        if key == "days":
            return colorize_days(v)
        if key == "cagr":
            return colorize_cagr(v)
        return str(v)

    # strip ANSI to compute widths
    def plain_text_len(text: str) -> int:
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return len(ansi_escape.sub('', text))

    # widths
    col_w: Dict[str, int] = {}
    for title, key in headers:
        w = len(title)
        for r in rows:
            cell_content = cell(key, r)
            w = max(w, plain_text_len(cell_content))
        col_w[title] = w

    # header
    header_row = " | ".join(f"{Colors.BOLD}{Colors.WHITE}{t:<{col_w[t]}}{Colors.RESET}" for t, _ in headers)
    separator = "-+-".join("-" * col_w[t] for t, _ in headers)
    print(header_row)
    print(f"{Colors.DIM}{separator}{Colors.RESET}")

    # sort
    def sort_key(r: Dict[str, Any]):
        has_data = 0 if r["trades"] == "-" else 1
        pa = r.get("profit_all")
        return (has_data, pa if isinstance(pa, (int, float)) else float("-inf"), r.get("trades") or -1)

    rows.sort(key=sort_key, reverse=True)

    # rows
    for r in rows:
        row_cells = []
        for title, key in headers:
            cell_content = cell(key, r)
            padding = col_w[title] - plain_text_len(cell_content)
            row_cells.append(cell_content + " " * padding)
        print(" | ".join(row_cells))

    print(
        f"\n{Colors.DIM}Legend:{Colors.RESET} "
        f"{Colors.BRIGHT_GREEN}Good{Colors.RESET} | {Colors.BRIGHT_RED}Bad{Colors.RESET} | {Colors.DIM}No Data{Colors.RESET}"
    )
    print(
        f"{Colors.DIM}Rules: Profit: >0 good; Win Rate: >=50% good; PF: >=1.0 good; Max DD: >=-20% good; CAGR: >10% good.{Colors.RESET}\n"
    )

if __name__ == "__main__":
    main()

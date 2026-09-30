# Hyperliquid Copy Trading Strategy

A Freqtrade strategy that automatically copies trades from a Hyperliquid perpetual futures account to your Freqtrade bot.
More about Freqtrade: https://www.freqtrade.io/en/stable/

## Overview

This strategy monitors a specified Hyperliquid wallet address and replicates its long positions in your Freqtrade bot, scaled by account value: for every coin, `my position = copied position × my account value / copied account value`.

## Features

- **Real-time Position Tracking**: Monitors target Hyperliquid account for position changes
- **Smart Position Scaling**: Automatically scales position sizes based on account value ratios
- **Position Change Detection**: Detects opens, closes, increases, decreases, and modifications
- **Long-Only Trading**: **Only copies long positions** (ignores shorts for simplicity, and in general it reduces the long-term risk-reward ratio)
- **Comprehensive Logging**: Detailed position summaries and change tracking
- **Data Persistence**: Saves position history and changes to CSV files
- **State-based copying**: Every loop compares what the trader holds with what you hold, coin by coin, so a missed change event is simply picked up on the next loop
- **Copy-fidelity log**: One row per loop in `fidelity.csv` measuring how far your positions are from the scaled targets
- Trades the static whitelist in `config.json` (16 major Hyperliquid coins)

## Requirements

### Dependencies
`docker`, `docker compose`

## Configuration

### Main Strategy Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `FREQTRADE__COPY_ADDRESS` (in `hyperliquid.env`) or `copy_address` (in `config.json`) | empty | Hyperliquid wallet address to copy. The bot does not trade until it is set and the wallet has a non-zero account value. |
| `max_open_trades` (in `config.json`)| 4 | Maximum number of positions at a given time. Copied positions beyond this are not opened (they show up as "missing" in the fidelity log). |
| `LEV` (in `COPY_HL.py`) | 3 | Leverage per position. It sets how much **margin** each position uses, not how much you copy: the exposure is copied from the trader. Margin is isolated, so a lower `LEV` puts liquidation further away (about −26…−32 % price move at 3x, −11…−16 % at 6x, depending on the coin). It must still be at least the trader's total long exposure / account value; the bot logs a warning when the margin does not fit. |
| `change_threshold` (in `COPY_HL.py`)| 0.5 | Positions below this % of the copied account are not copied (exit only below half of it), and size changes below this % of your account are ignored |
| `adjustment_threshold` (in `COPY_HL.py`)| 10 | When the trader did not act, your position is only resized once it is more than this % away from its target |

Remark: `stake_amount` in `config.json` is ignored.

### Required Settings

1. **Create `hyperliquid.env`** from the template and fill it in (it is git-ignored):
```bash
cp hyperliquid.env.example hyperliquid.env
```
Set `FREQTRADE__COPY_ADDRESS` to the wallet you want to copy, and fresh values for the API password and JWT secret. You can check the positions of the copied wallet with e.g. https://apexliquid.bot/detail?address=<address>, or with `python track_account.py <address>`.

2. **Live trading only**: set `FREQTRADE__EXCHANGE__WALLET_ADDRESS` and `FREQTRADE__EXCHANGE__PRIVATE_KEY`. Use a Hyperliquid **API wallet** key (it can trade but cannot withdraw), never your main wallet's key, and never put keys in `config.json`.

⚠️ Earlier versions of this repository committed an API password and JWT secret to `config.json`. They are in the public git history: do not reuse them.

## How It Works

### Position Tracking
1. **API Polling**: Calls `fetch_user_state` once per loop: one `clearinghouseState` request and one `portfolio` request, each with a 10 s timeout. If either fetch fails or equity is stale, the bot does nothing that loop. The CLI also prints the account mode, perp balance, and portfolio equity.
2. **Change Detection**: Compares current positions with the previous snapshot (logged to CSV, and used to time re-entries and resizes)
3. **Signal Generation**: For each coin, compares the trader's holdings with yours: enter if the trader holds a significant long you don't have, exit if you hold something the trader no longer holds as a significant long
4. The shared code lives in `user_data/strategies/copy_core.py`; `track_account.py` is a command-line viewer using the same tracker.

### Position Scaling
- Scale factor: `My Account Value / Copied Portfolio Equity`. The copied denominator is the last `day.accountValueHistory` point from Hyperliquid's `portfolio` endpoint, in every account mode. It includes spot assets and avoids using an incomplete perp balance for unified accounts. No perp/spot balance formula is inferred.
- Missing or nonfinite portfolio values, points older than five minutes, and timestamps more than five seconds ahead of the local clock suppress copy actions for the loop. There is no fallback to the perp balance.
- Target size in coins: `copied size × scale factor`
- Only copies positions > 0.5 % of the copied account value

### Trade Management
- **Entry**: Opens a position sized to the target when the trader holds a significant long you don't have
- **Exit**: Closes positions the trader no longer holds (or holds short, or below 0.25 % of their account)
- **Adjustment**: Resizes towards the target when the trader resizes (tolerance 0.5 % of your account), or when drift exceeds 10 % of the target
- **After a stop-loss or liquidation**: Does not buy back until the trader changes that position again

## Position Types Detected

These are logged to `changes_log.csv`. Entries and exits follow the trader's holdings, not these events.

| Change Type | Description | Effect |
|-------------|-------------|---------|
| `opened_long` | New long position opened | Entry (via holdings comparison) |
| `closed` | Position closed | Exit (via holdings comparison) |
| `increased` | Position size increased | Resize with the smaller tolerance |
| `decreased` | Position size decreased | Resize with the smaller tolerance |
| `modified` | Leverage/entry price changed | None |
| `flipped` | Direction changed (long↔short) | Long→short: exit. Short→long: entry |

## File Structure

The strategy creates `user_data/strategies/position_data/<copied address>/` with:
- `positions_history.csv` - Position snapshots at each change
- `last_positions.csv` - Current position snapshot
- `changes_log.csv` - All detected changes
- `fidelity.csv` - One row per loop: your account value, the copied account value, `Σ|target − actual| / my account value`, number of missing and extra positions. This is the number to watch to see whether the copy works.

## Usage

1. **Install Dependencies**:
`Docker`

2. **Configure**: create `hyperliquid.env` (see Required Settings). Adjust `max_open_trades` (in `config.json`) and `LEV` (in `COPY_HL.py`) for the account to be copied.

3. **Run Freqtrade**:
```bash
docker compose up
```

4. **Tests** (no freqtrade install needed):
```bash
pip install pytest pandas requests
pytest -m "not network"   # offline
pytest -m network         # live read-only checks against Hyperliquid
```

## Safety Features

- **Long-Only**: Ignores shorts and closes any long the trader flipped to short
- **Fails closed**: On API failure, bad data, an unset address or an empty copied wallet, the bot does nothing (no entries, exits or resizes)
- **Size Limits**: Respects Freqtrade's min/max stake amounts
- **Margin check**: Warns when the copied exposure needs more margin than your account has at `LEV`
- **Threshold Protection**: Ignores insignificant positions or position changes

## Monitoring

### Console Output
Each loop logs:
- Detected position changes of the copied wallet
- Both account values and the scale factor
- Per coin: the trader's position (and its % of their account), your target and your actual position value
- A warning if the copied exposure needs more margin than you have at `LEV`
- The copy-fidelity line (also appended to `fidelity.csv`)

## Disclaimers

⚠️ **Important Warnings**:
- Very fresh and experimental, use with Dry-run only (paper trading)
- Monitor positions regularly (e.g. look in https://apexliquid.bot/)
- The copied trader's strategy may not be suitable for your risk tolerance and leverage level
- Past performance doesn't guarantee future results

## License

This strategy is provided as-is for educational purposes. Use at your own risk.

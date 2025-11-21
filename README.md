# Hyperliquid Copy Trading Strategy

A Freqtrade strategy that automatically copies trades from a Hyperliquid perpetual futures account to your Freqtrade bot.
More about Freqtrade: https://www.freqtrade.io/en/stable/

## Overview

This strategy monitors a specified Hyperliquid wallet address and replicates its trading positions in your Freqtrade bot with appropriate position sizing based on account value scaling, and an "effective" leverage asssumption.

## Features

- **Real-time Position Tracking**: Monitors target Hyperliquid account for position changes
- **Smart Position Scaling**: Automatically scales position sizes based on account value ratios
- **Position Change Detection**: Detects opens, closes, increases, decreases, and modifications
- **Long-Only Trading**: **Only copies long positions** (ignores shorts for simplicity, and in general it reduces the long-term risk-reward ratio)
- **Comprehensive Logging**: Detailed position summaries and change tracking
- **Data Persistence**: Saves position history and changes to CSV files
- **Missed Trade Recovery**: Detects and corrects missed entries/exits, or incorect sizing
- Can trade top ~50 Hyperliquid coins, sorted by market cap (ignores lower ranked), dynamic list, see full list here : https://remotepairlist.com?q=4885f6046bea5db4

## Requirements

### Dependencies
`docker`, `docker compose`

## Configuration

### Main Strategy Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_open_trades` (in `config.json`)| 4 | Maximum number of positions at a given time. Which value to use will depend on the account you copy. |
| `LEV` (in `COPY_HL.py`) | 6 | Effective leverage to use. Which value to use will depend on the account you copy, and it can be tricky to evaluate |
| `change_threshold` (in `COPY_HL.py`)| 0.5 | Minimum position size or position change threshold (% of account) |
| `ADDRESS_TO_TRACK_TOP` (in `COPY_HL.py`)| Set in code | Hyperliquid wallet address to copy |

Remark: `stake_amount` in `config.json` is ignored.

### Required Settings

1. **Set Target Address**: Update `ADDRESS_TO_TRACK_TOP` variable at the top of `COPY_HL.py` with the Hyperliquid wallet you want to copy, for example:
```python
ADDRESS_TO_TRACK_TOP = "0x4b66f4048a0a90fd5ff44abbe5d68332656b78b8"
```
You can also check the positions (verify if they are copied properly) of the wallet to copy with e.g. : https://apexliquid.bot/detail?address=0x4b66f4048a0a90fd5ff44abbe5d68332656b78b8

## How It Works

### Position Tracking
1. **API Polling**: Fetches position data from Hyperliquid (cached)
2. **Change Detection**: Compares current positions with previous snapshot
3. **Signal Generation**: Creates buy/sell signals based on detected changes
4. See `track_account.py` to test the account tracking class.

### Position Scaling
- Calculates scale factor: `My Account Value / Copied Account Value`
- Adjusts position sizes proportionally
- Only copies positions >0.5% of copied account value (absolute or changes)

### Trade Management
- **Entry**: Opens positions when target account opens new longs
- **Exit**: Closes positions when target account closes positions
- **Adjustment**: Increases/decreases positions to match target ratios
- **Safety**: Immediately exits any mistakenly opened Short or Long positions

## Position Types Detected

| Change Type | Description | Action |
|-------------|-------------|---------|
| `opened_long` | New long position opened | Enter long |
| `closed` | Position closed | Exit position |
| `increased` | Position size increased | Add to position |
| `decreased` | Position size decreased | Reduce position |
| `modified` | Leverage/entry price changed | None |
| `flipped` | Direction changed (long↔short) | None |

## File Structure

The strategy creates a `position_data/` directory with:
- `positions_history.csv` - Complete position history
- `last_positions.csv` - Current position snapshots  
- `changes_log.csv` - All detected changes

## Usage

1. **Install Dependencies**:
`Docker`

2. **Configure Strategy**:
```python
# In `COPY_HL.py`, set your target address
ADDRESS_TO_TRACK_TOP = "0x1234567890abcdef1234567890abcdef12345678"
```
Adjust `max_open_trades` (in `config.json`) and `LEV` (in `COPY_HL.py`) for the account to be copied.

3. **Run Freqtrade**:
```bash
docker compose build
docker compose up
```

## Safety Features

- **Long-Only**: Automatically ignores and closes any short positions
- **Size Limits**: Respects Freqtrade's min/max stake amounts
- **Error Handling**: Gracefully handles API failures and data issues
- **Position Validation**: Continuously validates position alignment
- **Threshold Protection**: Ignores insignificant positions or positions changes

## Monitoring

### Console Output
The strategy provides detailed logging including:
- Position change summaries
- Account value comparisons
- Scale factor calculations
- Position matching analysis
- Missing/extra position alerts

### Example Log
```
================================================================================
POSITIONS SUMMARY
================================================================================
Copied Account Value: $2,439,609.23
My Account Value:    $1,050.96
Scale Factor:        0.000431x (interted 2321.3x )
--------------------------------------------------
POSITIONS TO COPY:
       BTC |  LONG | Size:       0.0001 | Value: $     10.57 ( 0.00%) | Scaled: $      0.00
       ETH |  LONG | Size:       0.0001 | Value: $      0.44 ( 0.00%) | Scaled: $      0.00
      HYPE |  LONG | Size:   87720.0200 | Value: $4188630.96 (171.69%) | Scaled: $   1804.43
--------------------------------------------------
MY OPEN POSITIONS:
      HYPE | LONG  | Stake: $    304.92 | Value: $   1829.53 (174.08%) | Leverage: 6.0x
--------------------------------------------------
POSITION MATCHING ANALYSIS:
  Matching positions:
        HYPE | Copied: $4188630.96 -> Expected: $ 1804.43 | Actual: $ 1829.53 | Diff:    1.4%
  Missing positions (should open): None
================================================================================
```

## Disclaimers

⚠️ **Important Warnings**:
- Very fresh and experimental, use with Dry-run only (paper trading)
- Monitor positions regularly (e.g. look in  ttps://apexliquid.bot/)
- The copied trader's strategy may not be suitable for your risk tolerance and leverage level
- Past performance doesn't guarantee future results

## License

This strategy is provided as-is for educational purposes. Use at your own risk.

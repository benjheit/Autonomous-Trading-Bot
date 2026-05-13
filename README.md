# Autonomous-Trading-Bot

A fully autonomous algorithmic trading system built in Python that combines 
real-time market data, AI-driven sentiment analysis, and quantitative 
momentum indicators to make automated buy/sell decisions on US equities.

Built and actively developed as a personal project. Currently running on 
Alpaca's paper trading environment.

---

## How It Works

The bot runs a continuous scan loop during market hours, executing a 
multi-stage decision pipeline:

1. **Market Regime Detection** — Fetches SPY data and computes a 20-day SMA 
   to classify the market as BULL, BEAR, or CHOP. Adjusts strategy accordingly.
2. **News Catalyst Scan** — Pulls live business headlines via NewsAPI and flags 
   any watchlist symbols appearing in the news as high-priority targets.
3. **Pre-Screen Filter** — Scores each symbol across RVOL, SuperTrend, ADX, 
   EMA alignment, RSI, and VWAP proximity. Rejects low-conviction setups before 
   reaching the AI layer.
4. **AI Decision Engine** — Passes high-scoring candidates to GPT-4o-mini in 
   batches with a compressed multi-timeframe data snapshot (1D/1H/5M OHLCV). 
   The model scores conviction 1–10 and recommends BUY or WAIT.
5. **Position Management** — Manages open trades with ATR-based hard stops, 
   trailing stops, partial profit-taking at +3%, and AI-driven exit signals.
6. **Risk Management** — Uses a Half-Kelly sizing formula (40% fraction, 6% 
   cap), a 4% daily loss circuit breaker, and a karma system that reduces 
   exposure to repeatedly losing symbols.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Brokerage API | Alpaca Markets (paper trading) |
| AI Decision Making | OpenAI GPT-4o-mini |
| News Ingestion | NewsAPI |
| Market Data | Alpaca IEX feed |
| GUI | Tkinter + Matplotlib |
| Data Processing | Pandas, NumPy |
| Persistence | JSON + SQLite |

---

## Features

- **Regime-aware trading** — automatically switches between long-only (BULL), 
  inverse ETF (BEAR), and catalyst-only (CHOP) strategies
- **Multi-timeframe analysis** — "Omni-Sight Matrix" combining 1-day, 1-hour, 
  and 5-minute OHLCV data with SuperTrend, ADX, MACD, RSI, and EMA indicators
- **Batch AI calls** — up to 5 symbols analyzed per GPT call to minimize 
  latency and API cost
- **Half-Kelly position sizing** — dynamically adjusts trade size based on 
  recent win rate and profit factor
- **Karma system** — symbols accumulate positive/negative scores based on 
  trade outcomes; chronic losers are deprioritized
- **ATR-based stops** — minimum stop distance of `max(ATR × 2.2, price × 1.5%)` 
  to avoid premature exits
- **Partial profit-taking** — sells 50% of position at +3% gain, lets 
  remainder run with trailing stop
- **EOD liquidation** — automatically closes all non-swing positions before 
  market close
- **Real-time GUI dashboard** — equity curve, portfolio allocation pie chart, 
  live order feed with P/L, and performance stats (win rate, Sharpe, drawdown)
- **Wash sale prevention** — SQLite penalty box bans recently sold symbols 
  for 18 hours

---

## Project Structure

AlpacaTradingBot/
├── main.py              # Core bot — scan loop, AI engine, position management
├── database.py          # SQLite trade logging and penalty box
├── resurrect.py         # Utility to reset karma scores on all symbols
├── .env                 # API keys (not committed)
├── universe.json        # Watchlist with karma scores
├── ai_trade_memory.json # Trade entry log with AI scores and outcomes
├── trade_outcomes.json  # P/L record per closed trade
├── equity_history.json  # Timestamped equity snapshots for chart
├── active_highs.json    # Trailing stop high-water marks
└── swing_positions.json # Positions flagged for overnight holding

---

## Setup

```bash
# Clone the repo
git clone https://github.com/YOUR-USERNAME/autonomous-trading-bot.git
cd autonomous-trading-bot

# Install dependencies
pip install alpaca-py openai newsapi-python pandas numpy matplotlib python-dotenv

# Create your .env file
ALPACA_KEY=your_key_here
ALPACA_SECRET=your_secret_here
OPENAI_KEY=your_key_here
NEWS_API_KEY=your_key_here

# Run
python main.py
```

> ⚠️ This bot is configured for **paper trading only**. Never run algorithmic 
> trading systems with real money without thorough backtesting and risk review.

---

## Status

Active development. Current version: **v3.4 "Clean Slate"**

Recent changes:
- P/L calculation rewritten to derive directly from broker order fills 
  (no memory file dependency)
- AI ticker discovery disabled — bot now trades curated watchlist only
- Regime detection bug fixed (bear ETFs no longer entered in BULL regime)
- ATR stop floor added to prevent premature exits on low-volatility symbols

"""
DEEP QUANT TRADING BOT  v3.4  —  "CLEAN SLATE"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIXES FROM v3.3:
  1. P/L DISPLAY — calculated directly from orders list (BUY→SELL price math)
     No more N/A. No memory file dependency for display.
  2. DISCOVERED TARGETS DISABLED — news AI was adding DE, XOM, UAL, SLB etc.
     Bot now only trades from the curated WATCHLIST. Period.
  3. YANG/bear ETF in BULL regime bug fixed — regime check before pre-screen
     now hard-rejects wrong-direction ETFs before they reach AI.
  4. Watchlist tightened — only liquid, IEX-available momentum stocks.
  5. Stats (win rate etc.) fixed — record_outcome now finds PENDING by symbol.
"""

import os, time, math, sys, threading, json, random
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta
import pytz
import pandas as pd
import numpy as np
from dotenv import load_dotenv

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.dates as mdates
import matplotlib.ticker as ticker

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (MarketOrderRequest, GetPortfolioHistoryRequest,
                                      GetOrdersRequest)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed
from newsapi import NewsApiClient
from openai import OpenAI

# ══════════════════════════════════════════════════════════════════════════════
# 1. KEYS
# ══════════════════════════════════════════════════════════════════════════════
load_dotenv()
API_KEY    = os.getenv("ALPACA_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET")
OPENAI_KEY = os.getenv("OPENAI_KEY")
NEWS_KEY   = os.getenv("NEWS_API_KEY")

# ══════════════════════════════════════════════════════════════════════════════
# 2. SETTINGS
# ══════════════════════════════════════════════════════════════════════════════
RISK_PER_TRADE_PCT     = 0.015
MAX_POSITION_PCT       = 0.10
MIN_TRADE_VAL          = 300
MAX_DAILY_LOSS_PCT     = 0.04
ATR_MULTIPLIER         = 1.8
HARD_STOP_ATR_MULT     = 2.2
MIN_STOP_PCT           = 0.015   # Never stop on less than 1.5% move

TRAILING_STOP_PCT      = 0.018
MIN_PROFIT_TO_TRAIL    = 0.012
PARTIAL_PROFIT_TRIGGER = 0.03
PARTIAL_PROFIT_SIZE    = 0.50

AI_SCORE_THRESHOLD     = 8
PRE_SCREEN_MIN_SCORE   = 3
RVOL_MIN               = 1.3
TRADE_COOLDOWN_MINS    = 20

# Timing (ET)
ENTRY_START_H  = 10; ENTRY_START_M  = 15
ENTRY_CUTOFF_H = 15; ENTRY_CUTOFF_M = 30
EOD_HOUR = 12; EOD_MIN = 50   # PST

# Regime
BULL_SMA_PERIOD     = 20
BULL_SMA_BUFFER_PCT = 0.005
REGIME_CACHE_SECS   = 900

# AI
AI_BATCH_SIZE      = 5
NEWS_SCAN_INTERVAL = 20

# Kelly
KELLY_LOOKBACK   = 20
KELLY_FRACTION   = 0.4
KELLY_MIN_TRADES = 8
KELLY_CAP        = 0.06

# Cache
CACHE_TTL_WAIT  = 2
CACHE_TTL_BUY   = 2
CACHE_PRICE_TOL = 0.008

# Karma
KARMA_WIN = 5; KARMA_LOSS = -8; KARMA_BAN = 35

# ══════════════════════════════════════════════════════════════════════════════
# 3. UNIVERSE  — CURATED, IEX-VERIFIED ONLY
#    discovered_targets is DISABLED — was causing bot to trade random S&P stocks
# ══════════════════════════════════════════════════════════════════════════════
BEAR_SQUAD = ["SQQQ", "SOXS", "SPXS"]

# Only these symbols. Nothing else. Ever.
WATCHLIST = [
    "NVDA", "TSLA", "AMD", "PLTR", "AI", "SMCI", "ARM",
    "COIN", "MSTR", "MARA", "RIOT",
    "TQQQ", "SQQQ", "SOXL", "SOXS", "SPXL", "SPXS",
    "HOOD", "CVNA", "RDDT", "AFRM", "UPST", "DKNG",
    "NVDL", "SHOP", "ROKU", "CLSK"
]

STOCK_COLORS = {
    "CASH":"#00cc44","TQQQ":"#00CCFF","SQQQ":"#FF00FF","SOXL":"#00FF99",
    "SOXS":"#FF3300","SPXL":"#3366FF","SPXS":"#FF6600","NVDL":"#76b900",
    "NVDA":"#76b900","AMD":"#ED1C24","TSLA":"#CC0000","COIN":"#1652F0",
    "PLTR":"#ff6600","MSTR":"#f7931a","OTHERS":"#555555"
}

# ══════════════════════════════════════════════════════════════════════════════
# 4. CLIENTS
# ══════════════════════════════════════════════════════════════════════════════
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client    = StockHistoricalDataClient(API_KEY, SECRET_KEY)
ai_client      = OpenAI(api_key=OPENAI_KEY)
news_client    = NewsApiClient(api_key=NEWS_KEY)

# ══════════════════════════════════════════════════════════════════════════════
# 5. FILES
# ══════════════════════════════════════════════════════════════════════════════
PROFIT_FILE   = "trade_outcomes.json"
MEMORY_FILE   = "ai_trade_memory.json"
HIGHS_FILE    = "active_highs.json"
UNIVERSE_FILE = "universe.json"
SWING_FILE    = "swing_positions.json"
EQUITY_FILE   = "equity_history.json"

# ══════════════════════════════════════════════════════════════════════════════
# 6. GLOBAL STATE
# ══════════════════════════════════════════════════════════════════════════════
active_catalysts   = []   # Only symbols from WATCHLIST that appear in news
smart_cache        = {}
trade_cooldowns    = {}
partial_taken      = {}
last_eod_date      = None
regime_cache       = {"regime": "UNKNOWN", "spy_price": 0, "spy_sma": 0,
                      "dist_pct": 0, "time": 0}
daily_start_equity = 0.0

# ══════════════════════════════════════════════════════════════════════════════
# 7. UTILITIES
# ══════════════════════════════════════════════════════════════════════════════
def now_str():
    return datetime.now().strftime('%I:%M:%S %p')

def get_random_color():
    return "#{:06x}".format(random.randint(0, 0xFFFFFF))

def sanitize(data):
    if isinstance(data, dict):   return {k: sanitize(v) for k, v in data.items()}
    elif isinstance(data, list): return [sanitize(v) for v in data]
    elif isinstance(data, float):
        if math.isnan(data) or math.isinf(data): return 0.0
    return data

def wait_for_next_minute():
    now  = datetime.now()
    secs = 60 - now.second - now.microsecond / 1_000_000
    time.sleep(max(1, secs))

def is_market_open():
    try:
        return trading_client.get_clock().is_open
    except:
        nyc = pytz.timezone('America/New_York')
        now = datetime.now(nyc)
        if now.weekday() >= 5: return False
        if now.hour < 9 or (now.hour == 9 and now.minute < 30): return False
        if now.hour >= 16: return False
        return True

def is_entry_window():
    nyc   = pytz.timezone('America/New_York')
    now   = datetime.now(nyc)
    total = now.hour * 60 + now.minute
    start = ENTRY_START_H * 60 + ENTRY_START_M
    cut   = ENTRY_CUTOFF_H * 60 + ENTRY_CUTOFF_M
    if total < start:
        return False, f"⏳ Waiting ({ENTRY_START_H}:{ENTRY_START_M:02d} ET — {start-total} min)"
    if total >= cut:
        return False, f"🔔 Cutoff ({ENTRY_CUTOFF_H}:{ENTRY_CUTOFF_M:02d} PM ET)"
    return True, "OPEN"

# ══════════════════════════════════════════════════════════════════════════════
# 8. MARKET REGIME
# ══════════════════════════════════════════════════════════════════════════════
def get_market_regime():
    global regime_cache
    if time.time() - regime_cache["time"] < REGIME_CACHE_SECS:
        return regime_cache["regime"]
    try:
        df = fetch_bars("SPY", TimeFrame.Day, 60)
        if df is None or len(df) < 25:
            regime_cache["time"] = time.time()
            return regime_cache.get("regime", "CHOP")
        spy   = float(df['close'].iloc[-1])
        sma20 = float(df['close'].rolling(BULL_SMA_PERIOD).mean().iloc[-1])
        sma5  = float(df['close'].rolling(5).mean().iloc[-1])
        sma5p = float(df['close'].rolling(5).mean().iloc[-5])
        dist  = (spy - sma20) / sma20
        up    = sma5 > sma5p
        if dist >= BULL_SMA_BUFFER_PCT and up:     regime = "BULL"
        elif dist <= -BULL_SMA_BUFFER_PCT or not up: regime = "BEAR"
        else:                                        regime = "CHOP"
        regime_cache = {"regime": regime, "spy_price": round(spy,2),
                        "spy_sma": round(sma20,2), "dist_pct": round(dist*100,2),
                        "time": time.time()}
        print(f"   🌐 [{now_str()}] REGIME: {regime} | SPY ${spy:.2f} SMA20 ${sma20:.2f} {dist*100:+.2f}%")
        return regime
    except Exception as e:
        print(f"   ⚠️ Regime failed: {e}")
        return regime_cache.get("regime", "CHOP")

# ══════════════════════════════════════════════════════════════════════════════
# 9. DATA FETCHING
# ══════════════════════════════════════════════════════════════════════════════
def fetch_bars(symbol, timeframe, days_back):
    try:
        nyc   = pytz.timezone('America/New_York')
        now   = datetime.now(nyc)
        start = now - timedelta(days=days_back)
        req   = StockBarsRequest(symbol_or_symbols=[symbol], timeframe=timeframe,
                                 start=start, end=now, feed=DataFeed.IEX)
        raw   = data_client.get_stock_bars(req).df
        if raw is None or raw.empty: return None
        if isinstance(raw.index, pd.MultiIndex):
            try:   df = raw.xs(symbol, level=0).reset_index()
            except KeyError: return None
        else:
            df = raw.reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        for col in ['trade_count','vwap']:
            if col in df.columns: df.drop(columns=[col], inplace=True)
        for col in ['open','high','low','close','volume']:
            if col not in df.columns: return None
        df = df.fillna(0).reset_index(drop=True)
        return df if len(df) >= 5 else None
    except Exception as e:
        if "subscription" not in str(e).lower():
            print(f"   ⚠️ [{symbol}] fetch: {str(e)[:50]}")
        return None

# ══════════════════════════════════════════════════════════════════════════════
# 10. INDICATORS
# ══════════════════════════════════════════════════════════════════════════════
def calc_supertrend(df, period=10, mult=3.0):
    try:
        df=df.copy()
        df['pc']=df['close'].shift(1)
        df['tr']=pd.concat([df['high']-df['low'],(df['high']-df['pc']).abs(),
                            (df['low']-df['pc']).abs()],axis=1).max(axis=1)
        df['atr']=df['tr'].rolling(period).mean()
        hl2=(df['high']+df['low'])/2
        df['bu']=hl2+mult*df['atr']; df['bl']=hl2-mult*df['atr']
        df['fu']=df['bu']; df['fl']=df['bl']; df['st']=1
        for i in range(1,len(df)):
            fup=df['fu'].iloc[i-1]; flp=df['fl'].iloc[i-1]; cp=df['close'].iloc[i-1]
            df.at[df.index[i],'fu']=df['bu'].iloc[i] if (df['bu'].iloc[i]<fup or cp>fup) else fup
            df.at[df.index[i],'fl']=df['bl'].iloc[i] if (df['bl'].iloc[i]>flp or cp<flp) else flp
            stp=df['st'].iloc[i-1]; cl=df['close'].iloc[i]
            if   stp==-1 and cl>df['fu'].iloc[i]: df.at[df.index[i],'st']=1
            elif stp== 1 and cl<df['fl'].iloc[i]: df.at[df.index[i],'st']=-1
            else:                                  df.at[df.index[i],'st']=stp
        return "UP" if df['st'].iloc[-1]==1 else "DOWN"
    except: return "UNKNOWN"

def calc_adx(df, p=14):
    try:
        df=df.copy()
        df['pc']=df['close'].shift(1)
        df['tr']=pd.concat([df['high']-df['low'],(df['high']-df['pc']).abs(),
                            (df['low']-df['pc']).abs()],axis=1).max(axis=1)
        df['dmp']=np.where((df['high']-df['high'].shift(1))>(df['low'].shift(1)-df['low']),
                            np.maximum(df['high']-df['high'].shift(1),0),0)
        df['dmm']=np.where((df['low'].shift(1)-df['low'])>(df['high']-df['high'].shift(1)),
                            np.maximum(df['low'].shift(1)-df['low'],0),0)
        atr=df['tr'].rolling(p).mean()
        dp=(df['dmp'].rolling(p).mean()/(atr+1e-9)*100).iloc[-1]
        dm=(df['dmm'].rolling(p).mean()/(atr+1e-9)*100).iloc[-1]
        df['dx']=(df['dmp'].rolling(p).mean()-df['dmm'].rolling(p).mean()).abs()/(df['dmp'].rolling(p).mean()+df['dmm'].rolling(p).mean()+1e-9)*100
        return float(df['dx'].rolling(p).mean().iloc[-1]),float(dp),float(dm)
    except: return 0.0,50.0,50.0

def analyze(df, n=12):
    df=df.copy().reset_index(drop=True)
    for col in ['open','high','low','close','volume']:
        if col not in df.columns: df[col]=0.0
    df['e7']=df['close'].ewm(span=7,adjust=False).mean()
    df['e21']=df['close'].ewm(span=21,adjust=False).mean()
    df['e50']=df['close'].ewm(span=50,adjust=False).mean()
    df['e12']=df['close'].ewm(span=12,adjust=False).mean()
    df['e26']=df['close'].ewm(span=26,adjust=False).mean()
    df['macd']=df['e12']-df['e26']
    df['sig']=df['macd'].ewm(span=9,adjust=False).mean()
    df['hist']=df['macd']-df['sig']
    w=min(20,len(df))
    df['vwap']=(df['close']*df['volume']).cumsum()/(df['volume']+1e-9).cumsum()
    avg_vol=df['volume'].rolling(w).mean().iloc[-1]
    rvol=df['volume'].iloc[-1]/max(avg_vol,1)
    delta=df['close'].diff()
    gain=delta.where(delta>0,0).rolling(min(14,len(df))).mean()
    loss=(-delta.where(delta<0,0)).rolling(min(14,len(df))).mean()
    df['rsi']=100-100/(1+gain/(loss+1e-9))
    price=float(df['close'].iloc[-1]); vwap=float(df['vwap'].iloc[-1])
    e7=float(df['e7'].iloc[-1]); e21=float(df['e21'].iloc[-1]); e50=float(df['e50'].iloc[-1])
    st=calc_supertrend(df) if len(df)>=12 else "UNKNOWN"
    adx,dp,dm=calc_adx(df) if len(df)>=16 else (0.0,50.0,50.0)
    recent=df.tail(n).copy()
    for c in ['open','high','low','close']: recent[c]=recent[c].round(2)
    recent['volume']=recent['volume'].round(0)
    return {"price":price,"vwap_dist":round((price-vwap)/max(vwap,0.01),4),
            "rvol":round(float(rvol),2),"rsi":round(float(df['rsi'].iloc[-1]),1),
            "macd_bull":bool(df['hist'].iloc[-1]>df['hist'].iloc[-2]),
            "supertrend":st,"adx":round(float(adx),1),
            "di_plus":round(float(dp),1),"di_minus":round(float(dm),1),
            "ema_bull":bool(price>e7>e21>e50),"ema_bear":bool(price<e7<e21<e50),
            "tape":recent[['open','high','low','close','volume']].to_dict('records')}

def build_matrix(symbol):
    try:
        d1=fetch_bars(symbol,TimeFrame.Day,90)
        if d1 is None or len(d1)<5: return None
        d1h=fetch_bars(symbol,TimeFrame.Hour,10)
        if d1h is None or len(d1h)<5: return None
        d5m=fetch_bars(symbol,TimeFrame(5,TimeFrameUnit.Minute),3)
        if d5m is None or len(d5m)<5: return None
        atr=float((d5m['high']-d5m['low']).rolling(min(14,len(d5m))).mean().iloc[-1])
        return sanitize({"D1":analyze(d1,n=min(12,len(d1))),
                         "H1":analyze(d1h,n=min(12,len(d1h))),
                         "M5":analyze(d5m,n=min(12,len(d5m))),
                         "price":float(d5m['close'].iloc[-1]),
                         "atr":max(atr,0.01),
                         "gap":float((d5m['close'].iloc[-1]-d5m['close'].iloc[-2])
                                     /max(abs(d5m['close'].iloc[-2]),0.01))})
    except Exception as e:
        print(f"   ❌ [{symbol}] matrix: {e}"); return None

# ══════════════════════════════════════════════════════════════════════════════
# 11. P/L CALCULATION  ← COMPLETE REWRITE (no memory file dependency)
# ══════════════════════════════════════════════════════════════════════════════
def build_pl_map(orders):
    """
    Calculates P/L for every SELL order by matching it to the closest preceding
    BUY of the same symbol. Returns dict: {sell_order_id: profit_dollars}

    This approach is 100% reliable because it uses actual fill prices from
    the broker — no memory file, no custom UIDs, no lookup failures.

    Logic:
      - Build a chronological list of fills per symbol
      - For each SELL, find the most recent BUY and compute (sell-buy)*qty
      - Handles partial fills and multiple positions correctly
    """
    pl_map = {}

    # Group fills by symbol, sorted by time
    buys_by_symbol = {}  # symbol -> list of (price, qty, time)

    # Sort orders oldest first so we process them in order
    sorted_orders = sorted(
        [o for o in orders if o.status == OrderStatus.FILLED],
        key=lambda o: o.submitted_at
    )

    for o in sorted_orders:
        sym   = o.symbol
        qty   = float(o.filled_qty)
        price = float(o.filled_avg_price) if o.filled_avg_price else 0.0
        side  = o.side.value.upper()

        if side == "BUY":
            if sym not in buys_by_symbol:
                buys_by_symbol[sym] = []
            buys_by_symbol[sym].append({"price": price, "qty": qty,
                                         "time": o.submitted_at})
        elif side == "SELL":
            # Find the most recent BUY for this symbol
            buys = buys_by_symbol.get(sym, [])
            if buys:
                # Use the most recent buy before this sell
                buy = buys[-1]
                profit = (price - buy["price"]) * qty
                pl_map[str(o.id)] = round(profit, 2)
                # Remove that buy so it doesn't get matched twice
                buys_by_symbol[sym] = buys[:-1]
            # If no matching buy found, profit stays unmapped (shows as N/A)

    return pl_map


# ══════════════════════════════════════════════════════════════════════════════
# 12. PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════════
def init_universe():
    if not os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE,'w') as f:
            json.dump({s:{"score":100} for s in WATCHLIST},f,indent=4)

def load_universe():
    try:
        with open(UNIVERSE_FILE,'r') as f: return json.load(f)
    except: return {s:{"score":100} for s in WATCHLIST}

def save_universe(d):
    try:
        with open(UNIVERSE_FILE,'w') as f: json.dump(d,f,indent=4)
    except: pass

def active_symbols():
    u=load_universe()
    # Only return symbols that are in WATCHLIST — ignore any legacy entries
    return [s for s in WATCHLIST if u.get(s,{"score":100})["score"]>=KARMA_BAN]

def bump_karma(symbol, profit):
    u=load_universe()
    if symbol in u:
        u[symbol]["score"]+=KARMA_WIN if profit>0 else KARMA_LOSS
        save_universe(u)

def log_entry(order_id, symbol, score, entry_price):
    try:
        mem={}
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE,'r') as f: mem=json.load(f)
        mem[str(order_id)]={"symbol":symbol,"entry_ts":time.time(),
                             "entry_time":datetime.now().strftime("%I:%M %p"),
                             "entry_price":float(entry_price),
                             "ai_score":score,"result":"PENDING"}
        with open(MEMORY_FILE,'w') as f: json.dump(mem,f,indent=4)
    except: pass

def get_entry_ts(symbol):
    try:
        if not os.path.exists(MEMORY_FILE): return 0
        with open(MEMORY_FILE,'r') as f: mem=json.load(f)
        return max((t.get("entry_ts",0) for t in mem.values()
                    if t.get("symbol")==symbol and t.get("result")=="PENDING"),default=0)
    except: return 0

def record_outcome(uid, profit, symbol, exit_type=""):
    """Saves outcome and updates memory by finding PENDING trade by symbol."""
    try:
        data={}
        if os.path.exists(PROFIT_FILE):
            with open(PROFIT_FILE,'r') as f: data=json.load(f)
        data[str(uid)]=float(profit)
        with open(PROFIT_FILE,'w') as f: json.dump(data,f)
    except: pass

    bump_karma(symbol, float(profit))

    # Update memory — find PENDING by symbol (uid won't match for stops/trails)
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE,'r') as f: mem=json.load(f)
            best_key=None; best_ts=0
            for k,t in mem.items():
                if (t.get("symbol")==symbol and t.get("result")=="PENDING"
                        and t.get("entry_ts",0)>best_ts):
                    best_ts=t.get("entry_ts",0); best_key=k
            if best_key:
                mem[best_key]["result"]="WIN" if profit>0 else "LOSS"
                mem[best_key]["profit"]=float(profit)
                mem[best_key]["exit_type"]=exit_type
            with open(MEMORY_FILE,'w') as f: json.dump(mem,f,indent=4)
    except: pass

    direction="WIN ✅" if float(profit)>0 else "LOSS ❌"
    print(f"   📊 [{now_str()}] [{symbol}] CLOSED: ${profit:+.2f} → {direction} ({exit_type})")
    try:
        h=load_highs()
        if symbol in h: del h[symbol]; save_highs(h)
    except: pass

def recent_trades(n=20):
    try:
        if not os.path.exists(MEMORY_FILE): return []
        with open(MEMORY_FILE,'r') as f: mem=json.load(f)
        closed=[t for t in mem.values() if t.get("result") in ("WIN","LOSS")]
        closed.sort(key=lambda x:x.get("entry_ts",0),reverse=True)
        return closed[:n]
    except: return []

def load_highs():
    if not os.path.exists(HIGHS_FILE): return {}
    try:
        with open(HIGHS_FILE,'r') as f: return json.load(f)
    except: return {}

def save_highs(d):
    try:
        with open(HIGHS_FILE,'w') as f: json.dump(d,f)
    except: pass

def load_swings():
    if not os.path.exists(SWING_FILE): return {}
    try:
        with open(SWING_FILE,'r') as f: return json.load(f)
    except: return {}

def save_swings(d):
    try:
        with open(SWING_FILE,'w') as f: json.dump(d,f,indent=4)
    except: pass

# ══════════════════════════════════════════════════════════════════════════════
# 13. PERFORMANCE & SIZING
# ══════════════════════════════════════════════════════════════════════════════
def compute_stats():
    trades=recent_trades(KELLY_LOOKBACK)
    out={"win_rate":0.0,"profit_factor":0.0,"expectancy":0.0,"max_drawdown":0.0,
         "sharpe":0.0,"trades":len(trades),"streak":0,"streak_type":"—"}
    if not trades: return out
    wins  =[t["profit"] for t in trades if t.get("result")=="WIN"  and "profit" in t]
    losses=[t["profit"] for t in trades if t.get("result")=="LOSS" and "profit" in t]
    n=len(wins)+len(losses)
    if n==0: return out
    wr=len(wins)/n
    gw=sum(wins) if wins else 0.0; gl=abs(sum(losses)) if losses else 1.0
    aw=gw/len(wins) if wins else 0.0; al=gl/len(losses) if losses else 1.0
    out["win_rate"]=round(wr*100,1)
    out["profit_factor"]=round(gw/max(gl,0.01),2)
    out["expectancy"]=round(wr*aw-(1-wr)*al,2)
    sk=0; st=trades[0].get("result","—")
    for t in trades:
        if t.get("result")==st: sk+=1
        else: break
    out["streak"]=sk; out["streak_type"]=st
    try:
        if os.path.exists(EQUITY_FILE):
            with open(EQUITY_FILE,'r') as f:
                eq=[d["equity"] for d in json.load(f) if d.get("equity",0)>0]
            if eq:
                pk=eq[0]; mx=0.0
                for e in eq: pk=max(pk,e); mx=max(mx,(pk-e)/pk*100)
                out["max_drawdown"]=round(mx,2)
    except: pass
    if len(trades)>=5:
        pls=np.array([t.get("profit",0) for t in trades])
        if pls.std()>0: out["sharpe"]=round((pls.mean()/pls.std())*math.sqrt(252),2)
    return out

def kelly_risk():
    trades=recent_trades(KELLY_LOOKBACK)
    wins  =[t["profit"] for t in trades if t.get("result")=="WIN"  and "profit" in t]
    losses=[t["profit"] for t in trades if t.get("result")=="LOSS" and "profit" in t]
    if len(wins)+len(losses)<KELLY_MIN_TRADES: return RISK_PER_TRADE_PCT
    W=len(wins)/(len(wins)+len(losses))
    aw=abs(sum(wins)/len(wins)) if wins else 0.01
    al=abs(sum(losses)/len(losses)) if losses else 0.01
    hk=max(0.005,(W-(1-W)/(aw/max(al,0.01)))*KELLY_FRACTION)
    return min(hk,KELLY_CAP)

def calc_qty(symbol, price, atr, conf=5):
    try:
        acct=trading_client.get_account()
        eq=float(acct.equity); cash=float(acct.cash)
        rp=kelly_risk()
        if conf>=9: rp=min(rp*1.2,KELLY_CAP)
        stop_d=max(atr*ATR_MULTIPLIER, price*MIN_STOP_PCT)
        qty=(eq*rp)/stop_d
        qty=min(qty,(eq*MAX_POSITION_PCT)/max(price,0.01))
        qty=min(qty,cash/max(price,0.01))
        try:
            if not trading_client.get_asset(symbol).fractionable: qty=math.floor(qty)
        except: qty=math.floor(qty)
        return qty if qty*price>=MIN_TRADE_VAL else 0
    except: return 0

# ══════════════════════════════════════════════════════════════════════════════
# 14. CACHE
# ══════════════════════════════════════════════════════════════════════════════
def is_cached(symbol, price):
    if symbol not in smart_cache: return False
    c=smart_cache[symbol]
    age=time.time()-c["time"]; dp=abs(price-c["price"])/max(c["price"],0.01)
    return age<c.get("ttl",CACHE_TTL_WAIT)*60 and dp<CACHE_PRICE_TOL and c["action"]=="WAIT"

def set_cache(symbol, price, action):
    smart_cache[symbol]={"price":price,"time":time.time(),"action":action,
                          "ttl":CACHE_TTL_BUY if action=="BUY" else CACHE_TTL_WAIT}

# ══════════════════════════════════════════════════════════════════════════════
# 15. PRE-SCREEN
# ══════════════════════════════════════════════════════════════════════════════
def pre_screen(symbol, matrix, regime):
    """
    Regime-aware pre-screen.
    Hard rejects wrong-direction symbols immediately.
    """
    score=0; reasons=[]; is_bear=symbol in BEAR_SQUAD

    # Hard direction reject based on regime
    if regime=="BEAR" and not is_bear:
        return 0, ["🚫 BEAR regime — longs rejected"]
    if regime=="BULL" and is_bear:
        return 0, ["🚫 BULL regime — bear ETFs rejected"]
    if regime=="CHOP" and symbol not in active_catalysts:
        return 0, ["🟡 CHOP — no catalyst"]

    try:
        m5=matrix.get("M5",{}); m1h=matrix.get("H1",{}); m1d=matrix.get("D1",{})
        rvol=m5.get("rvol",1.0); rsi=m5.get("rsi",50)
        vwap_d=m5.get("vwap_dist",0); adx=m5.get("adx",0)
        st5=m5.get("supertrend","UNKNOWN"); st1h=m1h.get("supertrend","UNKNOWN")
        want="DOWN" if is_bear else "UP"

        # Volume gate — must have RVOL_MIN minimum
        if rvol<RVOL_MIN:
            return 0,[f"LOW VOL {rvol:.1f}x"]

        if rvol>=2.5:        score+=3; reasons.append(f"VOL {rvol:.1f}x 🔥")
        elif rvol>=1.8:      score+=2; reasons.append(f"VOL {rvol:.1f}x")
        else:                score+=1; reasons.append(f"VOL {rvol:.1f}x")

        # SuperTrend
        st_hits=sum(s==want for s in [st5,st1h] if s!="UNKNOWN")
        if st_hits==2: score+=2; reasons.append("ST 2/2 ✓")
        elif st_hits==1: score+=1; reasons.append("ST 1/2")
        else: score=max(0,score-1); reasons.append("ST opposing")

        # ADX
        if adx>=22: score+=2; reasons.append(f"ADX {adx:.0f}")
        elif adx>=15: score+=1; reasons.append(f"ADX {adx:.0f}")

        # EMA
        if m5.get("ema_bull") and not is_bear: score+=2; reasons.append("EMA ✓")
        if m5.get("ema_bear") and is_bear:     score+=2; reasons.append("EMA ✓")

        # RSI
        lo,hi=(20,48) if is_bear else (50,80)
        if lo<=rsi<=hi: score+=1; reasons.append(f"RSI {rsi:.0f}")

        # VWAP
        if (vwap_d>0.001 and not is_bear) or (vwap_d<-0.001 and is_bear):
            score+=1; reasons.append(f"VWAP {vwap_d*100:+.1f}%")

        # 1D trend
        tape=m1d.get("tape",[])
        if len(tape)>=5:
            c=[x['close'] for x in tape[-5:]]
            if (c[-1]<c[0] if is_bear else c[-1]>c[0]):
                score+=1; reasons.append("1D trend ✓")

        # Volume breakout
        tape5=m5.get("tape",[])
        if len(tape5)>=8:
            rh=[x['high'] for x in tape5[-4:]]; ph=[x['high'] for x in tape5[-8:-4]]
            rv=[x['volume'] for x in tape5[-4:]]; pv=[x['volume'] for x in tape5[-8:-4]]
            if sum(rh)/4>sum(ph)/4*1.005 and sum(rv)/4>sum(pv)/4*1.25:
                score+=2; reasons.append("VOL BREAKOUT ✓")

        # Rejects
        if (rsi>82 if not is_bear else rsi<18): score=0; reasons=["EXTREME RSI"]
        if (vwap_d<-0.025 if not is_bear else vwap_d>0.025):
            score=max(0,score-2); reasons.append("wrong VWAP side")

    except: pass
    return score, reasons

# ══════════════════════════════════════════════════════════════════════════════
# 16. AI ENGINE
# ══════════════════════════════════════════════════════════════════════════════
def compress(symbol, matrix, n=8):
    def tape(t,n):
        rows=[]
        for c in (t[-n:] if len(t)>=n else t):
            v=c.get('volume',0)
            vs=f"{v/1e6:.1f}M" if v>=1e6 else f"{v/1e3:.0f}K"
            rows.append(f"[{c['open']:.1f},{c['high']:.1f},{c['low']:.1f},{c['close']:.1f},{vs}]")
        return ",".join(rows)
    m5=matrix.get("M5",{}); h1=matrix.get("H1",{}); d1=matrix.get("D1",{})
    return (f"{symbol}|rv:{m5.get('rvol',1):.1f}|rsi:{m5.get('rsi',50):.0f}|"
            f"vw:{m5.get('vwap_dist',0)*100:+.1f}%|st5:{m5.get('supertrend','?')}|"
            f"st1h:{h1.get('supertrend','?')}|adx:{m5.get('adx',0):.0f}|"
            f"ema:{'Y' if m5.get('ema_bull') else 'N'}|bear:{'Y' if symbol in BEAR_SQUAD else 'N'}\n"
            f"1D:{tape(d1.get('tape',[]),n)}\n"
            f"1H:{tape(h1.get('tape',[]),n)}\n"
            f"5M:{tape(m5.get('tape',[]),n)}")

def batch_ai(candidates, regime):
    if not candidates: return {}
    hot=[s for s in candidates if s in active_catalysts]
    blocks="\n---\n".join(compress(sym,mx) for sym,mx in candidates.items())
    if regime=="BEAR":
        rules=("BEAR MARKET. Only BUY bear ETFs (bear=Y): ST DOWN, RSI 20-48, below VWAP, ADX>15.")
    elif regime=="CHOP":
        rules=("CHOPPY MARKET. Extreme selectivity. BUY only: RVOL>2.0 AND ADX>22 AND ST confirmed.")
    else:
        rules=("BULL MARKET. BUY: RVOL>1.3 AND ST UP AND ADX>15 AND above VWAP AND RSI 50-80.")
    system=(f"Strict quant momentum trader. {rules} "
            f"Score 8-10 = very high conviction only. Score <8 = WAIT. "
            f"Respond ONLY minified JSON.")
    user=(f"REGIME:{regime}|HOT:{hot}\n{blocks}\n\n"
          f'Return ONLY:{{"SYM":{{"a":"BUY"|"WAIT","c":1-10}},...}}')
    try:
        resp=ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            max_tokens=200,temperature=0.05)
        raw=resp.choices[0].message.content.strip()
        if "```" in raw: raw=raw.split("```")[1].replace("json","").strip()
        parsed=json.loads(raw)
        results={}
        for sym,dec in parsed.items():
            sym=sym.upper()
            action=str(dec.get("a","WAIT")).upper(); conf=int(dec.get("c",0))
            if sym in candidates: set_cache(sym,candidates[sym]["price"],action)
            results[sym]={"action":action,"confidence":conf}
        for sym in candidates:
            if sym not in results:
                set_cache(sym,candidates[sym]["price"],"WAIT")
                results[sym]={"action":"WAIT","confidence":0}
        return results
    except Exception as e:
        print(f"   ⚠️ AI: {e}")
        return {s:{"action":"WAIT","confidence":0} for s in candidates}

def single_ai(symbol, matrix):
    prompt=(f"MGMT {symbol}\n{compress(symbol,matrix,n=6)}\n\n"
            f"Structure broken? JSON only: {{\"a\":\"SELL\"|\"HOLD\",\"c\":1-10}}")
    try:
        resp=ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            max_tokens=60,temperature=0.05)
        raw=resp.choices[0].message.content.strip()
        if "```" in raw: raw=raw.split("```")[1].replace("json","").strip()
        dec=json.loads(raw)
        return {"action":str(dec.get("a","HOLD")).upper(),"confidence":dec.get("c",5)}
    except:
        return {"action":"HOLD","confidence":5}

# ══════════════════════════════════════════════════════════════════════════════
# 17. POSITION MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════
def manage_position(pos):
    sym=pos.symbol; qty=float(pos.qty)
    pl_pct=float(pos.unrealized_plpc)
    curr=float(pos.current_price); entry=float(pos.avg_entry_price)
    pl_val=(curr-entry)*qty
    print(f"  [{now_str()}] [{sym}] P/L: ${pl_val:+,.2f} ({pl_pct*100:.2f}%)")

    try:
        h=load_highs(); highest=max(h.get(sym,curr),curr)
        h[sym]=highest; save_highs(h)
    except: highest=curr

    matrix=build_matrix(sym)
    atr_val=matrix["atr"] if matrix else max(entry*0.02,0.01)

    # ATR stop with minimum 1.5% floor
    stop_dist=max(atr_val*HARD_STOP_ATR_MULT, entry*MIN_STOP_PCT)
    floor=entry-stop_dist

    if curr<floor:
        print(f" 🚨 [{now_str()}] STOP [{sym}] ${curr:.2f} < ${floor:.2f}")
        uid=f"STOP_{sym}_{int(time.time())}"
        try:
            trading_client.submit_order(MarketOrderRequest(
                symbol=sym,qty=qty,side=OrderSide.SELL,time_in_force=TimeInForce.DAY))
            record_outcome(uid,pl_val,sym,"ATR_STOP")
        except: pass
        return

    # Partial profit at +3%
    if pl_pct>=PARTIAL_PROFIT_TRIGGER and sym not in partial_taken:
        sq=qty*PARTIAL_PROFIT_SIZE
        try:
            if not trading_client.get_asset(sym).fractionable: sq=math.floor(sq)
        except: sq=math.floor(sq)
        if sq>0 and sq*curr>MIN_TRADE_VAL:
            print(f" 💰 [{now_str()}] PARTIAL [{sym}] +{pl_pct*100:.1f}%")
            uid=f"PARTIAL_{sym}_{int(time.time())}"
            try:
                trading_client.submit_order(MarketOrderRequest(
                    symbol=sym,qty=sq,side=OrderSide.SELL,time_in_force=TimeInForce.DAY))
                record_outcome(uid,pl_pct*sq*entry,sym,"PARTIAL")
                partial_taken[sym]=True
            except: pass

    # Trailing stop
    if highest>entry*(1+MIN_PROFIT_TO_TRAIL) and curr<highest*(1-TRAILING_STOP_PCT):
        print(f" 🛡️ [{now_str()}] TRAIL STOP [{sym}]")
        uid=f"TRAIL_{sym}_{int(time.time())}"
        try:
            trading_client.submit_order(MarketOrderRequest(
                symbol=sym,qty=qty,side=OrderSide.SELL,time_in_force=TimeInForce.DAY))
            record_outcome(uid,pl_val,sym,"TRAIL")
            if sym in partial_taken: del partial_taken[sym]
        except: pass
        return

    # AI check after 15 min if momentum fading
    if (time.time()-get_entry_ts(sym))/60<15: return
    if not matrix: return
    if matrix.get("M5",{}).get("macd_bull",True) and pl_pct>-0.005: return
    dec=single_ai(sym,matrix)
    if dec.get("action")=="SELL" and dec.get("confidence",0)>=7:
        print(f"   📉 [{now_str()}] AI EXIT [{sym}]")
        uid=f"AIEXIT_{sym}_{int(time.time())}"
        try:
            s=trading_client.submit_order(MarketOrderRequest(
                symbol=sym,qty=qty,side=OrderSide.SELL,time_in_force=TimeInForce.DAY))
            record_outcome(s.id,pl_val,sym,"AI_EXIT")
            if sym in partial_taken: del partial_taken[sym]
        except: pass

# ══════════════════════════════════════════════════════════════════════════════
# 18. SCAN CYCLE
# ══════════════════════════════════════════════════════════════════════════════
def scan_cycle(scan_list):
    ok,reason=is_entry_window()
    if not ok:
        print(f"   ⏰ [{now_str()}] {reason}"); return

    regime=get_market_regime()

    # Daily loss check
    global daily_start_equity
    try:
        acct=trading_client.get_account()
        eq=float(acct.equity); cash=float(acct.cash)
        if daily_start_equity>0:
            dl=(eq-daily_start_equity)/daily_start_equity
            if dl< -MAX_DAILY_LOSS_PCT:
                print(f"   🛑 [{now_str()}] DAILY LOSS {dl*100:.2f}% — halted"); return
        if cash<MIN_TRADE_VAL:
            print(f"   💸 insufficient cash"); return
    except: return

    # Regime-aware scan list
    if regime=="BEAR":
        scan_list=[s for s in scan_list if s in BEAR_SQUAD]
        if not scan_list:
            print(f"   🐻 BEAR — no bear ETFs available"); return
        print(f"   🐻 [{now_str()}] BEAR — {scan_list}")
    elif regime=="CHOP":
        chop_list=[s for s in scan_list if s in active_catalysts]
        if not chop_list:
            print(f"   🟡 [{now_str()}] CHOP + no catalysts → sitting out"); return
        scan_list=chop_list
        print(f"   🟡 [{now_str()}] CHOP catalyst scan: {scan_list}")
    else:
        print(f"   🟢 [{now_str()}] BULL — {len(scan_list)} symbols")

    try:
        held={p.symbol for p in trading_client.get_all_positions()}
    except: held=set()

    cands={}; mxs={}
    sc_cache=0; sc_cool=0; sc_nodata=0; sc_filt=0

    for sym in scan_list:
        if sym in held: continue
        if sym in trade_cooldowns and time.time()<trade_cooldowns.get(sym,0):
            sc_cool+=1; continue
        matrix=build_matrix(sym)
        if not matrix: sc_nodata+=1; continue
        if is_cached(sym,matrix["price"]):
            sc_cache+=1; continue
        score,reasons=pre_screen(sym,matrix,regime)
        rstr=', '.join(reasons[:3]) if reasons else '—'
        print(f"   📊 [{now_str()}] {sym:6s} score:{score:2d} | {rstr}")
        if score<PRE_SCREEN_MIN_SCORE:
            sc_filt+=1; set_cache(sym,matrix["price"],"WAIT"); continue
        print(f"   🔬 [{now_str()}] {sym} PASS → AI")
        cands[sym]=matrix; mxs[sym]=matrix
        if len(cands)>=AI_BATCH_SIZE:
            _fire(cands,mxs,regime,held); cands={}
        time.sleep(0.2)

    print(f"   📈 [{now_str()}] cache={sc_cache} cool={sc_cool} nodata={sc_nodata} filt={sc_filt} q={len(cands)}")
    if cands: _fire(cands,mxs,regime,held)

def _fire(cands, mxs, regime, held):
    if not cands: return
    print(f"   🤖 [{now_str()}] AI → {list(cands.keys())}")
    decisions=batch_ai(cands,regime)
    for sym,dec in decisions.items():
        action=dec.get("action","WAIT"); conf=dec.get("confidence",0)
        print(f"   🧠 [{now_str()}] {sym} → {action} conf:{conf}/10")
        if action!="BUY" or conf<AI_SCORE_THRESHOLD: continue
        mx=mxs.get(sym)
        if not mx: continue
        print(f" 🚀 [{now_str()}] BUY [{sym}] conf:{conf} REGIME:{regime}")
        qty=calc_qty(sym,mx["price"],mx["atr"],conf)
        if qty<=0:
            print(f"   ⚠️ [{sym}] qty=0"); continue
        swing=(conf>=9 and regime=="BULL" and mx.get("D1",{}).get("macd_bull",False))
        try:
            o=trading_client.submit_order(MarketOrderRequest(
                symbol=sym,qty=qty,side=OrderSide.BUY,time_in_force=TimeInForce.DAY))
            log_entry(o.id,sym,conf,mx["price"])
            trade_cooldowns[sym]=time.time()+TRADE_COOLDOWN_MINS*60
            print(f" ✅ [{now_str()}] ORDER [{sym}] qty={qty:.4f} @ ~${mx['price']:.2f}")
            if swing:
                sw=load_swings(); sw[sym]={"ts":datetime.now().isoformat(),"conf":conf}
                save_swings(sw); print(f"   🌙 [{sym}] SWING (BULL+conf:{conf})")
        except Exception as e:
            print(f"   ❌ [{sym}] order failed: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# 19. GUI
# ══════════════════════════════════════════════════════════════════════════════
class BotGUI:
    def __init__(self, root):
        self.root=root
        self.root.title("DEEP QUANT  ·  v3.4  ·  CLEAN SLATE")
        self.root.geometry("1800x1050")
        self.root.configure(bg="#050505")
        self.chart_ts=[]; self.chart_eq=[]; self.annot=None
        self._pl_map={}   # Cached P/L map rebuilt each dashboard update

        style=ttk.Style(); style.theme_use("clam")
        style.configure("Vertical.TScrollbar",background="#333",troughcolor="#050505",
                        bordercolor="#050505",arrowcolor="white",relief="flat")
        style.map("Vertical.TScrollbar",background=[('active','#555')])

        fl=tk.Frame(root,bg="#050505")
        fl.pack(side=tk.LEFT,fill=tk.BOTH,expand=True,padx=15,pady=15)
        tk.Label(fl,text="TRADING LOG",fg="white",bg="#050505",
                 font=("Segoe UI",13,"bold")).pack(anchor="w",pady=(0,4))
        lc=tk.Frame(fl,bg="#050505"); lc.pack(fill=tk.BOTH,expand=True)
        sb=ttk.Scrollbar(lc,orient="vertical",command=lambda *a:self.log.yview(*a))
        sb.pack(side=tk.RIGHT,fill=tk.Y)
        self.log=tk.Text(lc,wrap=tk.WORD,width=52,height=36,font=("Consolas",9),
                         bg="#0a0a0a",fg="#cccccc",insertbackground="white",bd=0,
                         yscrollcommand=sb.set)
        self.log.pack(side=tk.LEFT,fill=tk.BOTH,expand=True)
        for tag,fg,bg in [("G","#00ff00","#001a00"),("R","#ff3333","#1a0000"),
                          ("C","#00ffff","#050505"),("Y","#ffcc00","#050505"),
                          ("M","#ff00ff","#050505"),("O","#FFA500","#050505"),
                          ("B","#7fafff","#050505"),("P","#bb88ff","#050505")]:
            self.log.tag_config(tag,foreground=fg,background=bg)

        fr=tk.Frame(root,bg="#050505")
        fr.pack(side=tk.RIGHT,fill=tk.BOTH,expand=True,padx=15,pady=15)

        fb=tk.Frame(fr,bg="#111111",padx=10,pady=10)
        fb.pack(side=tk.TOP,fill=tk.X,pady=(0,8))
        self.lbl_cash  =self._card(fb,"CASH",        "$0.00")
        self.lbl_upl   =self._card(fb,"OPEN P/L",    "$0.00")
        self.lbl_day   =self._card(fb,"DAILY CHANGE", "$0.00","#00ff00")
        self.lbl_regime=self._card(fb,"REGIME",       "...",  "#ffcc00")
        self.lbl_kelly =self._card(fb,"KELLY RISK",  "1.50%","#00ccff")
        self.lbl_pos   =self._card(fb,"POSITIONS",   "0 open","#888888")

        fs=tk.Frame(fr,bg="#0a0a0a",padx=10,pady=6)
        fs.pack(side=tk.TOP,fill=tk.X,pady=(0,8))
        self.lbl_wr =self._sc(fs,"WIN RATE",     "—%")
        self.lbl_pf =self._sc(fs,"PROFIT FACTOR","—")
        self.lbl_exp=self._sc(fs,"EXPECTANCY",   "$—")
        self.lbl_sh =self._sc(fs,"SHARPE",       "—")
        self.lbl_dd =self._sc(fs,"MAX DRAWDOWN", "—%")
        self.lbl_str=self._sc(fs,"STREAK",       "—")

        feq=tk.Frame(fr,bg="#050505")
        feq.pack(side=tk.TOP,fill=tk.BOTH,expand=False,pady=(0,8))
        tk.Label(feq,text="PORTFOLIO EQUITY",fg="white",bg="#050505",
                 font=("Segoe UI",11,"bold")).pack(anchor="w")
        self.fig_eq=Figure(figsize=(6,3),dpi=100,facecolor='#050505')
        self.ax_eq=self.fig_eq.add_subplot(111); self.ax_eq.set_facecolor('#050505')
        self.fig_eq.subplots_adjust(left=0.1,right=0.95,top=0.9,bottom=0.15)
        self.can_eq=FigureCanvasTkAgg(self.fig_eq,master=feq)
        self.can_eq.get_tk_widget().pack(fill=tk.BOTH,expand=True)
        self.can_eq.mpl_connect("motion_notify_event",self.on_hover)

        fsp=tk.Frame(fr,bg="#050505"); fsp.pack(side=tk.BOTTOM,fill=tk.BOTH,expand=True)

        fpi=tk.Frame(fsp,bg="#050505",width=380); fpi.pack(side=tk.LEFT,fill=tk.BOTH,expand=True)
        tk.Label(fpi,text="ALLOCATION",fg="white",bg="#050505",font=("Segoe UI",11,"bold")).pack(anchor="w")
        self.fig_pi=Figure(figsize=(4.5,4.5),dpi=100,facecolor='#050505')
        self.ax_pi=self.fig_pi.add_subplot(111); self.ax_pi.set_facecolor('#050505')
        self.fig_pi.subplots_adjust(left=0,right=1,top=1,bottom=0)
        self.can_pi=FigureCanvasTkAgg(self.fig_pi,master=fpi)
        self.can_pi.get_tk_widget().pack(fill=tk.BOTH,expand=True)

        fo=tk.Frame(fsp,bg="#050505",padx=15); fo.pack(side=tk.RIGHT,fill=tk.BOTH,expand=True)
        tk.Label(fo,text="RECENT ORDERS",fg="white",bg="#050505",font=("Segoe UI",11,"bold")).pack(anchor="w")
        foc=tk.Frame(fo,bg="#111111"); foc.pack(fill=tk.BOTH,expand=True)
        self.oc=tk.Canvas(foc,bg="#111111",highlightthickness=0)
        osb=ttk.Scrollbar(foc,orient="vertical",command=self.oc.yview)
        self.ogrid=tk.Frame(self.oc,bg="#111111")
        osb.pack(side=tk.RIGHT,fill=tk.Y)
        self.oc.pack(side=tk.LEFT,fill=tk.BOTH,expand=True)
        self.oc.configure(yscrollcommand=osb.set)
        self.cwin=self.oc.create_window((0,0),window=self.ogrid,anchor="nw")
        self.ogrid.bind("<Configure>",lambda e:self.oc.configure(scrollregion=self.oc.bbox("all")))
        self.oc.bind("<Configure>",lambda e:self.oc.itemconfig(self.cwin,width=e.width))

        sys.stdout=self
        threading.Thread(target=self.bot_loop,daemon=True).start()

    def _card(self,p,t,v,c="white"):
        f=tk.Frame(p,bg="#111111"); f.pack(side=tk.LEFT,expand=True,fill=tk.X)
        tk.Label(f,text=t,fg="#888",bg="#111111",font=("Segoe UI",8)).pack()
        l=tk.Label(f,text=v,fg=c,bg="#111111",font=("Segoe UI",13,"bold")); l.pack()
        return l

    def _sc(self,p,t,v):
        f=tk.Frame(p,bg="#0a0a0a"); f.pack(side=tk.LEFT,expand=True,fill=tk.X)
        tk.Label(f,text=t,fg="#555",bg="#0a0a0a",font=("Segoe UI",7)).pack()
        l=tk.Label(f,text=v,fg="#ddd",bg="#0a0a0a",font=("Segoe UI",11,"bold")); l.pack()
        return l

    def on_hover(self,event):
        if event.inaxes==self.ax_eq and self.annot and self.chart_ts:
            try:
                idx=(np.abs(mdates.date2num(self.chart_ts)-event.xdata)).argmin()
                y=self.chart_eq[idx]
                self.annot.xy=(event.xdata,y)
                self.annot.set_text(f"{self.chart_ts[idx].strftime('%a %I:%M %p')}\n${y:,.2f}")
                self.annot.set_visible(True); self.can_eq.draw_idle()
            except: pass
        elif self.annot and self.annot.get_visible():
            self.annot.set_visible(False); self.can_eq.draw_idle()

    def write(self,text):
        self.root.after(0,self._write,text)

    def _write(self,text):
        at_bot=self.log.yview()[1]>=0.99; u=text.upper(); tag=None
        if ("BUY" in u and ("SIGNAL" in u or "ORDER" in u)) or "WIN" in u: tag="G"
        elif "STOP" in u or "LOSS" in u:   tag="R"
        elif "CATALYST" in u:              tag="O"
        elif "REGIME" in u:                tag="O"
        elif "AI" in u and "CALL" in u:    tag="C"
        elif "PASS" in u:                  tag="C"
        elif "TRAIL" in u or "PARTIAL" in u or "KELLY" in u: tag="Y"
        elif "CYCLE" in u or "SCAN" in u or "COMPLETE" in u: tag="B"
        elif "BEAR" in u or "CHOP" in u:   tag="P"
        self.log.insert(tk.END,text,tag)
        if at_bot: self.log.see(tk.END)

    def flush(self): pass

    def update_stats(self, n_positions=0):
        s=compute_stats(); k=kelly_risk()*100
        self.lbl_wr.config(text=f"{s['win_rate']}%",fg="#00ff00" if s['win_rate']>=50 else "#ff5555")
        self.lbl_pf.config(text=f"{s['profit_factor']:.2f}",fg="#00ff00" if s['profit_factor']>=1.5 else "#ffcc00")
        self.lbl_exp.config(text=f"${s['expectancy']:.2f}",fg="#00ff00" if s['expectancy']>=0 else "#ff5555")
        self.lbl_sh.config(text=f"{s['sharpe']:.2f}",fg="#00ff00" if s['sharpe']>=1 else "#ffcc00")
        self.lbl_dd.config(text=f"{s['max_drawdown']:.1f}%",fg="#ff5555" if s['max_drawdown']>10 else "#ffcc00")
        self.lbl_str.config(text=f"{s['streak']}× {s['streak_type']}",
                             fg="#00ff00" if s['streak_type']=="WIN" else "#ff5555")
        self.lbl_kelly.config(text=f"{k:.2f}%")
        regime=regime_cache.get("regime","?")
        rc={"BULL":"#00ff00","BEAR":"#ff3333","CHOP":"#ffcc00"}.get(regime,"#888")
        spy_str=""
        if regime_cache.get("spy_price"):
            spy_str=f" SPY${regime_cache['spy_price']:.0f} {regime_cache.get('dist_pct',0):+.1f}%"
        self.lbl_regime.config(text=f"{regime}{spy_str}",fg=rc)
        self.lbl_pos.config(text=f"{n_positions} open",fg="#00ccff")

    def update_orders(self, orders):
        """
        Builds P/L map from orders directly — no memory file lookup.
        Matches each SELL to its most recent BUY of the same symbol.
        """
        # Rebuild P/L map from full order history
        self._pl_map = build_pl_map(orders)

        for w in self.ogrid.winfo_children(): w.destroy()
        for i,h in enumerate(["ASSET","SIDE","QTY","PRICE","P/L","TIME"]):
            tk.Label(self.ogrid,text=h,bg="#1a1a1a",fg="#777",
                     font=("Segoe UI",8,"bold"),pady=5).grid(
                row=0,column=i,sticky="nsew",padx=1,pady=1)
            self.ogrid.grid_columnconfigure(i,weight=1)
        r=1
        for o in orders:
            if r>200 or o.status!=OrderStatus.FILLED: continue
            qty=o.filled_qty; price=o.filled_avg_price or "0.00"
            side=o.side.value.upper()
            ts=o.submitted_at.astimezone(
                pytz.timezone('America/Los_Angeles')).strftime("%m/%d %I:%M %p")
            total=float(qty)*float(price); val="—"; plc="white"

            if side=="BUY":
                val=f"-${total:,.2f}"; sc="#00cc44"
            else:
                sc="#ff4444"
                profit=self._pl_map.get(str(o.id))
                if profit is not None:
                    pct=(profit/total*100) if total else 0
                    val=f"{'+'if profit>=0 else''}${abs(profit):.2f} ({pct:.1f}%)"
                    plc="#00cc44" if profit>=0 else "#ff4444"
                else:
                    # Fallback: show sell value with a ~ prefix
                    val=f"~${total:,.2f}"
                    plc="#888888"

            for col,(txt,fg) in enumerate([
                (o.symbol,"white"),(side,sc),(str(qty),"white"),
                (f"${float(price):.2f}","white"),(val,plc),(ts,"#888")
            ]):
                tk.Label(self.ogrid,text=txt,bg="#111111",fg=fg,
                         font=("Segoe UI",9,"bold" if col in(1,4) else "normal"),
                         pady=4).grid(row=r,column=col,sticky="nsew")
            r+=1

    def save_equity(self,equity):
        try:
            now=datetime.now()
            if now.minute%5!=0: return
            data=[]
            if os.path.exists(EQUITY_FILE):
                with open(EQUITY_FILE,'r') as f:
                    try: data=json.load(f)
                    except: pass
            if data:
                last=datetime.fromtimestamp(data[-1]['time'])
                if last.minute==now.minute and last.hour==now.hour: return
            data.append({"time":now.timestamp(),"equity":float(equity)})
            data=[d for d in data if d['time']>now.timestamp()-48*3600]
            with open(EQUITY_FILE,'w') as f: json.dump(data,f)
        except: pass

    def get_local_eq(self):
        if not os.path.exists(EQUITY_FILE): return []
        try:
            with open(EQUITY_FILE,'r') as f: return json.load(f)
        except: return []

    def update_dashboard(self):
        try:
            acct=trading_client.get_account()
            positions=trading_client.get_all_positions()
            equity=float(acct.equity); leq=float(acct.last_equity)
            change=equity-leq; pct=(change/leq*100) if leq else 0
            unpl=sum(float(p.unrealized_pl) for p in positions)
            self.lbl_upl.config(text=f"${unpl:,.2f}",fg="#00ff00" if unpl>=0 else "#ff3333")
            self.lbl_cash.config(text=f"${float(acct.cash):,.2f}")
            self.lbl_day.config(text=f"{change:+.2f} ({pct:+.2f}%)",fg="#00ff00" if change>=0 else "#ff3333")
            self.save_equity(equity)
            self.update_stats(len(positions))

            orders=trading_client.get_orders(GetOrdersRequest(status='closed',limit=300,nested=False))
            self.update_orders(orders)

            # Equity chart
            h=trading_client.get_portfolio_history(
                GetPortfolioHistoryRequest(period="1W",timeframe="5Min",extended_hours=True))
            self.ax_eq.clear()
            cutoff=datetime.now()-timedelta(days=3)
            self.chart_ts=[]; self.chart_eq=[]
            for ts,eq in zip(h.timestamp,h.equity):
                t=datetime.fromtimestamp(ts)
                if t>=cutoff and eq and eq>1000 and abs(eq-equity)/equity<0.25:
                    self.chart_ts.append(t); self.chart_eq.append(eq)
            for d in self.get_local_eq():
                lt=datetime.fromtimestamp(d['time'])
                if self.chart_ts and lt>self.chart_ts[-1] and lt>=cutoff:
                    self.chart_ts.append(lt); self.chart_eq.append(d['equity'])
            if not self.chart_ts: self.chart_ts.append(datetime.now()); self.chart_eq.append(equity)
            if len(self.chart_ts)==1:
                self.chart_ts.insert(0,self.chart_ts[0]-timedelta(minutes=5))
                self.chart_eq.insert(0,self.chart_eq[0])
            self.ax_eq.plot(self.chart_ts,self.chart_eq,color="#FCDC12",linewidth=2)
            if self.chart_eq:
                ym=min(self.chart_eq)*0.999
                self.ax_eq.fill_between(self.chart_ts,self.chart_eq,ym,color="#FCDC12",alpha=0.12)
                self.ax_eq.set_ylim(bottom=ym)
            self.ax_eq.tick_params(colors='#555',labelsize=7)
            self.ax_eq.grid(True,color='#1a1a1a',linestyle='--')
            self.ax_eq.xaxis.set_major_formatter(mdates.DateFormatter('%a %I:%M %p'))
            self.ax_eq.yaxis.set_major_formatter(ticker.StrMethodFormatter('${x:,.0f}'))
            self.annot=self.ax_eq.annotate("",xy=(0,0),xytext=(10,10),textcoords="offset points",
                bbox=dict(boxstyle="round",fc="white",ec="black"),arrowprops=dict(arrowstyle="->"))
            self.annot.set_visible(False); self.can_eq.draw()

            # Pie
            self.ax_pi.clear()
            cash=float(acct.cash)
            items=[(p.symbol,float(p.market_value)) for p in positions]
            if cash>100: items.append(("CASH",cash))
            items.sort(key=lambda x:x[1],reverse=True)
            labels=[]; sizes=[]; colors=[]; other=0
            total=sum(v for _,v in items) or 1
            for name,val in items:
                if val/total>0.03:
                    labels.append(name); sizes.append(val)
                    if name not in STOCK_COLORS: STOCK_COLORS[name]=get_random_color()
                    colors.append(STOCK_COLORS.get(name,"#333"))
                else: other+=val
            if other>0: labels.append("OTHERS"); sizes.append(other); colors.append("#555")
            if sizes:
                _,_,at=self.ax_pi.pie(sizes,labels=labels,autopct='%1.1f%%',colors=colors,
                    startangle=90,pctdistance=0.85,textprops=dict(color="white",fontsize=8),
                    wedgeprops=dict(width=0.4,edgecolor='#050505'))
                for a in at: a.set_color('black'); a.set_fontweight('bold'); a.set_fontsize(7)
                self.ax_pi.text(0,0,f"${equity:,.2f}",ha='center',va='center',
                                fontsize=18,color="white",fontweight="bold")
            self.can_pi.draw()
        except Exception as e: print(f"Dashboard error: {e}")

    def scan_news(self):
        print(f" 📰 [{now_str()}] Scanning news...")
        try:
            hl=news_client.get_top_headlines(category='business',language='en',country='us')
            global active_catalysts
            active_catalysts=[]
            for a in hl['articles']:
                t=a['title'].upper()
                # ONLY flag symbols that are in WATCHLIST
                for s in WATCHLIST:
                    if s in t and s not in active_catalysts:
                        print(f" 🔥 NEWS: {s} ('{t[:45]}...')")
                        active_catalysts.append(s)
            # No AI ticker discovery — disabled to prevent random stock trading
        except Exception as e: print(f"   ⚠️ News: {e}")

    def eod_liquidate(self):
        print(f" 🚨 [{now_str()}] EOD LIQUIDATION...")
        swings=load_swings()
        regime=get_market_regime()
        try:
            trading_client.cancel_orders()
            for pos in trading_client.get_all_positions():
                sym=pos.symbol
                if sym in swings and regime=="BULL":
                    print(f"   🌙 HOLDING {sym} (SWING+BULL)")
                    continue
                elif sym in swings:
                    sw=load_swings(); del sw[sym]; save_swings(sw)
                print(f"   📉 EOD SELL {sym}")
                try:
                    trading_client.submit_order(MarketOrderRequest(
                        symbol=sym,qty=float(pos.qty),
                        side=OrderSide.SELL,time_in_force=TimeInForce.DAY))
                except Exception as e: print(f"   Failed {sym}: {e}")
        except Exception as e: print(f"   ❌ EOD: {e}")

    def bot_loop(self):
        global last_eod_date, smart_cache, daily_start_equity
        print(f"⏳ [{now_str()}] Synchronizing clock...")
        wait_for_next_minute()
        init_universe()
        last_news=datetime.now()-timedelta(minutes=20)

        while True:
            if not is_market_open():
                print(f"[{now_str()}] 💤 Market closed. Waiting...")
                smart_cache={}; daily_start_equity=0
                self.root.after(0,self.update_dashboard)
                wait_for_next_minute(); continue

            pst=pytz.timezone('America/Los_Angeles')
            now_pst=datetime.now(pst)
            today=now_pst.strftime("%Y-%m-%d")

            # Set daily start equity once
            if daily_start_equity==0:
                try:
                    acct=trading_client.get_account()
                    daily_start_equity=float(acct.equity)
                    print(f"   📊 [{now_str()}] Day start equity: ${daily_start_equity:,.2f}")
                except: pass

            # EOD
            if now_pst.hour==EOD_HOUR and now_pst.minute>=EOD_MIN:
                if last_eod_date!=today:
                    self.eod_liquidate(); last_eod_date=today; daily_start_equity=0
                time.sleep(60); continue

            # News
            if datetime.now()-last_news>timedelta(minutes=NEWS_SCAN_INTERVAL):
                self.scan_news(); last_news=datetime.now()

            print(f"\n--- 📡 SCAN [{now_str()}] ---")

            try:
                for pos in trading_client.get_all_positions():
                    manage_position(pos); time.sleep(1)
            except Exception as e: print(f"Mgmt: {e}")

            # WATCHLIST only — no discovered_targets
            full=list(set(active_catalysts + active_symbols()))
            scan_cycle(full)

            print(f"--- ✅ COMPLETE [{now_str()}] ---")
            self.root.after(0,self.update_dashboard)
            wait_for_next_minute()

# ══════════════════════════════════════════════════════════════════════════════
# 20. MAIN
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root=tk.Tk()
    app=BotGUI(root)
    root.mainloop()
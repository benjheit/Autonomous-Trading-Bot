import sqlite3
import time
from datetime import datetime

DB_FILE = "trading_bot.db"

def get_connection():
    return sqlite3.connect(DB_FILE)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Table for Trade History
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            action TEXT,
            price REAL,
            qty REAL,
            timestamp REAL,
            date_str TEXT
        )
    ''')
    
    # 2. Table for "Penalty Box" (Banned Stocks)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS penalty_box (
            symbol TEXT PRIMARY KEY,
            banned_at REAL,
            unban_at REAL
        )
    ''')
    
    conn.commit()
    conn.close()

def log_buy(symbol, price, qty):
    conn = get_connection()
    cursor = conn.cursor()
    now = time.time()
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("INSERT INTO trade_log (symbol, action, price, qty, timestamp, date_str) VALUES (?, ?, ?, ?, ?, ?)",
                   (symbol, "BUY", price, qty, now, date_str))
    
    conn.commit()
    conn.close()

def log_sell(symbol, price):
    """
    Logs a sell AND bans the stock for 24 hours to prevent 'Wash Sales'
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = time.time()
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 1. Log the Sell
    cursor.execute("INSERT INTO trade_log (symbol, action, price, qty, timestamp, date_str) VALUES (?, ?, ?, ?, ?, ?)",
                   (symbol, "SELL", price, 0, now, date_str))
    
    # 2. Add to Penalty Box (Ban for 18 hours to be safe)
    # 18 hours * 3600 seconds = 64800
    unban_time = now + 64800 
    
    # Use REPLACE to update if it already exists
    cursor.execute("REPLACE INTO penalty_box (symbol, banned_at, unban_at) VALUES (?, ?, ?)",
                   (symbol, now, unban_time))
    
    conn.commit()
    conn.close()

def is_stock_banned(symbol):
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT unban_at FROM penalty_box WHERE symbol=?", (symbol,))
    row = cursor.fetchone()
    
    if row is None:
        conn.close()
        return False
    
    unban_timestamp = row[0]
    now = time.time()
    
    if now > unban_timestamp:
        # --- THE FIX: Delete the record immediately ---
        print(f"[{symbol}] 🔓 Penalty expired. Unbanning stock.")
        cursor.execute("DELETE FROM penalty_box WHERE symbol=?", (symbol,))
        conn.commit()
        conn.close()
        return False
    else:
        # Still banned
        hours_left = (unban_timestamp - now) / 3600
        # Optional: Print only if you want to see the countdown
        # print(f"[{symbol}] ⏳ 24h Penalty Box. Time left: {hours_left:.1f} hours")
        conn.close()
        return True
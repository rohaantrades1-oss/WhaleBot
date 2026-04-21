import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime
from pybit.unified_trading import HTTP

# ==== CONFIG ====
API_KEY    = "cuJM7mNun4TfCtqqWo"
API_SECRET = "sLEIF4zxMfG6MLMELSAJ4S1XpIN3AWmuNcC1"
TELEGRAM_TOKEN = "8683564941:AAHaHm4COZyZPPmTyvDNwKB3QxBZ4y64cuI"
CHAT_ID        = "7910756984"

# ==== INIT ====
session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET
)

# ==== TELEGRAM ====
def send_alert(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        })
    except Exception as e:
        print(f"Telegram Error: {e}")

# ==== GET TOP ALTCOINS ====
def get_symbols():
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        resp = requests.get(url).json()
        tickers = resp['result']['list']

        filtered = [
            t for t in tickers
            if t['symbol'].endswith('USDT')
            and float(t['turnover24h']) > 5_000_000
            and float(t['turnover24h']) < 200_000_000
            and 'BTC'  not in t['symbol']
            and 'ETH'  not in t['symbol']
            and '1000' not in t['symbol']
        ]

        filtered.sort(key=lambda x: float(x['turnover24h']), reverse=True)
        symbols = [t['symbol'] for t in filtered[:80]]
        print(f"📊 {len(symbols)} coins mil gaye scan ke liye")
        return symbols
    except Exception as e:
        print(f"Symbol fetch error: {e}")
        return ["SOLUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT"]

# ==== GET KLINES ====
def get_df(symbol, interval, limit=60):
    try:
        url = (
            f"https://api.bybit.com/v5/market/kline"
            f"?category=linear&symbol={symbol}"
            f"&interval={interval}&limit={limit}"
        )
        resp = requests.get(url).json()
        data = resp['result']['list']
        data.reverse()  # Bybit reverse order mein deta hai

        df = pd.DataFrame(data, columns=[
            'time','open','high','low','close','volume','turnover'
        ])
        for col in ['open','high','low','close','volume']:
            df[col] = df[col].astype(float)
        df['time'] = pd.to_datetime(df['time'], unit='ms', errors='coerce')
        return df
    except Exception as e:
        print(f"Kline error {symbol}: {e}")
        return None

# ==== RSI ====
def calc_rsi(df, period=14):
    delta = df['close'].diff()
    gain  = delta.where(delta > 0, 0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

# ==== CVD ====
def calc_cvd(df):
    delta = np.where(df['close'] > df['open'], df['volume'], -df['volume'])
    return pd.Series(delta).cumsum().values

# ==== OPEN INTEREST ====
def get_oi_change(symbol):
    try:
        url = (
            f"https://api.bybit.com/v5/market/open-interest"
            f"?category=linear&symbol={symbol}"
            f"&intervalTime=5min&limit=10"
        )
        resp = requests.get(url).json()
        data = resp['result']['list']

        if len(data) >= 2:
            old_oi = float(data[-1]['openInterest'])
            new_oi = float(data[0]['openInterest'])
            change = ((new_oi - old_oi) / old_oi) * 100
            return round(change, 2)
        return 0
    except:
        return 0

# ==== ATR ====
def calc_atr(df, period=14):
    high  = df['high']
    low   = df['low']
    close = df['close']
    tr = pd.concat([
        high - low,
        abs(high - close.shift()),
        abs(low  - close.shift())
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

# ==== MAIN DETECTION ====
def check_symbol(symbol):
    try:
        df_15m = get_df(symbol, "15", limit=60)
        df_5m  = get_df(symbol, "5",  limit=50)

        if df_15m is None or df_5m is None:
            return
        if len(df_15m) < 25 or len(df_5m) < 25:
            return

        last_15 = df_15m.iloc[-1]
        score   = 0.0
        signals = {}

        # ================================
        # SIGNAL 1 — Vol/Price Divergence
        # ================================
        vol_ma5  = df_15m['volume'].rolling(5).mean().iloc[-1]
        vol_ma20 = df_15m['volume'].rolling(20).mean().iloc[-1]
        price_change_pct = abs(
            last_15['close'] - df_15m['close'].iloc[-6]
        ) / df_15m['close'].iloc[-6] * 100

        vol_rising = vol_ma5 > vol_ma20 * 1.4
        price_flat = price_change_pct < 1.0

        if vol_rising and price_flat:
            score += 2.5
            signals['Vol/Price Divergence'] = '✅'
        else:
            signals['Vol/Price Divergence'] = '❌'

        # ================================
        # SIGNAL 2 — Open Interest
        # ================================
        oi_change  = get_oi_change(symbol)
        oi_building = oi_change > 1.5

        if oi_building:
            score += 2.0
            signals[f'OI Building (+{oi_change}%)'] = '✅'
        else:
            signals[f'OI Change ({oi_change}%)'] = '❌'

        # ================================
        # SIGNAL 3 — CVD Divergence
        # ================================
        cvd            = calc_cvd(df_15m)
        cvd_rising     = cvd[-1] > cvd[-6] * 1.05
        price_not_moved = price_change_pct < 1.5

        if cvd_rising and price_not_moved:
            score += 2.0
            signals['CVD Bullish Divergence'] = '✅'
        else:
            signals['CVD Divergence'] = '❌'

        # ================================
        # SIGNAL 4 — Candle Compression
        # ================================
        recent_bodies = abs(
            df_15m['close'] - df_15m['open']
        ).iloc[-5:].mean()
        avg_body = abs(
            df_15m['close'] - df_15m['open']
        ).rolling(20).mean().iloc[-1]

        coiling = recent_bodies < avg_body * 0.45

        if coiling:
            score += 1.5
            signals['Candle Compression'] = '✅'
        else:
            signals['Candle Compression'] = '❌'

        # ================================
        # SIGNAL 5 — Liquidity Grab
        # ================================
        body_size  = abs(last_15['close'] - last_15['open'])
        lower_wick = last_15['open'] - last_15['low']
        is_bullish = last_15['close'] > last_15['open']

        wick_grab = (
            is_bullish and
            lower_wick > body_size * 1.5 and
            last_15['close'] > (last_15['high'] + last_15['low']) / 2
        )

        if wick_grab:
            score += 1.5
            signals['Liquidity Grab (Wick)'] = '✅'
        else:
            signals['Liquidity Grab'] = '❌'

        # ================================
        # SIGNAL 6 — RSI Neutral
        # ================================
        df_15m['rsi'] = calc_rsi(df_15m)
        rsi_val = df_15m['rsi'].iloc[-1]
        rsi_ok  = 45 < rsi_val < 62

        if rsi_ok:
            score += 1.0
            signals[f'RSI Neutral ({rsi_val:.1f})'] = '✅'
        else:
            signals[f'RSI ({rsi_val:.1f})'] = '❌'

        # ================================
        # SIGNAL 7 — 5min Volume Spike
        # ================================
        vol5_avg   = df_5m['volume'].rolling(20).mean().iloc[-1]
        vol5_last  = df_5m['volume'].iloc[-1]
        vol5_spike = vol5_last > vol5_avg * 2.0

        if vol5_spike:
            score += 0.5
            signals['5min Vol Spike'] = '✅'

        # ================================
        # ALERT
        # ================================
        max_score   = 10.5
        probability = int((score / max_score) * 100)

        if score >= 6.5:
            atr   = calc_atr(df_15m)
            entry = last_15['close']
            sl    = round(entry - atr * 1.5, 4)
            tp1   = round(entry + atr * 2.0, 4)
            tp2   = round(entry + atr * 4.0, 4)
            tp3   = round(entry + atr * 6.0, 4)

            sl_pct  = round((entry - sl)  / entry * 100, 2)
            tp1_pct = round((tp1 - entry) / entry * 100, 2)
            tp2_pct = round((tp2 - entry) / entry * 100, 2)
            tp3_pct = round((tp3 - entry) / entry * 100, 2)

            signal_lines = "\n".join([
                f"  {v} {k}" for k, v in signals.items()
            ])

            msg = f"""
🐋 <b>WHALE ACCUMULATION DETECTED</b>

📌 <b>Coin:</b> {symbol}
🕐 <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}
📊 <b>Timeframe:</b> 15m + 5m confirmed

<b>📡 Signals:</b>
{signal_lines}

🎯 <b>Score:</b> {score:.1f} / {max_score}
🔥 <b>Confidence:</b> {probability}%

💰 <b>Entry:</b> {entry}
🛑 <b>Stop Loss:</b> {sl} (-{sl_pct}%)
🎯 <b>TP1:</b> {tp1} (+{tp1_pct}%)
🎯 <b>TP2:</b> {tp2} (+{tp2_pct}%)
🎯 <b>TP3:</b> {tp3} (+{tp3_pct}%)

⚠️ DYOR — Bot alert hai, guarantee nahi
"""
            send_alert(msg)
            print(f"✅ ALERT! {symbol} | Score: {score} | {probability}%")
        else:
            print(f"⏭  Skip: {symbol} | Score: {score:.1f}")

    except Exception as e:
        print(f"❌ Error {symbol}: {e}")

# ==== MAIN LOOP ====
def main():
    print("🐋 Bybit Whale Bot Starting...")
    send_alert("🤖 <b>Bybit Whale Bot Started!</b>\nHar 5 minute mein market scan hoga...")

    while True:
        print(f"\n⏰ {datetime.now().strftime('%H:%M:%S')} — Scan shuru...")
        symbols = get_symbols()

        for sym in symbols:
            check_symbol(sym)
            time.sleep(0.3)

        print("✅ Scan complete — 5 min baad dobara...")
        time.sleep(300)

if __name__ == "__main__":
    main()
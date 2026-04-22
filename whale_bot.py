import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime

# ==== CONFIG ====
API_KEY    = "cuJM7mNun4TfCtqqWo"
API_SECRET = "sLEIF4zxMfG6MLMELSAJ4S1XpIN3AWmuNcC1"
TELEGRAM_TOKEN = "8683564941:AAHaHm4COZyZPPmTyvDNwKB3QxBZ4y64cuI"
CHAT_ID        = "7910756984"

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
            and float(t['turnover24h']) > 3_000_000
            and float(t['turnover24h']) < 200_000_000
            and 'BTC'  not in t['symbol']
            and 'ETH'  not in t['symbol']
            and '1000' not in t['symbol']
        ]
        filtered.sort(key=lambda x: float(x['turnover24h']), reverse=True)
        return [t['symbol'] for t in filtered[:100]]
    except Exception as e:
        print(f"Symbol error: {e}")
        return ["SOLUSDT","DOGEUSDT","XRPUSDT","ADAUSDT","AVAXUSDT"]

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
        data.reverse()
        df = pd.DataFrame(data, columns=[
            'time','open','high','low','close','volume','turnover'
        ])
        for col in ['open','high','low','close','volume']:
            df[col] = df[col].astype(float)
        df['time'] = pd.to_datetime(
            df['time'].astype(float), unit='ms', errors='coerce'
        )
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
            return round(((new_oi - old_oi) / old_oi) * 100, 2)
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

# ==== ALREADY PUMPED CHECK ====
def already_pumped(df):
    # Last 3 candles mein 3%+ move hua? Skip karo
    recent_move = (df['close'].iloc[-1] - df['close'].iloc[-4]) / df['close'].iloc[-4] * 100
    return recent_move > 3.0

# ==== VOLUME BLAST DETECTION (MAIN NEW SIGNAL) ====
def volume_blast(df):
    # Last candle ka volume avg se 5x+ zyada
    # BUT price abhi flat hai — yahi whale entry hai!
    vol_avg    = df['volume'].rolling(20).mean().iloc[-2]
    last_vol   = df['volume'].iloc[-1]
    price_move = abs(df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2] * 100
    
    vol_spike  = last_vol > vol_avg * 4.0   # Volume 4x blast
    price_flat = price_move < 1.5            # Price abhi nahi hili
    
    return vol_spike and price_flat

# ==== TIGHT CONSOLIDATION CHECK ====
def tight_consolidation(df):
    # Last 8 candles ki range bohot tight hai
    recent_high = df['high'].iloc[-8:].max()
    recent_low  = df['low'].iloc[-8:].min()
    range_pct   = (recent_high - recent_low) / recent_low * 100
    return range_pct < 2.5  # 2.5% se tight range

# ==== MAIN DETECTION ====
def check_symbol(symbol):
    try:
        # 5min pe scan — faster detection
        df_5m  = get_df(symbol, "5",  limit=60)
        df_15m = get_df(symbol, "15", limit=60)

        if df_5m is None or df_15m is None:
            return
        if len(df_5m) < 25 or len(df_15m) < 25:
            return

        # Already pumped? Skip!
        if already_pumped(df_5m):
            print(f"⏭  Skip (pumped): {symbol}")
            return

        last_5  = df_5m.iloc[-1]
        last_15 = df_15m.iloc[-1]
        score   = 0.0
        signals = {}

        # ================================
        # SIGNAL 1 — Volume Blast (Weight: 3.0) ⭐ NEW
        # Yahi asli whale entry signal hai
        # ================================
        vol_blast_detected = volume_blast(df_5m)
        if vol_blast_detected:
            score += 3.0
            signals['🔥 Volume Blast (Whale Entry!)'] = '✅'
        else:
            signals['Volume Blast'] = '❌'

        # ================================
        # SIGNAL 2 — Tight Consolidation (Weight: 2.0) ⭐ NEW
        # Pump se pehle price tight hoti hai
        # ================================
        consolidation = tight_consolidation(df_5m)
        if consolidation:
            score += 2.0
            signals['Tight Consolidation'] = '✅'
        else:
            signals['Tight Consolidation'] = '❌'

        # ================================
        # SIGNAL 3 — OI Building (Weight: 2.0)
        # ================================
        oi_change   = get_oi_change(symbol)
        oi_building = oi_change > 2.0  # 2%+ OI increase

        if oi_building:
            score += 2.0
            signals[f'OI Building (+{oi_change}%)'] = '✅'
        else:
            signals[f'OI Change ({oi_change}%)'] = '❌'

        # ================================
        # SIGNAL 4 — CVD Divergence (Weight: 1.5)
        # ================================
        cvd        = calc_cvd(df_5m)
        cvd_rising = cvd[-1] > cvd[-6] * 1.05

        price_change = abs(last_5['close'] - df_5m['close'].iloc[-6]) / df_5m['close'].iloc[-6] * 100

        if cvd_rising and price_change < 1.5:
            score += 1.5
            signals['CVD Bullish Divergence'] = '✅'
        else:
            signals['CVD Divergence'] = '❌'

        # ================================
        # SIGNAL 5 — RSI Neutral (Weight: 1.0)
        # ================================
        df_5m['rsi'] = calc_rsi(df_5m)
        rsi_val = df_5m['rsi'].iloc[-1]
        rsi_ok  = 40 < rsi_val < 65

        if rsi_ok:
            score += 1.0
            signals[f'RSI Neutral ({rsi_val:.1f})'] = '✅'
        else:
            signals[f'RSI ({rsi_val:.1f})'] = '❌'

        # ================================
        # ALERT — Score 8.0+ only
        # ================================
        max_score   = 9.5
        probability = int((score / max_score) * 100)

        if score >= 8.0:
            atr   = calc_atr(df_5m)
            entry = last_5['close']
            sl    = round(entry - atr * 2.0, 6)
            tp1   = round(entry + atr * 3.0, 6)
            tp2   = round(entry + atr * 6.0, 6)
            tp3   = round(entry + atr * 10.0, 6)

            sl_pct  = round((entry - sl)  / entry * 100, 2)
            tp1_pct = round((tp1 - entry) / entry * 100, 2)
            tp2_pct = round((tp2 - entry) / entry * 100, 2)
            tp3_pct = round((tp3 - entry) / entry * 100, 2)

            signal_lines = "\n".join([
                f"  {'✅' if v == '✅' else '❌'} {k}"
                for k, v in signals.items()
            ])

            msg = f"""
🐋 <b>WHALE ENTRY DETECTED</b>

📌 <b>Coin:</b> {symbol}
🕐 <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}
⚡ <b>Act FAST — Pre-pump signal!</b>

<b>📡 Signals:</b>
{signal_lines}

🎯 <b>Score:</b> {score:.1f} / {max_score}
🔥 <b>Confidence:</b> {probability}%

💰 <b>Entry:</b> {entry}
🛑 <b>Stop Loss:</b> {sl} (-{sl_pct}%)
🎯 <b>TP1:</b> {tp1} (+{tp1_pct}%)
🎯 <b>TP2:</b> {tp2} (+{tp2_pct}%)
🎯 <b>TP3:</b> {tp3} (+{tp3_pct}%)

⚠️ Max 5X leverage — SL zaroor lagao!
"""
            send_alert(msg)
            print(f"🔥 ALERT! {symbol} | Score: {score} | {probability}%")
        else:
            print(f"⏭  Skip: {symbol} | Score: {score:.1f}")

    except Exception as e:
        print(f"❌ Error {symbol}: {e}")

# ==== MAIN LOOP ====
def main():
    print("🐋 Whale Bot V2 Starting...")
    send_alert("🤖 <b>Whale Bot V2 Started!</b>\n⚡ Faster detection — Pre-pump signals!\nHar 3 minute mein scan hoga...")

    while True:
        print(f"\n⏰ {datetime.now().strftime('%H:%M:%S')} — Scan shuru...")
        symbols = get_symbols()
        print(f"📊 {len(symbols)} coins scanning...")

        for sym in symbols:
            check_symbol(sym)
            time.sleep(0.2)

        print("✅ Scan complete — 3 min baad dobara...")
        time.sleep(180)  # 3 min — faster!

if __name__ == "__main__":
    main()

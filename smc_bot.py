"""
SMC (Smart Money Concepts) Crypto Scanner — Telegram Bot
=========================================================
MULTI-TIMEFRAME: 4H Bias + 15M Entry

Logic:
  1. 4H Timeframe  → Determines market BIAS (Bullish or Bearish)
     - Identify 4H swing highs/lows
     - Detect 4H liquidity sweep
     - Confirm 4H MSS/ChoCh
     - Mark 4H Order Block / FVG as POI zone

  2. 15M Timeframe → Entry TRIGGER (only in direction of 4H bias)
     - Detect 15M liquidity sweep in same direction as 4H
     - Confirm 15M ChoCh
     - Find 15M OB or FVG for precise entry
     - Calculate tight SL + TP targets

  ONLY sends alert when BOTH timeframes align.
  This dramatically reduces false signals.

SETUP:
  pip install requests schedule
  Fill TELEGRAM_TOKEN and CHAT_ID below
  python smc_bot.py
"""

import requests
import time
import schedule
from datetime import datetime

# ============================================================
#   CONFIGURATION — FILL THESE IN
# ============================================================
TELEGRAM_TOKEN        = "8610582252:AAF72dcjZkN2vKSaoyWY998jbYoHEwpR9Jw"
CHAT_ID               = "5481858797"
SCAN_INTERVAL_MINUTES = 15                       # Scan frequency

# ============================================================
#   COINS TO MONITOR
# ============================================================
COINS = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX",
    "LINK", "NEAR", "HYPE", "ONDO", "INJ", "SUI", "TIA", "ARB",
    "OP", "MATIC", "DOT", "ATOM", "LTC", "UNI", "AAVE", "MKR",
    "FET", "RENDER", "WLD", "JUP", "PYTH", "BONK", "WIF", "PEPE"
]

def to_symbol(c): return c + "USDT"
def now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ============================================================
#   TELEGRAM
# ============================================================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        if r.status_code == 200:
            print(f"[{now()}] ✅ Alert sent")
        else:
            print(f"[{now()}] ❌ Telegram error: {r.text[:100]}")
    except Exception as e:
        print(f"[{now()}] ❌ Telegram failed: {e}")

# ============================================================
#   FETCH CANDLES FROM BINANCE
# ============================================================
def get_candles(symbol, interval="15m", limit=100):
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10
        )
        return [{
            "open":  float(c[1]),
            "high":  float(c[2]),
            "low":   float(c[3]),
            "close": float(c[4]),
            "vol":   float(c[5]),
        } for c in r.json()]
    except Exception as e:
        print(f"[{now()}] Fetch error {symbol} {interval}: {e}")
        return []

# ============================================================
#   SHARED SMC FUNCTIONS
# ============================================================
def find_swings(candles, lookback=5):
    highs, lows = [], []
    for i in range(lookback, len(candles) - lookback):
        win_h = [c["high"] for c in candles[i-lookback:i+lookback+1]]
        win_l = [c["low"]  for c in candles[i-lookback:i+lookback+1]]
        if candles[i]["high"] == max(win_h): highs.append((i, candles[i]["high"]))
        if candles[i]["low"]  == min(win_l): lows.append((i,  candles[i]["low"]))
    return highs, lows

def detect_sweep(candles, highs, lows, lookback=3):
    if len(candles) < 5 or not highs or not lows:
        return None
    last = candles[-1]
    recent_high = max(h[1] for h in highs[-lookback:])
    recent_low  = min(l[1] for l in lows[-lookback:])

    # Bearish sweep: wick above swing high, closes back below
    if last["high"] > recent_high and last["close"] < recent_high:
        wick = last["high"] - last["close"]
        body = abs(last["close"] - last["open"])
        if wick > body * 0.5:
            return {"type": "BEARISH_SWEEP", "level": recent_high}

    # Bullish sweep: wick below swing low, closes back above
    if last["low"] < recent_low and last["close"] > recent_low:
        wick = last["close"] - last["low"]
        body = abs(last["close"] - last["open"])
        if wick > body * 0.5:
            return {"type": "BULLISH_SWEEP", "level": recent_low}

    return None

def detect_mss(candles, sweep_type):
    if len(candles) < 6:
        return False
    recent = candles[-6:]
    last = recent[-1]
    if sweep_type == "BULLISH_SWEEP":
        recent_high   = max(c["high"] for c in recent[:-2])
        bullish_body  = last["close"] > last["open"]
        breaks_high   = last["close"] > recent_high
        strong        = (last["close"] - last["open"]) > (last["high"] - last["low"]) * 0.5
        return bullish_body and breaks_high and strong
    elif sweep_type == "BEARISH_SWEEP":
        recent_low    = min(c["low"] for c in recent[:-2])
        bearish_body  = last["close"] < last["open"]
        breaks_low    = last["close"] < recent_low
        strong        = (last["open"] - last["close"]) > (last["high"] - last["low"]) * 0.5
        return bearish_body and breaks_low and strong
    return False

def detect_fvg(candles):
    fvgs = []
    for i in range(2, len(candles)):
        c0, c2 = candles[i-2], candles[i]
        if c0["high"] < c2["low"]:
            fvgs.append({"type": "BULLISH_FVG", "top": c2["low"], "bottom": c0["high"]})
        if c0["low"] > c2["high"]:
            fvgs.append({"type": "BEARISH_FVG", "top": c0["low"], "bottom": c2["high"]})
    return fvgs[-1] if fvgs else None

def detect_ob(candles, sweep_type):
    if len(candles) < 10:
        return None
    recent = candles[-10:]
    if sweep_type == "BULLISH_SWEEP":
        for i in range(len(recent)-2, 0, -1):
            c = recent[i]
            if c["close"] < c["open"]:
                return {"type": "BULLISH_OB", "top": c["open"], "bottom": c["close"]}
    elif sweep_type == "BEARISH_SWEEP":
        for i in range(len(recent)-2, 0, -1):
            c = recent[i]
            if c["close"] > c["open"]:
                return {"type": "BEARISH_OB", "top": c["close"], "bottom": c["open"]}
    return None

def detect_4h_trend(candles):
    """
    Determine 4H market bias using last 30 candles:
    - BULLISH: price making higher highs and higher lows
    - BEARISH: price making lower highs and lower lows
    - NEUTRAL: mixed structure
    """
    if len(candles) < 20:
        return "NEUTRAL"

    closes = [c["close"] for c in candles[-20:]]
    highs  = [c["high"]  for c in candles[-20:]]
    lows   = [c["low"]   for c in candles[-20:]]

    # Split into two halves and compare
    mid = len(closes) // 2
    first_half_high = max(highs[:mid])
    second_half_high = max(highs[mid:])
    first_half_low  = min(lows[:mid])
    second_half_low  = min(lows[mid:])

    hh = second_half_high > first_half_high  # Higher High
    hl = second_half_low  > first_half_low   # Higher Low
    lh = second_half_high < first_half_high  # Lower High
    ll = second_half_low  < first_half_low   # Lower Low

    if hh and hl:   return "BULLISH"
    if lh and ll:   return "BEARISH"
    return "NEUTRAL"

def fp(price):
    if price >= 1000:  return f"${price:,.2f}"
    elif price >= 1:   return f"${price:.4f}"
    else:              return f"${price:.6f}"

# ============================================================
#   MULTI-TIMEFRAME SCAN PER COIN
# ============================================================
alerted = {}

def scan_coin(coin):
    symbol = to_symbol(coin)

    # ── STEP 1: 4H BIAS ──────────────────────────────────────
    candles_4h = get_candles(symbol, interval="4h", limit=80)
    if len(candles_4h) < 30:
        return

    bias_4h = detect_4h_trend(candles_4h)
    if bias_4h == "NEUTRAL":
        return  # No clear bias — skip coin

    highs_4h, lows_4h = find_swings(candles_4h, lookback=4)
    sweep_4h = detect_sweep(candles_4h, highs_4h, lows_4h, lookback=4)
    mss_4h   = detect_mss(candles_4h, sweep_4h["type"]) if sweep_4h else False
    fvg_4h   = detect_fvg(candles_4h[-20:])
    ob_4h    = detect_ob(candles_4h, sweep_4h["type"]) if sweep_4h else None

    # Determine 4H POI (Point of Interest)
    poi_4h = ob_4h or fvg_4h  # Where 4H wants price to return

    # ── STEP 2: 15M ENTRY TRIGGER ────────────────────────────
    candles_15m = get_candles(symbol, interval="15m", limit=80)
    if len(candles_15m) < 20:
        return

    highs_15m, lows_15m = find_swings(candles_15m, lookback=3)
    sweep_15m = detect_sweep(candles_15m, highs_15m, lows_15m, lookback=3)
    if not sweep_15m:
        return

    mss_15m = detect_mss(candles_15m, sweep_15m["type"])
    if not mss_15m:
        return

    fvg_15m = detect_fvg(candles_15m[-15:])
    ob_15m  = detect_ob(candles_15m, sweep_15m["type"])

    # ── STEP 3: ALIGNMENT CHECK ──────────────────────────────
    # 15M sweep must align with 4H bias
    aligned = (
        (bias_4h == "BULLISH" and sweep_15m["type"] == "BULLISH_SWEEP") or
        (bias_4h == "BEARISH" and sweep_15m["type"] == "BEARISH_SWEEP")
    )
    if not aligned:
        return  # Counter-trend — skip

    # ── STEP 4: BUILD ALERT ──────────────────────────────────
    current_price = candles_15m[-1]["close"]
    is_long   = sweep_15m["type"] == "BULLISH_SWEEP"
    direction = "🟢 LONG (BUY)" if is_long else "🔴 SHORT (SELL)"
    emoji     = "🚀" if is_long else "📉"

    # Entry zone from 15M OB or FVG
    if ob_15m:
        entry_top, entry_bottom = ob_15m["top"], ob_15m["bottom"]
        entry_src = "15M Order Block"
    elif fvg_15m:
        entry_top, entry_bottom = fvg_15m["top"], fvg_15m["bottom"]
        entry_src = "15M Fair Value Gap"
    else:
        entry_top    = current_price * (1.002 if is_long else 1.001)
        entry_bottom = current_price * (0.999 if is_long else 0.998)
        entry_src = "Current Price Zone"

    # SL just beyond the sweep level
    if is_long:
        sl  = sweep_15m["level"] * 0.997
        tp1 = current_price * 1.03
        tp2 = current_price * 1.06
        tp3 = current_price * 1.09
    else:
        sl  = sweep_15m["level"] * 1.003
        tp1 = current_price * 0.97
        tp2 = current_price * 0.94
        tp3 = current_price * 0.91

    risk = abs(current_price - sl)
    rr2  = round(abs(current_price - tp2) / risk, 1) if risk > 0 else 0
    rr3  = round(abs(current_price - tp3) / risk, 1) if risk > 0 else 0

    # Confluences count
    confluences = []
    confluences.append(f"✅ 4H Bias: <b>{bias_4h}</b>")
    if sweep_4h: confluences.append(f"✅ 4H Liquidity Sweep @ {fp(sweep_4h['level'])}")
    if mss_4h:   confluences.append("✅ 4H Market Structure Shift confirmed")
    if ob_4h:    confluences.append(f"✅ 4H Order Block: {fp(ob_4h['bottom'])} – {fp(ob_4h['top'])}")
    elif fvg_4h: confluences.append(f"✅ 4H FVG: {fp(fvg_4h['bottom'])} – {fp(fvg_4h['top'])}")
    confluences.append(f"✅ 15M Liquidity Sweep @ {fp(sweep_15m['level'])}")
    confluences.append("✅ 15M Market Structure Shift (ChoCh)")
    if ob_15m:   confluences.append(f"✅ 15M Order Block: {fp(ob_15m['bottom'])} – {fp(ob_15m['top'])}")
    if fvg_15m:  confluences.append(f"✅ 15M FVG: {fp(fvg_15m['bottom'])} – {fp(fvg_15m['top'])}")

    conf_score = len(confluences)
    quality = "⭐⭐⭐ HIGH" if conf_score >= 6 else "⭐⭐ MEDIUM" if conf_score >= 4 else "⭐ BASIC"

    # Dedup — 1 alert per coin per direction per 2 hours
    alert_key = f"{coin}_{sweep_15m['type']}"
    if time.time() - alerted.get(alert_key, 0) < 7200:
        return
    alerted[alert_key] = time.time()

    msg = f"""{emoji} <b>SMC SETUP — {coin}/USDT</b>
📊 {direction}
💰 Price: {fp(current_price)}
🏆 Quality: {quality} ({conf_score} confluences)

━━━━━━━━━━━━━━━━━━
📐 <b>TIMEFRAME ANALYSIS:</b>
{chr(10).join(confluences)}

━━━━━━━━━━━━━━━━━━
🎯 <b>TRADE PLAN:</b>
📥 Entry: {fp(entry_bottom)} – {fp(entry_top)}
   ({entry_src})
🎯 TP1: {fp(tp1)} (+3%)
🎯 TP2: {fp(tp2)} (+6%) → RR 1:{rr2}
🎯 TP3: {fp(tp3)} (+9%) → RR 1:{rr3}
🛑 SL: {fp(sl)}

━━━━━━━━━━━━━━━━━━
⏰ {now()} UTC
⚠️ DYOR — Not financial advice"""

    send_telegram(msg)
    print(f"[{now()}] 🔔 {coin} {sweep_15m['type']} | 4H:{bias_4h} | Score:{conf_score}")

# ============================================================
#   SCANNER LOOP
# ============================================================
def run_scan():
    print(f"\n[{now()}] 🔍 Scanning {len(COINS)} coins (4H+15M MTF)...")
    for coin in COINS:
        try:
            scan_coin(coin)
            time.sleep(0.4)
        except Exception as e:
            print(f"[{now()}] Error {coin}: {e}")
    print(f"[{now()}] ✅ Done. Next scan in {SCAN_INTERVAL_MINUTES}m")

# ============================================================
#   MAIN
# ============================================================
def main():
    print("=" * 55)
    print("  SMC Multi-Timeframe Scanner — Telegram Bot")
    print(f"  Coins: {len(COINS)} | 4H Bias + 15M Entry")
    print(f"  Scan every: {SCAN_INTERVAL_MINUTES} minutes")
    print("=" * 55)

    if TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("\n❌ Set your TELEGRAM_TOKEN first!"); return
    if CHAT_ID == "YOUR_CHAT_ID_HERE":
        print("\n❌ Set your CHAT_ID first!"); return

    send_telegram(
        f"🤖 <b>SMC Multi-Timeframe Scanner Started!</b>\n\n"
        f"📊 Strategy: 4H Bias → 15M Entry\n"
        f"🪙 Monitoring: {len(COINS)} coins\n"
        f"⏱ Scanning every {SCAN_INTERVAL_MINUTES} minutes\n\n"
        f"Only HIGH-CONFLUENCE setups where 4H and 15M align will be sent.\n\n"
        f"Good luck! 🎯"
    )

    run_scan()
    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(run_scan)

    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()

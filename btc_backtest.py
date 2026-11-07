"""
BTC v1 Backtest  (4H candles, trend + pullback strategy)
--------------------------------------------------------
እንዴት ማሄድ: ሙሉውን ኮድ Google Colab ሴል ውስጥ ለጥፈህ Run ጫን።
ውጤት: ስክሪን ላይ ሪፖርት + trades.csv ፋይል።
"""
import time
import numpy as np
import pandas as pd
import requests

# ---------------- ቅንብሮች (እዚህ ብቻ ቀይር) ----------------
SYMBOL = "BTCUSDT"
INTERVAL = "4h"
YEARS = 4
FEE = 0.001          # 0.1% በእያንዳንዱ ጎን (መግቢያና መውጫ)
SLIPPAGE = 0.0002    # 0.02%
RISK_PER_TRADE = 0.01  # በአንድ ንግድ 1% ካፒታል
SL_ATR = 1.5         # stop-loss = 1.5 x ATR
TP_ATR = 2.5         # take-profit = 2.5 x ATR
MAX_HOLD = 30        # ከፍተኛ ጊዜ (ሻማዎች) = 5 ቀን
IN_SAMPLE_SHARE = 0.7  # 70% ለመማር፣ 30% ለማረጋገጫ (ያልታየ ዳታ)
DEMO = False         # True ካደረግህ ኢንተርኔት ሳይኖር በሰው ሰራሽ ዳታ ይሞክራል
# ------------------------------------------------------------


def download_klines():
    url = "https://data-api.binance.vision/api/v3/klines"
    end = int(time.time() * 1000)
    start = end - YEARS * 365 * 24 * 3600 * 1000
    rows = []
    while start < end:
        r = requests.get(url, params=dict(symbol=SYMBOL, interval=INTERVAL,
                                          startTime=start, limit=1000), timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        rows += data
        start = data[-1][0] + 1
        time.sleep(0.2)
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume",
                                     "ct", "qv", "n", "tb", "tq", "ig"])
    df["time"] = pd.to_datetime(df["t"], unit="ms")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df[["time", "open", "high", "low", "close", "volume"]].drop_duplicates("time").reset_index(drop=True)


def demo_data(n=8000):
    rng = np.random.default_rng(1)
    close = 20000 * np.exp(np.cumsum(rng.normal(0.0001, 0.012, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.006, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.006, n))
    t = pd.date_range("2022-01-01", periods=n, freq="4h")
    return pd.DataFrame(dict(time=t, open=open_, high=high, low=low, close=close,
                             volume=rng.uniform(100, 1000, n)))


def add_indicators(df):
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    d = df["close"].diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["vol_ma"] = df["volume"].rolling(20).mean()
    return df


def find_signals(df):
    """ሲግናል በሻማ i መጨረሻ ላይ ይወለዳል፤ መግቢያው በ i+1 open ነው (ወደፊት አናይም)።"""
    prev_rsi = df["rsi"].shift()
    long_sig = (df["close"] > df["ema200"]) & (prev_rsi < 40) & (df["rsi"] >= 40) & (df["volume"] > 0.8 * df["vol_ma"])
    short_sig = (df["close"] < df["ema200"]) & (prev_rsi > 60) & (df["rsi"] <= 60) & (df["volume"] > 0.8 * df["vol_ma"])
    return long_sig, short_sig


def run_backtest(df):
    long_sig, short_sig = find_signals(df)
    trades = []
    i = 210
    n = len(df)
    while i < n - 2:
        side = 1 if long_sig.iloc[i] else (-1 if short_sig.iloc[i] else 0)
        if side == 0:
            i += 1
            continue
        atr = df["atr"].iloc[i]
        entry = df["open"].iloc[i + 1] * (1 + SLIPPAGE * side)
        sl = entry - side * SL_ATR * atr
        tp = entry + side * TP_ATR * atr
        risk = abs(entry - sl)
        exit_price, reason, j = None, None, i + 1
        for j in range(i + 1, min(i + 1 + MAX_HOLD, n)):
            hi, lo = df["high"].iloc[j], df["low"].iloc[j]
            hit_sl = lo <= sl if side == 1 else hi >= sl
            hit_tp = hi >= tp if side == 1 else lo <= tp
            if hit_sl:  # ሁለቱም በአንድ ሻማ ከተነኩ SL መጀመሪያ ይቆጠራል (ጥንቃቄ)
                exit_price, reason = sl, "SL"
                break
            if hit_tp:
                exit_price, reason = tp, "TP"
                break
        if exit_price is None:
            exit_price, reason = df["close"].iloc[j], "TIME"
        exit_price = exit_price * (1 - SLIPPAGE * side)
        gross = (exit_price - entry) * side
        cost = (entry + exit_price) * FEE
        r = (gross - cost) / risk
        trades.append(dict(entry_time=df["time"].iloc[i + 1], exit_time=df["time"].iloc[j],
                           side="LONG" if side == 1 else "SHORT", entry=round(entry, 2),
                           sl=round(sl, 2), tp=round(tp, 2), exit=round(exit_price, 2),
                           reason=reason, R=round(r, 3),
                           rsi=round(df["rsi"].iloc[i], 1), atr=round(atr, 2)))
        i = j + 1  # አንድ ጊዜ አንድ ንግድ ብቻ
    return pd.DataFrame(trades)


def report(tr, name):
    if tr.empty:
        print(f"\n[{name}] ምንም ንግድ የለም")
        return
    wins, losses = tr[tr.R > 0], tr[tr.R <= 0]
    pf = wins.R.sum() / abs(losses.R.sum()) if len(losses) and losses.R.sum() != 0 else float("inf")
    equity = (1 + tr.R * RISK_PER_TRADE).cumprod()
    dd = (equity / equity.cummax() - 1).min()
    print(f"\n===== {name} =====")
    print(f"ንግዶች:            {len(tr)}")
    print(f"Win rate:          {len(wins) / len(tr):.1%}")
    print(f"Profit factor:     {pf:.2f}")
    print(f"አማካይ R:           {tr.R.mean():.3f}")
    print(f"ጠቅላላ ተመላሽ:       {equity.iloc[-1] - 1:.1%}  (በ {RISK_PER_TRADE:.0%} risk)")
    print(f"Max drawdown:      {dd:.1%}")
    print(f"LONG / SHORT:      {(tr.side == 'LONG').sum()} / {(tr.side == 'SHORT').sum()}")


if __name__ == "__main__":
    df = demo_data() if DEMO else download_klines()
    print(f"ዳታ: {len(df)} ሻማዎች, {df.time.iloc[0]} → {df.time.iloc[-1]}")
    df = add_indicators(df)
    trades = run_backtest(df)
    trades.to_csv("trades.csv", index=False)
    cut = df["time"].iloc[int(len(df) * IN_SAMPLE_SHARE)]
    report(trades, "ሙሉ ጊዜ")
    report(trades[trades.entry_time < cut], "መማሪያ ክፍል (70%)")
    report(trades[trades.entry_time >= cut], "ማረጋገጫ ክፍል (30%, ያልታየ)")
    print("\nትርጉም: ማረጋገጫው ክፍል ከመማሪያው ክፍል በጣም የባሰ ከሆነ ስትራቴጂው ዕድለኛ ነበር ማለት ነው።")

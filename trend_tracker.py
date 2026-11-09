"""
trend_tracker.py - ዕለታዊ trend መከታተያ (paper)  ->  docs/index.html (ዋና ገጽ)
ማሄድ: python trend_tracker.py     ሙከራ: python trend_tracker.py demo
btc_backtest.py በዚሁ ፎልደር ውስጥ መኖር አለበት።
ስትራቴጂዎች: Buy & Hold | Donchian 55/20 | EMA 50/200 | Ensemble + vol-target (እጩ)
"""
import json, os, sys
import numpy as np
import pandas as pd
import btc_backtest as bt
try:
    from notify import notify
except Exception:
    def notify(text):
        print("[ማሳወቂያ]", text)

DEMO = "demo" in sys.argv
COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT"]
COST = bt.FEE + bt.SLIPPAGE
DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DIR, "data", "trend_demo.json" if DEMO else "trend.json")
SITE = os.path.join(DIR, "docs", "trend_demo.html" if DEMO else "index.html")
os.makedirs(os.path.dirname(DATA), exist_ok=True)
os.makedirs(os.path.dirname(SITE), exist_ok=True)
bt.YEARS, bt.INTERVAL = 9, "1d"
KEYS = ["bh", "don", "ema", "c15", "c25", "c40"]


def get(sym, k):
    if DEMO:
        d = bt.demo_data(3300)
        f = np.exp(np.cumsum(np.random.default_rng(k).normal(0, 0.02, len(d))))
        for c in ["open", "high", "low", "close"]:
            d[c] = d[c] * f
        d["time"] = pd.date_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=len(d), freq="D")
    else:
        bt.SYMBOL = sym
        d = bt.download_klines()
        if d["time"].iloc[-1] + pd.Timedelta(days=1) > pd.Timestamp.now(tz="UTC").tz_localize(None):
            d = d.iloc[:-1]  # ያልተዘጋ ቀን አንጠቀምም
    return d.set_index(pd.DatetimeIndex(d["time"]))


def donchian(c, h, l):
    up = (c > h.rolling(55).max().shift(1)).values
    dn = (c < l.rolling(20).min().shift(1)).values
    pos, p = np.zeros(len(c)), 0
    for i in range(len(c)):
        p = 1 if (p == 0 and up[i]) else 0 if (p == 1 and dn[i]) else p
        pos[i] = p
    return pd.Series(pos, index=c.index)


def ens(c):
    return sum((c > c.rolling(n).mean()).astype(float) for n in (20, 50, 100, 200)) / 4


def volscale(c, tv=0.40):
    rv = c.pct_change().rolling(30).std() * np.sqrt(365)
    return (tv / rv).clip(upper=1).fillna(0)


def stats(r):
    if len(r) < 30 or r.std() == 0:
        return None
    e = (1 + r).cumprod()
    return dict(sh=round(float(r.mean() / r.std() * np.sqrt(365)), 2),
                ann=round(float(e.iloc[-1] ** (365 / len(r)) - 1), 3), dd=round(float((e / e.cummax() - 1).min()), 3))


def run():
    db = json.load(open(DATA, encoding="utf-8")) if os.path.exists(DATA) else {}
    R, H, coins = {k: [] for k in KEYS}, {k: [] for k in KEYS}, {}
    for k, sym in enumerate(COINS):
        try:
            d = get(sym, k)
        except Exception as ex:
            print(f"{sym} ዳታ አልወረደም: {ex}")
            continue
        c = d["close"]
        P = {"bh": pd.Series(1.0, index=c.index), "don": donchian(c, d["high"], d["low"]),
             "ema": (c.ewm(span=50, adjust=False).mean() > c.ewm(span=200, adjust=False).mean()).astype(float),
             "c15": ens(c) * volscale(c, 0.15), "c25": ens(c) * volscale(c, 0.25), "c40": ens(c) * volscale(c)}
        ret = c.pct_change().fillna(0)
        for key, p in P.items():
            held = p.shift(1).fillna(0)
            R[key].append(held * ret - held.diff().abs().fillna(held) * COST)
            H[key].append(held)
        sma = c.rolling(200).mean().iloc[-1]
        coins[sym] = dict(price=round(float(c.iloc[-1]), 4), up=bool(c.iloc[-1] > sma) if sma == sma else None,
                          w={key: round(float(p.iloc[-1]), 2) for key, p in P.items()})
    if not R["bh"]:
        print("ምንም ዳታ አልወረደም")
        return
    port = {k: pd.concat(v, axis=1, sort=True).mean(axis=1) for k, v in R.items()}
    expo = {k: pd.concat(v, axis=1, sort=True).mean(axis=1) for k, v in H.items()}
    t0 = port["bh"].index[0] + pd.Timedelta(days=200)
    port = {k: v[v.index >= t0] for k, v in port.items()}
    last = port["bh"].index[-1]
    db.setdefault("live_start", str((last + pd.Timedelta(days=1)).date()))
    ls = pd.Timestamp(db["live_start"])
    n = len(port["bh"])
    sel = sorted(set(range(0, n, 3)) | {n - 1})
    out = dict(live_start=db["live_start"], asof=str(last.date()), coins=coins,
               checked=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
               dates=[str(port["bh"].index[i].date()) for i in sel], eq={}, stats={})
    for k in KEYS:
        r = port[k]
        e = (1 + r).cumprod() * 100
        out["eq"][k] = [round(float(e.iloc[i]), 2) for i in sel]
        hist, live = r[r.index < ls], r[r.index >= ls]
        le = (1 + live).cumprod()
        out["stats"][k] = dict(hist=stats(hist), exp=round(float(expo[k][expo[k].index.isin(hist.index)].mean()), 2),
                               live=dict(days=len(live), ret=round(float(le.iloc[-1] - 1), 4) if len(live) else 0,
                                         dd=round(float((le / le.cummax() - 1).min()), 4) if len(live) else 0))
    old, alerts = db.get("coins") or {}, []
    for sym, m in coins.items():
        o, n, nm = (old.get(sym) or {}).get("w"), m["w"], sym.replace("USDT", "")
        if not o or "c25" not in o:
            continue
        if abs(n["c25"] - o["c25"]) >= 0.10 or (o["c25"] == 0) != (n["c25"] == 0):
            alerts.append(f"{nm}: {o['c25']*100:.0f}% → {n['c25']*100:.0f}%")
        for k, lab in (("don", "Donchian"), ("ema", "EMA")):
            if (o.get(k, 0) > 0) != (n[k] > 0):
                alerts.append(f"{nm} {lab}: {'ውስጥ ገባ' if n[k] > 0 else 'ወጣ'}")
    json.dump(out, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if alerts:
        notify(f"📊 ዕለታዊ trend ({out['asof']}፣ 25% ደረጃ፣ paper)\n" + "\n".join(alerts[:20]))
    with open(SITE, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("%%DATA%%", json.dumps(out, ensure_ascii=False).replace("</", "<\\/")))
    print(f"[{out['checked']}] ሳንቲሞች: {len(coins)} | እስከ {out['asof']} | live ከ {out['live_start']} | {SITE}")


PAGE = r"""<!doctype html><html lang="am"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Crypto trend tracker</title>
<style>
:root{--bg:#eef1f4;--card:#fff;--tx:#101828;--mu:#5b6574;--ln:#d5dbe3;--g:#0f7b55;--r:#c0392b;--b:#1f4fd8;--w:#fff4d6}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--tx:#e6e9ef;--mu:#8b95a5;--ln:#262d38;--g:#3ecf8e;--r:#ff7066;--b:#7aa2ff;--w:#2a2410}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.55 "Ebrima","Nyala","Segoe UI",system-ui,sans-serif;padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}
main{max-width:900px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.5rem;margin:0}h2{font-size:1.15rem;margin:26px 0 8px}a{color:var(--b)}
.top{display:flex;justify-content:space-between;align-items:center;gap:10px}
.mu{color:var(--mu);font-size:.92rem}.g{color:var(--g)}.r{color:var(--r)}
button{font:inherit;background:transparent;color:var(--tx);border:1px solid var(--ln);border-radius:6px;padding:6px 12px;cursor:pointer}
button:focus-visible{outline:3px solid var(--b);outline-offset:2px}
.warn{background:var(--w);border:1px solid var(--ln);border-radius:6px;padding:10px 12px;margin:14px 0;font-size:.92rem}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--ln);white-space:nowrap}th:first-child,td:first-child{text-align:left}th{color:var(--mu);font-weight:400;font-size:.88rem}
.ch{background:var(--card);border:1px solid var(--ln);border-radius:8px;padding:10px}svg{width:100%;height:auto;display:block}
.lg{display:flex;gap:14px;flex-wrap:wrap;font-size:.88rem;margin-top:6px}.lg i{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:5px}
button[aria-pressed=true]{background:var(--tx);color:var(--bg)}
</style></head><body><main>
<div class="top"><h1 id="h"></h1><button id="lg" type="button" aria-label="Language"></button></div>
<div class="mu" id="ck"></div><div class="mu"><a href="hourly.html" id="hr"></a> · <a href="experimental.html" id="ex"></a></div>
<div class="warn" id="wn"></div>
<h2 id="h1"></h2><div class="tw" id="pos"></div>
<h2 id="h2"></h2><div id="lvw"></div><div class="ch" id="chart"></div><div class="mu" id="cn"></div>
<h2 id="h3"></h2><div class="tw" id="st"></div><div class="mu" id="nt"></div>
</main><script>
const D=%%DATA%%,$=q=>document.querySelector(q),ALL=["bh","don","ema","c15","c25","c40"];
const I={am:{h:"ክሪፕቶ trend መከታተያ",chk:"ተፈትሿል ",ex:"ሙከራ: 4 ሰዓት ሲግናሎች →",hr:"1 ሰዓት S2 (እጩ፣ ቀጥታ) →",
warn:"<b>ይህ paper trading እና ምርምር ነው።</b> ምናባዊ ገንዘብ ብቻ። የገንዘብ ምክር አይደለም፣ ምንም ትርፍ ዋስትና የለም፣ እና እነዚህ ስትራቴጂዎች ቢያንስ -30% እስከ -55% የሚደርስ ኪሳራ አስተናግደዋል።",
h1:"የዛሬ ቦታዎች (ዕለታዊ መዝጊያ እስከ ",coin:"ሳንቲም",price:"ዋጋ",tr:"አዝማሚያ",up:"ከ200 ቀን አማካይ በላይ",dn:"ከ200 ቀን አማካይ በታች",
cand:"Ensemble + vol-target (እጩ)",risk:"የአደጋ ደረጃ (የ volatility ዒላማ):",inn:"ውስጥ",out:"ውጭ",h2:"የ$100 እድገት (ሎጋሪዝም ሚዛን)",live:"ቀጥታ ይጀምራል",
cn:"ከ'ቀጥታ' መስመር በፊት ያለው ታሪካዊ ማስመሰል ነው (ሕጎቹ ቀድመው የተወሰኑ፣ ዳታው ግን ያለፈ)። ከመስመሩ በኋላ ያለው ብቻ የወደፊት ማስረጃ ነው።",
h3:"ስታቲስቲክስ",s:"ስትራቴጂ",sh:"Sharpe",an:"ዓመታዊ",dd:"ትልቁ ኪሳራ",exp:"በገበያ ውስጥ",lv:"ቀጥታ (ቀን / ተመላሽ / ኪሳራ)",
nt:"ታሪካዊ ማስመሰል: 8 ሳንቲሞች እኩል ክብደት፣ ክፍያ እና slippage ተካተዋል። ሳንቲሞቹ የዛሬ ታዋቂዎች ስለሆኑ (survivorship bias) ታሪካዊ ውጤቱ ከእውነቱ ይሻላል። የእጩው ስትራቴጂ ከ Buy & Hold ያነሰ ኪሳራ አሳይቷል፣ ነገር ግን ወደፊትም እንደሚሆን ማረጋገጫ የለም። * 15% ደረጃ በ stress test ከተፈተነው ክልል (25–60%) ውጭ ነው፤ ኪሳራው ያነሰ ይሆናል ተብሎ ይጠበቃል፣ ግን አልተፈተነም።"},
en:{h:"Crypto trend tracker",chk:"Last checked ",ex:"Experimental: 4-hour signals →",hr:"Hourly S2 (candidate, live) →",
warn:"<b>This is paper trading and research.</b> Virtual money only. Not financial advice, no profit is guaranteed, and these strategies have suffered drawdowns of roughly -30% to -55% in history.",
h1:"Today’s positions (daily close up to ",coin:"Coin",price:"Price",tr:"Trend",up:"Above 200-day average",dn:"Below 200-day average",
cand:"Ensemble + vol-target (candidate)",risk:"Risk level (volatility target):",inn:"In",out:"Out",h2:"Growth of $100 (log scale)",live:"Live starts",
cn:"Before the ‘live’ line this is a historical simulation (rules fixed in advance, data from the past). Only what comes after the line is forward evidence.",
h3:"Statistics",s:"Strategy",sh:"Sharpe",an:"Annual",dd:"Max drawdown",exp:"In market",lv:"Live (days / return / drawdown)",
nt:"Historical simulation: 8 coins equal weight, fees and slippage included. The coins are today’s well-known ones (survivorship bias), so history looks better than reality. The candidate had smaller drawdowns than Buy & Hold, but that is no guarantee for the future. * The 15% level is outside the stress-tested range (25–60%); drawdowns are expected to be smaller, but this was not tested."}};
let lg="am";try{lg=localStorage.getItem("lg")||"am"}catch(e){}
let lv=25;try{lv=+localStorage.getItem("lv")||25}catch(e){}
const ks=()=>["bh","don","ema","c"+lv];
const NM=T=>({bh:"Buy & Hold",don:"Donchian 55/20",ema:"EMA 50/200",c15:T.cand+" 15%*",c25:T.cand+" 25%",c40:T.cand+" 40%"}),COL={bh:"var(--mu)",don:"#b7791f",ema:"#8b5cf6",c15:"var(--b)",c25:"var(--b)",c40:"var(--b)"};
const pc=(x,d=0)=>x==null?"–":(x*100).toFixed(d)+"%";
function render(){const T=I[lg],N=NM(T);
 document.documentElement.lang=lg;$("#h").textContent=T.h;document.title=T.h;$("#lg").textContent=lg==="am"?"English":"አማርኛ";
 $("#ck").textContent=T.chk+D.checked;$("#ex").textContent=T.ex;$("#hr").textContent=T.hr;$("#wn").innerHTML=T.warn;
 $("#h1").textContent=T.h1+D.asof+")";
 const pw=(w,k)=>k==="c"?pc(w,0):w>0?T.inn:T.out;
 $("#pos").innerHTML=`<table><thead><tr><th>${T.coin}</th><th>${T.price}</th><th>${T.tr}</th><th>${N["c"+lv]}</th><th>Donchian</th><th>EMA</th></tr></thead><tbody>${Object.entries(D.coins).map(([s,m])=>`<tr><td>${s.replace("USDT","")}</td><td>${Number(m.price).toLocaleString("en-US",{maximumFractionDigits:4})}</td><td class="${m.up?"g":"r"}">${m.up==null?"–":m.up?"↑ "+T.up:"↓ "+T.dn}</td><td>${pw(m.w["c"+lv],"c")}</td><td>${pw(m.w.don)}</td><td>${pw(m.w.ema)}</td></tr>`).join("")}</tbody></table>`;
 $("#h2").textContent=T.h2;$("#lvw").innerHTML=`<div class="mu" style="margin-bottom:6px">${T.risk} ${[15,25,40].map(x=>`<button class="lv" data-x="${x}" aria-pressed="${x===lv}">${x}%</button>`).join(" ")}</div>`;document.querySelectorAll(".lv").forEach(b=>b.onclick=()=>{lv=+b.dataset.x;try{localStorage.setItem("lv",lv)}catch(e){}render()});
 const all=ks().flatMap(k=>D.eq[k]),lo=Math.log(Math.min(...all)),hi=Math.log(Math.max(...all)),n=D.dates.length,X=i=>(i/(n-1)*600).toFixed(1),Y=v=>(205-(Math.log(v)-lo)/(hi-lo||1)*195).toFixed(1);
 const fi=D.dates.findIndex(d=>d>=D.live_start),li=fi<0?n-1:fi;
 $("#chart").innerHTML=`<svg viewBox="0 0 600 215" role="img" aria-label="${T.h2}">${ks().map(k=>`<polyline fill="none" stroke="${COL[k]}" stroke-width="${k[0]==="c"?2.4:1.4}" points="${D.eq[k].map((v,i)=>X(i)+","+Y(v)).join(" ")}"/>`).join("")}<line x1="${X(li)}" x2="${X(li)}" y1="0" y2="215" stroke="var(--tx)" stroke-dasharray="4 4"/><text x="${Math.min(+X(li)+4,520)}" y="12" font-size="11" fill="var(--tx)">${T.live}</text></svg><div class="lg">${ks().map(k=>`<span><i style="background:${COL[k]}"></i>${N[k]}</span>`).join("")}</div>`;
 $("#cn").textContent=T.cn;$("#h3").textContent=T.h3;
 $("#st").innerHTML=`<table><thead><tr><th>${T.s}</th><th>${T.sh}</th><th>${T.an}</th><th>${T.dd}</th><th>${T.exp}</th><th>${T.lv}</th></tr></thead><tbody>${ALL.map(k=>{const s=D.stats[k],h=s.hist||{};return `<tr><td>${N[k]}</td><td>${h.sh??"–"}</td><td>${pc(h.ann)}</td><td>${pc(h.dd)}</td><td>${pc(s.exp)}</td><td>${s.live.days} / ${pc(s.live.ret,1)} / ${pc(s.live.dd,1)}</td></tr>`}).join("")}</tbody></table>`;
 $("#nt").textContent=T.nt}
$("#lg").onclick=()=>{lg=lg==="am"?"en":"am";try{localStorage.setItem("lg",lg)}catch(e){}render()};
render();
</script></body></html>"""

if __name__ == "__main__":
    run()

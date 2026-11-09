"""
hourly_tracker.py - S2 (Hourly Donchian 72/24) ቀጥታ paper tracker -> docs/hourly.html
ማሄድ: python hourly_tracker.py   (በየሰዓቱ ይሮጣል፤ ሁኔታውን data/hourly.json ውስጥ ያስቀምጣል)
ሙከራ: python hourly_tracker.py demo
ሕጉ ከ intraday_lab_v2 ጋር አንድ ነው: ከ72 ሰዓት ከፍተኛ በላይ + ከ720 ሰዓት SMA በላይ ሲዘጋ ግዛ፣ ከ24 ሰዓት ዝቅተኛ በታች ሲዘጋ ሽጥ፣
ቦታው በ vol-target (40%) ይመዘናል፣ ከ0.25 በታች ለውጥ አይገበያይም። 8 ሳንቲሞች እኩል ክብደት።
"""
import json, os, sys, time
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
DATA = os.path.join(DIR, "data", "hourly_demo.json" if DEMO else "hourly.json")
SITE = os.path.join(DIR, "docs", "hourly_demo.html" if DEMO else "hourly.html")
os.makedirs(os.path.dirname(DATA), exist_ok=True)
os.makedirs(os.path.dirname(SITE), exist_ok=True)
bt.INTERVAL = "1h"
WIN = 1200


def fmt(t):
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M")


def fetch_window(sym):
    if DEMO:
        k, n = COINS.index(sym), WIN + int(os.environ.get("HT_EXTRA", "0"))
        c = 100 * np.exp(np.cumsum(np.random.default_rng(k).normal(0.0003, 0.01, n)))
        z = np.abs(np.random.default_rng(k + 100).normal(0, 0.003, n))
        return pd.DataFrame(dict(time=pd.date_range("2026-01-01", periods=n, freq="h"), open=c, high=c * (1 + z),
                                 low=c * (1 - z), close=c, volume=1.0))
    bt.SYMBOL = sym
    end = int(time.time() * 1000)
    d = bt.download_klines(end - WIN * 3600 * 1000, end)
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    return d[d["time"] + pd.Timedelta(hours=1) <= now].reset_index(drop=True)


def prep(d):
    c, h, l = d["close"], d["high"], d["low"]
    sma = c.rolling(720).mean()
    up = ((c > h.rolling(72).max().shift(1)) & (c > sma)).values
    dn = (c < l.rolling(24).min().shift(1)).values
    rv = c.pct_change().rolling(168).std() * np.sqrt(8760)
    return up, dn, ((0.40 / rv).clip(upper=1).fillna(0)).values, (~sma.isna()).values


def step(st, up, dn, v):
    if st["latch"] == 0 and up:
        st["latch"] = 1
    elif st["latch"] == 1 and dn:
        st["latch"] = 0
    x = st["latch"] * float(v)
    if abs(x - st["pos"]) > 0.25 or (x == 0 and st["pos"] != 0):
        st["pos"] = x


def run():
    db = json.load(open(DATA, encoding="utf-8")) if os.path.exists(DATA) else {}
    frames = {}
    for s in COINS:
        try:
            frames[s] = fetch_window(s)
        except Exception as ex:
            print(f"{s} አልወረደም ({ex})፤ በሚቀጥለው ሰዓት እንደገና ይሞክራል")
            return
    first = "coins" not in db
    db.setdefault("coins", {})
    db.setdefault("trades", [])
    ev = {}
    alerts = []
    for s, d in frames.items():
        up, dn, v, ok = prep(d)
        c, times = d["close"].values, d["time"]
        st = db["coins"].setdefault(s, dict(latch=0, pos=0.0, held_prev=0.0, last=None, in_trade=False, tr=1.0,
                                           entry_time=None, entry_price=None))
        start = 1 if st["last"] is None else int(np.searchsorted(times.values, pd.Timestamp(st["last"]).to_datetime64(), side="right"))
        for i in range(max(start, 1), len(d)):
            held = st["pos"]
            if not first:
                ret = c[i] / c[i - 1] - 1
                r = held * ret - abs(held - st["held_prev"]) * COST
                ev.setdefault(times[i], []).append((r, ret))
                if held > 0 and not st["in_trade"]:
                    st.update(in_trade=True, tr=1.0, entry_time=fmt(times[i]), entry_price=round(float(c[i - 1]), 6))
                    alerts.append(f"🟢 {s.replace('USDT', '')}: ንግድ ተከፈተ · ቦታ {held*100:.0f}% · ዋጋ {c[i - 1]:g}")
                if st["in_trade"]:
                    st["tr"] *= 1 + r
                    if held == 0:
                        db["trades"].append(dict(s=s, e=st["entry_time"], x=fmt(times[i]), r=round(st["tr"] - 1, 4)))
                        alerts.append(f"{'✅' if st['tr'] > 1 else '🔴'} {s.replace('USDT', '')}: ንግድ ተዘጋ · {(st['tr'] - 1)*100:+.1f}% · ከ {st['entry_time']}")
                        st.update(in_trade=False, tr=1.0, entry_time=None, entry_price=None)
            st["held_prev"] = held
            if ok[i]:
                step(st, up[i], dn[i], v[i])
        st["last"] = fmt(times.iloc[-1]) if False else str(times.iloc[-1])
        st["price"] = round(float(c[-1]), 6)
    if first:
        db["live_start"] = fmt(max(pd.Timestamp(x["last"]) for x in db["coins"].values()))
        db["eq"] = dict(t=[db["live_start"]], s2=[100.0], bh=[100.0])
    for t in sorted(ev):
        a = ev[t]
        db["eq"]["t"].append(fmt(t))
        db["eq"]["s2"].append(round(db["eq"]["s2"][-1] * (1 + np.mean([x[0] for x in a])), 4))
        db["eq"]["bh"].append(round(db["eq"]["bh"][-1] * (1 + np.mean([x[1] for x in a])), 4))
    for k in db["eq"]:
        db["eq"][k] = db["eq"][k][-20000:]
    db["checked"] = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC")
    json.dump(db, open(DATA, "w", encoding="utf-8"), ensure_ascii=False)
    with open(SITE, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("%%DATA%%", json.dumps(db, ensure_ascii=False).replace("</", "<\\/")))
    if alerts and not first:
        notify("⏱ 1 ሰዓት S2 (paper)\n" + "\n".join(alerts[:15]))
    print(f"[{db['checked']}] ሰዓታት (live): {len(db['eq']['t']) - 1} | ንግዶች: {len(db['trades'])} | live ከ {db['live_start']}")


PAGE = r"""<!doctype html><html lang="am"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Hourly S2 tracker</title>
<style>
:root{--bg:#eef1f4;--card:#fff;--tx:#101828;--mu:#5b6574;--ln:#d5dbe3;--g:#0f7b55;--r:#c0392b;--b:#1f4fd8;--w:#fff4d6}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--tx:#e6e9ef;--mu:#8b95a5;--ln:#262d38;--g:#3ecf8e;--r:#ff7066;--b:#7aa2ff;--w:#2a2410}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.55 "Ebrima","Nyala","Segoe UI",system-ui,sans-serif;padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}
main{max-width:900px;margin:0 auto;padding:20px 16px 48px}h1{font-size:1.5rem;margin:0}h2{font-size:1.15rem;margin:26px 0 8px}a{color:var(--b)}
.top{display:flex;justify-content:space-between;align-items:center;gap:10px}.mu{color:var(--mu);font-size:.92rem}.g{color:var(--g)}.r{color:var(--r)}
button{font:inherit;background:transparent;color:var(--tx);border:1px solid var(--ln);border-radius:6px;padding:6px 12px;cursor:pointer}button:focus-visible{outline:3px solid var(--b);outline-offset:2px}
.warn{background:var(--w);border:1px solid var(--ln);border-radius:6px;padding:10px 12px;margin:14px 0;font-size:.92rem}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--ln);white-space:nowrap}th:first-child,td:first-child{text-align:left}th{color:var(--mu);font-weight:400;font-size:.88rem}
.ch{background:var(--card);border:1px solid var(--ln);border-radius:8px;padding:10px}svg{width:100%;height:auto;display:block}.sm{display:flex;gap:22px;flex-wrap:wrap;margin:10px 0}.sm b{font-size:1.25rem;display:block}
</style></head><body><main>
<div class="top"><h1 id="h"></h1><button id="lg" type="button" aria-label="Language"></button></div>
<div class="mu" id="ck"></div><div class="mu"><a href="./" id="l1"></a> · <a href="experimental.html" id="l2"></a> · <a href="copylog.html" id="l3"></a></div>
<div class="warn" id="wn"></div><div class="sm" id="sm"></div>
<h2 id="h1"></h2><div class="tw" id="pos"></div>
<h2 id="h2"></h2><div class="ch" id="chart"></div>
<h2 id="h3"></h2><div class="tw" id="tr"></div>
<h2 id="h4"></h2><div class="tw" id="hs"></div><div class="mu" id="nt"></div>
</main><script>
const D=%%DATA%%,$=q=>document.querySelector(q);
const I={am:{h:"1 ሰዓት S2 መከታተያ (እጩ)",chk:"ተፈትሿል ",l1:"← ዋና ገጽ (ዕለታዊ)",l2:"ሙከራ: 4 ሰዓት ሲግናሎች",l3:"ዲሞ ኮፒ ምዝግብ",warn:"<b>ይህ paper trading እና ምርምር ነው።</b> ምናባዊ ገንዘብ ብቻ። ስትራቴጂው እጩ እንጂ የተረጋገጠ አይደለም፣ ብዙ ጊዜ ይሸነፋል (ከ3 ንግዶች 2ቱ ያህል)፣ እና በታሪክ ከ-20% እስከ -35% ኪሳራ አሳይቷል። የገንዘብ ምክር አይደለም።",
live:"ቀጥታ ከ",hrs:"ሰዓታት",s2:"S2 እድገት",bh:"Buy & Hold",tn:"የተዘጉ ንግዶች",wr:"Win rate",pf:"Profit factor",
h1:"የአሁን ቦታዎች",coin:"ሳንቲም",price:"ዋጋ",state:"ሁኔታ",inn:"ውስጥ",out:"ውጭ",open:"የአሁኑ ንግድ",since:"ከ",
h2:"የ$100 እድገት (ቀጥታ)",few:"ገና ብዙ ዳታ የለም፤ ከጥቂት ሰዓታት በኋላ ይታያል።",h3:"የተዘጉ ንግዶች (የቅርብ 40)",entry:"መግቢያ",exit:"መውጫ",ret:"ተመላሽ",res:"ውጤት",pw:"ትርፍ ✅",pl:"ኪሳራ 🔴",none:"እስካሁን የተዘጋ ንግድ የለም።",
h4:"ታሪካዊ ማስመሰል (6 ዓመት፣ ከክፍያ በኋላ)",grp:"ሳንቲሞች",sh:"Sharpe",an:"ዓመታዊ",dd:"ትልቁ ኪሳራ",wn:"Win",pay:"ትርፍ÷ኪሳራ",
nt:"ታሪካዊ ማስመሰል ከዚህ በፊት በተደረጉ ፈተናዎች ውጤት ነው፤ ሕጉ ቀድሞ የተወሰነ ነው። የንግድ ብዛት በቂ እስኪሆን (100+) ቀጥታ ቁጥሮቹን አትመኑ። በመጀመሪያው ሰዓት የተከፈቱ ቦታዎች ከቀጥታው መጀመሪያ ጀምሮ ይቆጠራሉ።"},
en:{h:"Hourly S2 tracker (candidate)",chk:"Last checked ",l1:"← Main page (daily)",l2:"Experimental: 4-hour signals",l3:"Demo copy log",warn:"<b>This is paper trading and research.</b> Virtual money only. The strategy is a candidate, not proven; it loses often (about 2 of 3 trades) and has had historical drawdowns of -20% to -35%. Not financial advice.",
live:"Live since",hrs:"hours",s2:"S2 growth",bh:"Buy & Hold",tn:"Closed trades",wr:"Win rate",pf:"Profit factor",
h1:"Current positions",coin:"Coin",price:"Price",state:"State",inn:"In",out:"Out",open:"Open trade",since:"since",
h2:"Growth of $100 (live)",few:"Not enough data yet; it will appear after a few hours.",h3:"Closed trades (latest 40)",entry:"Entry",exit:"Exit",ret:"Return",res:"Result",pw:"Profit ✅",pl:"Loss 🔴",none:"No closed trades yet.",
h4:"Historical simulation (6 years, after costs)",grp:"Coins",sh:"Sharpe",an:"Annual",dd:"Max drawdown",wn:"Win",pay:"Win÷loss",
nt:"The historical simulation comes from earlier tests; the rules were fixed in advance. Do not trust live numbers before 100+ trades. Positions already open in the first hour count from the start of the live period."}};
const HS=[["BTC ETH BNB SOL",1.14,.182,-.231,.37,2.53,1.49],["XRP ADA DOGE LINK",.90,.144,-.343,.34,2.85,1.45]];
let lg="am";try{lg=localStorage.getItem("lg")||"am"}catch(e){}
const pc=(x,d=1)=>(x*100).toFixed(d)+"%",f=x=>Number(x).toLocaleString("en-US",{maximumFractionDigits:6});
function render(){const T=I[lg];document.documentElement.lang=lg;document.title=T.h;
 $("#h").textContent=T.h;$("#lg").textContent=lg==="am"?"English":"አማርኛ";$("#ck").textContent=T.chk+D.checked;$("#l1").textContent=T.l1;$("#l2").textContent=T.l2;$("#l3").textContent=T.l3;$("#wn").innerHTML=T.warn;
 const e=D.eq,n=e.t.length,s=e.s2[n-1]/100-1,b=e.bh[n-1]/100-1,tr=D.trades,w=tr.filter(x=>x.r>0),ws=w.reduce((a,x)=>a+x.r,0),ls=-tr.filter(x=>x.r<=0).reduce((a,x)=>a+x.r,0);
 const c=(x)=>`<span class="${x>=0?"g":"r"}">${x>=0?"+":""}${pc(x)}</span>`;
 $("#sm").innerHTML=`<div class="mu">${T.live}<b>${D.live_start}</b>${n-1} ${T.hrs}</div><div class="mu">${T.s2}<b>${c(s)}</b></div><div class="mu">${T.bh}<b>${c(b)}</b></div><div class="mu">${T.tn}<b>${tr.length} / 100</b></div><div class="mu">${T.wr}<b>${tr.length?pc(w.length/tr.length,0):"–"}</b></div><div class="mu">${T.pf}<b>${ls?(ws/ls).toFixed(2):"–"}</b></div>`;
 $("#h1").textContent=T.h1;
 $("#pos").innerHTML=`<table><thead><tr><th>${T.coin}</th><th>${T.price}</th><th>${T.state}</th><th>${T.open}</th></tr></thead><tbody>${Object.entries(D.coins).map(([k,m])=>`<tr><td>${k.replace("USDT","")}</td><td>${f(m.price)}</td><td>${m.pos>0?T.inn+" "+pc(m.pos,0):T.out}</td><td>${m.in_trade?c(m.tr-1)+" "+T.since+" "+m.entry_time:"–"}</td></tr>`).join("")}</tbody></table>`;
 $("#h2").textContent=T.h2;
 if(n<3)$("#chart").innerHTML=`<div class="mu">${T.few}</div>`;else{const all=[...e.s2,...e.bh],lo=Math.min(...all),hi=Math.max(...all),X=i=>(i/(n-1)*600).toFixed(1),Y=v=>(205-(v-lo)/(hi-lo||1)*195).toFixed(1),P=a=>a.map((v,i)=>X(i)+","+Y(v)).join(" ");
  $("#chart").innerHTML=`<svg viewBox="0 0 600 215" role="img" aria-label="${T.h2}"><polyline fill="none" stroke="var(--mu)" stroke-width="1.4" points="${P(e.bh)}"/><polyline fill="none" stroke="var(--b)" stroke-width="2.4" points="${P(e.s2)}"/></svg><div class="mu">S2 (—) · Buy & Hold (grey) · ${e.t[0]} → ${e.t[n-1]}</div>`}
 $("#h3").textContent=T.h3;
 $("#tr").innerHTML=tr.length?`<table><thead><tr><th>${T.coin}</th><th>${T.entry}</th><th>${T.exit}</th><th>${T.ret}</th><th>${T.res}</th></tr></thead><tbody>${[...tr].reverse().slice(0,40).map(x=>`<tr><td>${x.s.replace("USDT","")}</td><td>${x.e}</td><td>${x.x}</td><td>${c(x.r)}</td><td class="${x.r>0?"g":"r"}">${x.r>0?T.pw:T.pl}</td></tr>`).join("")}</tbody></table>`:`<div class="mu">${T.none}</div>`;
 $("#h4").textContent=T.h4;
 $("#hs").innerHTML=`<table><thead><tr><th>${T.grp}</th><th>${T.sh}</th><th>${T.an}</th><th>${T.dd}</th><th>${T.wn}</th><th>${T.pay}</th><th>PF</th></tr></thead><tbody>${HS.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td><td>${pc(r[2])}</td><td>${pc(r[3])}</td><td>${pc(r[4],0)}</td><td>${r[5]}</td><td>${r[6]}</td></tr>`).join("")}</tbody></table>`;
 $("#nt").textContent=T.nt}
$("#lg").onclick=()=>{lg=lg==="am"?"en":"am";try{localStorage.setItem("lg",lg)}catch(e){}render()};
render();
</script></body></html>"""

if __name__ == "__main__":
    run()

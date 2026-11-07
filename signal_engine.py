"""
signal_engine.py - ቀጥታ (paper) ሲግናል ሞተር + ድረ ገጽ ጀነሬተር
ማሄድ:        python signal_engine.py          (አንድ ጊዜ ይፈትሻል)
ሁልጊዜ ማሄድ:  python signal_engine.py loop     (በየ15 ደቂቃ ይፈትሻል፤ መስኮቱን አትዝጋ)
ሙከራ:        python signal_engine.py demo     (ሰው ሰራሽ ዳታ፣ ኢንተርኔት አያስፈልግም)
ድረ ገጹ:      docs\\index.html ን በ browser ክፈት
btc_backtest.py በዚሁ ፎልደር ውስጥ መኖር አለበት።
"""
import json, os, sys, time
import pandas as pd
import btc_backtest as bt

DEMO = "demo" in sys.argv
DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DIR, "data", "signals_demo.json" if DEMO else "signals.json")
SITE = os.path.join(DIR, "docs", "demo.html" if DEMO else "index.html")
os.makedirs(os.path.dirname(DATA), exist_ok=True)
os.makedirs(os.path.dirname(SITE), exist_ok=True)


def fmt(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M")


def load():
    if os.path.exists(DATA):
        with open(DATA, encoding="utf-8") as f:
            return json.load(f)
    return {"signals": []}


def candles():
    bt.YEARS = 1
    df = bt.demo_data(600) if DEMO else bt.download_klines()
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    if not DEMO and df["time"].iloc[-1] + pd.Timedelta(hours=4) > now:
        df = df.iloc[:-1]  # ያልተዘጋ ሻማ አንጠቀምም
    return bt.add_indicators(df.reset_index(drop=True))


def review(s):
    p, n = s["snap"], s["candles"]
    stop = abs(s["entry"] - s["sl"]) / s["entry"] * 100
    tags = []
    if s["reason"] == "TP":
        tags.append("ወደ target ደረሰ")
    if s["reason"] == "TIME":
        tags.append("በጊዜ ወጣ")
    if s["reason"] == "SL":
        if n <= 2:
            tags.append("ፈጣን stop-out")
        if stop < 1.6:
            tags.append("ጠባብ stop")
        if abs(p["rsi"] - (40 if s["side"] == "LONG" else 60)) > 5:
            tags.append("ማሳደድ")
    return tags


def close_signal(s, reason, px, t, k):
    d = 1 if s["side"] == "LONG" else -1
    px = px * (1 - bt.SLIPPAGE * d)
    risk = abs(s["entry"] - s["sl"])
    r = ((px - s["entry"]) * d - (s["entry"] + px) * bt.FEE) / risk
    s.update(status="WIN" if r > 0 else "LOSS", exit=round(px, 2), reason=reason,
             R=round(r, 3), closed=fmt(t), candles=k)
    s["tags"] = review(s)


def update_open(db, df):
    for s in db["signals"]:
        if s["status"] != "OPEN":
            continue
        d = 1 if s["side"] == "LONG" else -1
        fut = df[df["time"] > pd.Timestamp(s["time"])].reset_index(drop=True)
        for k, row in fut.iterrows():
            sl = row["low"] <= s["sl"] if d == 1 else row["high"] >= s["sl"]
            tp = row["high"] >= s["tp"] if d == 1 else row["low"] <= s["tp"]
            out = "SL" if sl else "TP" if tp else "TIME" if k + 1 >= bt.MAX_HOLD else None
            if out:
                px = s["sl"] if out == "SL" else s["tp"] if out == "TP" else row["close"]
                close_signal(s, out, px, row["time"], k + 1)
                break


def new_signal(db, df):
    i = len(df) - 1
    ls, ss = bt.find_signals(df)
    side = "LONG" if ls.iloc[i] else "SHORT" if ss.iloc[i] else None
    r = df.iloc[i]
    sid = fmt(r["time"])
    busy = any(s["status"] == "OPEN" or s["time"] == sid for s in db["signals"])
    if not side or busy:
        return
    d = 1 if side == "LONG" else -1
    entry = r["close"] * (1 + bt.SLIPPAGE * d)
    db["signals"].append(dict(
        id=sid, time=sid, side=side, strategy="v1", status="OPEN",
        entry=round(entry, 2), sl=round(entry - d * bt.SL_ATR * r["atr"], 2),
        tp=round(entry + d * bt.TP_ATR * r["atr"], 2),
        snap=dict(rsi=round(r["rsi"], 1), atr=round(r["atr"], 1), ema200=round(r["ema200"], 1),
                  volx=round(r["volume"] / r["vol_ma"], 2),
                  trend="ከ EMA200 በላይ" if r["close"] > r["ema200"] else "ከ EMA200 በታች")))


def run():
    db = load()
    df = candles()
    update_open(db, df)
    new_signal(db, df)
    r = df.iloc[-1]
    db["market"] = dict(symbol=bt.SYMBOL, price=round(r["close"], 2), candle=fmt(r["time"]),
                        rsi=round(r["rsi"], 1),
                        trend="ከ EMA200 በላይ" if r["close"] > r["ema200"] else "ከ EMA200 በታች",
                        checked=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"))
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    payload = json.dumps(db, ensure_ascii=False).replace("</", "<\\/")
    with open(SITE, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("%%DATA%%", payload))
    o = [s for s in db["signals"] if s["status"] == "OPEN"]
    print(f"[{db['market']['checked']}] {bt.SYMBOL} {db['market']['price']} | "
          f"ክፍት ሲግናል: {len(o)} | ጠቅላላ: {len(db['signals'])} | ድረ ገጽ: {SITE}")


PAGE = r"""<!doctype html><html lang="am"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>BTC ሲግናሎች (paper trading)</title>
<style>
:root{--bg:#eef1f4;--card:#fff;--tx:#101828;--mu:#5b6574;--ln:#d5dbe3;--g:#0f7b55;--r:#c0392b;--b:#1f4fd8;--w:#fff4d6}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--tx:#e6e9ef;--mu:#8b95a5;--ln:#262d38;--g:#3ecf8e;--r:#ff7066;--b:#7aa2ff;--w:#2a2410}}
*{box-sizing:border-box}html{scroll-padding-top:env(safe-area-inset-top,0px)}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.55 "Ebrima","Nyala","Segoe UI",system-ui,sans-serif;padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}
main{max-width:860px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.5rem;margin:0 0 2px}h2{font-size:1.15rem;margin:0}
.mu{color:var(--mu);font-size:.92rem}.g{color:var(--g)}.r{color:var(--r)}
.warn{background:var(--w);border:1px solid var(--ln);border-radius:6px;padding:10px 12px;margin:14px 0;font-size:.92rem}
.sig{display:grid;grid-template-columns:150px 1fr;gap:20px;background:var(--card);border:1px solid var(--ln);border-radius:10px;padding:18px;margin:16px 0}
.lad{position:relative;height:240px;border-left:2px solid var(--ln);margin-left:70px}
.lad i{position:absolute;left:-2px;width:46px;border-top:2px solid var(--tx);font-style:normal;font-size:.8rem;line-height:1}
.lad i b{position:absolute;right:52px;top:-8px;white-space:nowrap;font-weight:600}
.lad .tp{border-color:var(--g)}.lad .sl{border-color:var(--r)}
.lad .px{border-color:var(--b);width:64px;border-top-style:dashed}
.row{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--ln);padding:7px 0}
button{font:inherit;background:var(--b);color:#fff;border:0;border-radius:6px;padding:9px 14px;margin-top:12px;cursor:pointer}
button:focus-visible,summary:focus-visible{outline:3px solid var(--b);outline-offset:2px}
.empty{background:var(--card);border:1px dashed var(--ln);border-radius:10px;padding:18px;margin:16px 0}
dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:14px 20px;margin:18px 0}
dt{color:var(--mu);font-size:.85rem}dd{margin:0;font-size:1.3rem;font-weight:600}
.tabs{display:flex;gap:8px;margin:18px 0 6px}.tabs button{background:transparent;color:var(--tx);border:1px solid var(--ln);margin:0;padding:6px 12px}
.tabs button[aria-pressed=true]{background:var(--tx);color:var(--bg)}
details{background:var(--card);border:1px solid var(--ln);border-radius:8px;margin:6px 0;padding:10px 12px}
summary{cursor:pointer;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.tag{display:inline-block;border:1px solid var(--ln);border-radius:20px;padding:0 9px;font-size:.82rem;margin:6px 6px 0 0}
@media(max-width:560px){.sig{grid-template-columns:1fr}.lad{margin-left:80px}}
</style></head><body><main>
<h1>BTC ሲግናሎች</h1><div class="mu" id="mk"></div>
<div class="warn"><b>ይህ paper trading ነው።</b> ምናባዊ ገንዘብ ብቻ፤ ስትራቴጂው አሁንም እየተፈተነ ነው፣ ትርፋማነቱ አልተረጋገጠም። የገንዘብ ምክር አይደለም፣ በእውነተኛ ገንዘብ አትገበያይበት።</div>
<div id="cur"></div>
<dl id="st"></dl><div class="mu" id="small"></div>
<h2>ያለፉ ሲግናሎች</h2>
<div class="tabs" id="tabs"></div><div id="list"></div>
</main><script>
const D=%%DATA%%,S=D.signals,M=D.market||{},$=q=>document.querySelector(q);
const f=x=>x==null?"–":Number(x).toLocaleString("en-US",{maximumFractionDigits:2});
const op=S.find(s=>s.status==="OPEN"),dn=S.filter(s=>s.status!=="OPEN").sort((a,b)=>b.closed.localeCompare(a.closed));
$("#mk").textContent=`${M.symbol||""} ${f(M.price)} · ${M.trend||""} · RSI ${M.rsi??"–"} · ተፈትሿል ${M.checked||"–"}`;
if(op){const v=[op.sl,op.tp,op.entry,M.price],hi=Math.max(...v),lo=Math.min(...v),pd=(hi-lo)*.08,H=hi+pd,L=lo-pd,y=x=>(H-x)/(H-L)*100;
 const m=(c,x,t)=>`<i class="${c}" style="top:${y(x)}%"><b>${t} ${f(x)}</b></i>`;
 const pr=Math.abs(op.entry-op.sl)/op.entry*100,rw=Math.abs(op.tp-op.entry)/op.entry*100;
 $("#cur").innerHTML=`<section class="sig"><div class="lad">${m("tp",op.tp,"TP")}${m("",op.entry,"entry")}${m("sl",op.sl,"SL")}${m("px",M.price,"አሁን")}</div>
 <div><h2 class="${op.side==="LONG"?"g":"r"}">${op.side} · ክፍት</h2><div class="mu">${op.time} UTC ሻማ መዝጊያ</div>
 <div class="row"><span>Entry</span><b>${f(op.entry)}</b></div><div class="row"><span>Stop-loss (−${pr.toFixed(2)}%)</span><b class="r">${f(op.sl)}</b></div>
 <div class="row"><span>Take-profit (+${rw.toFixed(2)}%)</span><b class="g">${f(op.tp)}</b></div>
 <div class="row"><span>RSI / ድምጽ / አዝማሚያ</span><span>${op.snap.rsi} / ${op.snap.volx}× / ${op.snap.trend}</span></div>
 <button id="cp">ሲግናሉን ኮፒ አድርግ</button> <span class="mu" id="cm"></span></div></section>`;
 $("#cp").onclick=()=>{navigator.clipboard.writeText(`${M.symbol} ${op.side}\nEntry ${op.entry}\nSL ${op.sl}\nTP ${op.tp}`).then(()=>$("#cm").textContent="ኮፒ ተደርጓል")}}
else $("#cur").innerHTML=`<div class="empty"><h2>አሁን ሲግናል የለም</h2><p class="mu">ሲስተሙ ሻማ ሲዘጋ (በየ4 ሰዓት) ይፈትሻል። ሁኔታዎቹ ካልተሟሉ ሲግናል አይሰጥም፤ ይህ የስትራቴጂው አካል ነው።</p></div>`;
const R=dn.map(s=>s.R),w=R.filter(x=>x>0).reduce((a,b)=>a+b,0),l=-R.filter(x=>x<=0).reduce((a,b)=>a+b,0);
let e=1,pk=1,dd=0;[...dn].reverse().forEach(s=>{e*=1+.01*s.R;pk=Math.max(pk,e);dd=Math.min(dd,e/pk-1)});
const it=[["ንግዶች",dn.length],["Win rate",dn.length?(R.filter(x=>x>0).length/dn.length*100).toFixed(0)+"%":"–"],["Profit factor",l?(w/l).toFixed(2):"–"],["አማካይ R",dn.length?(R.reduce((a,b)=>a+b,0)/dn.length).toFixed(2):"–"],["Max drawdown",(dd*100).toFixed(1)+"%"]];
$("#st").innerHTML=it.map(([a,b])=>`<div><dt>${a}</dt><dd>${b}</dd></div>`).join("");
if(dn.length<100)$("#small").textContent=`ናሙናው ትንሽ ነው (${dn.length}/100)። ቁጥሮቹን ከ100 ንግድ በፊት አትመኑ።`;
let tab="all";
function draw(){const a=dn.filter(s=>tab==="all"||(tab==="win"?s.status==="WIN":s.status==="LOSS"));
 $("#list").innerHTML=a.length?a.map(s=>`<details><summary><span><b class="${s.status==="WIN"?"g":"r"}">${s.status==="WIN"?"ትርፍ":"ኪሳራ"}</b> · ${s.side} · ${s.closed}</span><b>${s.R>0?"+":""}${s.R}R</b></summary>
 <div class="mu">Entry ${f(s.entry)} → ${f(s.exit)} (${s.reason}፣ ${s.candles} ሻማ) · RSI ${s.snap.rsi} · ${s.snap.trend}</div>
 ${s.tags.map(t=>`<span class="tag">${t}</span>`).join("")}
 <p class="mu">መለያዎቹ የስታቲስቲክስ ጥቆማዎች ናቸው፣ ምክንያቱን አያረጋግጡም።</p></details>`).join(""):`<p class="mu">እስካሁን የተዘጋ ሲግናል የለም።</p>`;
 $("#tabs").innerHTML=[["all","ሁሉም"],["win","ትርፍ"],["loss","ኪሳራ"]].map(([k,t])=>`<button data-k="${k}" aria-pressed="${tab===k}">${t}</button>`).join("");
 document.querySelectorAll("#tabs button").forEach(b=>b.onclick=()=>{tab=b.dataset.k;draw()})}
draw();
</script></body></html>"""

if __name__ == "__main__":
    if "loop" in sys.argv:
        while True:
            try:
                run()
            except Exception as ex:
                print("ስህተት (ቀጥሎ እንደገና ይሞክራል):", ex)
            time.sleep(900)
    else:
        run()

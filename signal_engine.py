"""
signal_engine.py (ስሪት 2) - 4 ስትራቴጂዎች x 3 ገበያዎች በትይዩ (paper trading)
ማሄድ:  python signal_engine.py        (አንድ ጊዜ ይፈትሻል)
       python signal_engine.py loop    (በየ15 ደቂቃ)
       python signal_engine.py demo    (ሰው ሰራሽ ዳታ)
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
MARKETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
BASE_FIND = bt.find_signals


def nochase(d):
    return d["rsi"] < 45, d["rsi"] > 55


def nottight(d):
    ok = 1.5 * d["atr"] / d["close"] * 100 >= 1.6
    return ok, ok


TRACKS = {
    "v1": dict(label="v1 መነሻ", sl=1.5, tp=2.5, rule=None),
    "H1": dict(label="H1 ሰፊ stop", sl=2.5, tp=4.0, rule=None),
    "H2": dict(label="H2 ማሳደድ የለም", sl=1.5, tp=2.5, rule=nochase),
    "H3": dict(label="H3 ጠባብ stop ዝለል", sl=1.5, tp=2.5, rule=nottight),
}


def fmt(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M")


def trend(r):
    return "ከ EMA200 በላይ" if r["close"] > r["ema200"] else "ከ EMA200 በታች"


def load():
    if os.path.exists(DATA):
        with open(DATA, encoding="utf-8") as f:
            return json.load(f)
    return {"signals": []}


def candles(sym):
    bt.YEARS = 1
    bt.SYMBOL = sym
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
    s.update(status="WIN" if r > 0 else "LOSS", exit=round(px, 4), reason=reason,
             R=round(r, 3), closed=fmt(t), candles=k)
    s["tags"] = review(s)


def update_open(db, df, sym):
    for s in db["signals"]:
        if s["status"] != "OPEN" or s.get("symbol") != sym:
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


def new_signal(db, df, sym, key):
    cfg = TRACKS[key]
    ls, ss = BASE_FIND(df)
    if cfg["rule"]:
        okl, oks = cfg["rule"](df)
        ls, ss = ls & okl, ss & oks
    i = len(df) - 1
    side = "LONG" if ls.iloc[i] else "SHORT" if ss.iloc[i] else None
    r = df.iloc[i]
    t = fmt(r["time"])
    busy = any(s.get("symbol") == sym and s.get("track") == key and (s["status"] == "OPEN" or s["time"] == t)
               for s in db["signals"])
    if not side or busy:
        return
    d = 1 if side == "LONG" else -1
    entry = r["close"] * (1 + bt.SLIPPAGE * d)
    db["signals"].append(dict(
        id=f"{sym}-{key}-{t}", time=t, symbol=sym, track=key, side=side, status="OPEN",
        entry=round(entry, 4), sl=round(entry - d * cfg["sl"] * r["atr"], 4),
        tp=round(entry + d * cfg["tp"] * r["atr"], 4),
        snap=dict(rsi=round(r["rsi"], 1), atr=round(r["atr"], 2), volx=round(r["volume"] / r["vol_ma"], 2),
                  trend=trend(r))))


def run():
    db = load()
    db.pop("market", None)
    db.setdefault("markets", {})
    for sym in MARKETS:
        try:
            df = candles(sym)
        except Exception as ex:
            print(f"{sym} ዳታ አልወረደም: {ex}")
            continue
        update_open(db, df, sym)
        for key in TRACKS:
            new_signal(db, df, sym, key)
        r = df.iloc[-1]
        db["markets"][sym] = dict(price=round(r["close"], 4), candle=fmt(r["time"]), rsi=round(r["rsi"], 1),
                                  trend=trend(r), closes=[round(x, 4) for x in df["close"].tail(90)])
    db["checked"] = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC")
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    payload = json.dumps(db, ensure_ascii=False).replace("</", "<\\/")
    labels = json.dumps({k: v["label"] for k, v in TRACKS.items()}, ensure_ascii=False)
    with open(SITE, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("%%DATA%%", payload).replace("%%TRACKS%%", labels))
    o = sum(s["status"] == "OPEN" for s in db["signals"])
    print(f"[{db['checked']}] ገበያዎች: {len(db['markets'])} | ክፍት: {o} | ጠቅላላ: {len(db['signals'])} | {SITE}")


PAGE = r"""<!doctype html><html lang="am"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>ክሪፕቶ ሲግናሎች (paper trading)</title>
<style>
:root{--bg:#eef1f4;--card:#fff;--tx:#101828;--mu:#5b6574;--ln:#d5dbe3;--g:#0f7b55;--r:#c0392b;--b:#1f4fd8;--w:#fff4d6}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--tx:#e6e9ef;--mu:#8b95a5;--ln:#262d38;--g:#3ecf8e;--r:#ff7066;--b:#7aa2ff;--w:#2a2410}}
*{box-sizing:border-box}html{scroll-padding-top:env(safe-area-inset-top,0px)}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.55 "Ebrima","Nyala","Segoe UI",system-ui,sans-serif;padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}
main{max-width:900px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.5rem;margin:0 0 2px}h2{font-size:1.15rem;margin:26px 0 8px}
.mu{color:var(--mu);font-size:.92rem}.g{color:var(--g)}.r{color:var(--r)}
.warn{background:var(--w);border:1px solid var(--ln);border-radius:6px;padding:10px 12px;margin:14px 0;font-size:.92rem}
.mks{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:0 28px;margin:14px 0}
.mk{display:grid;grid-template-columns:auto 1fr 110px;gap:2px 12px;align-items:center;border-bottom:1px solid var(--ln);padding:8px 0}
.mk svg{grid-row:1/3;grid-column:3;width:110px;height:34px}.mk b{font-size:1.02rem}
.op{background:var(--card);border:1px solid var(--ln);border-radius:8px;padding:12px 14px;margin:8px 0}
.op header{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.rb{position:relative;display:flex;height:10px;border-radius:5px;overflow:hidden;margin:12px 0 4px}
.rb i{position:absolute;top:-3px;bottom:-3px;width:3px;background:var(--tx)}.rb i.p{background:var(--b);width:5px;border-radius:3px}
.lv{display:flex;justify-content:space-between;font-size:.88rem}
button{font:inherit;background:var(--b);color:#fff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer}
button:focus-visible,summary:focus-visible,select:focus-visible{outline:3px solid var(--b);outline-offset:2px}
.empty{background:var(--card);border:1px dashed var(--ln);border-radius:8px;padding:14px}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--ln);white-space:nowrap}th:first-child,td:first-child{text-align:left}th{color:var(--mu);font-weight:400;font-size:.88rem}
tr:last-child td{font-weight:600}
.fl{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}select{font:inherit;background:var(--card);color:var(--tx);border:1px solid var(--ln);border-radius:6px;padding:5px 8px}
details{background:var(--card);border:1px solid var(--ln);border-radius:8px;margin:6px 0;padding:10px 12px}
summary{cursor:pointer;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.tag{display:inline-block;border:1px solid var(--ln);border-radius:20px;padding:0 9px;font-size:.82rem;margin:6px 6px 0 0}
</style></head><body><main>
<h1>ክሪፕቶ ሲግናሎች</h1><div class="mu" id="ck"></div>
<div class="warn"><b>ይህ paper trading ነው።</b> ምናባዊ ገንዘብ ብቻ፤ ስትራቴጂዎቹ አሁንም እየተፈተኑ ነው፣ ትርፋማነታቸው አልተረጋገጠም። የገንዘብ ምክር አይደለም፣ በእውነተኛ ገንዘብ አትገበያይባቸው።</div>
<div class="mks" id="mks"></div>
<h2 id="oh">ክፍት ሲግናሎች</h2><div id="cur"></div>
<h2>ስትራቴጂዎች ሲወዳደሩ</h2><div class="tw" id="st"></div><div class="mu" id="nt"></div>
<h2>ያለፉ ሲግናሎች</h2><div class="fl" id="fl"></div><div id="list"></div>
</main><script>
const D=%%DATA%%,TR=%%TRACKS%%,S=D.signals,MK=D.markets||{},$=q=>document.querySelector(q);
const f=x=>x==null?"–":Number(x).toLocaleString("en-US",{maximumFractionDigits:4});
const nm=s=>(s||"").replace("USDT","");
$("#ck").textContent="ተፈትሿል "+(D.checked||"–");
$("#mks").innerHTML=Object.entries(MK).map(([s,m])=>{const c=m.closes||[],lo=Math.min(...c),hi=Math.max(...c),
 p=c.map((v,i)=>`${(i/(c.length-1)*110).toFixed(1)},${(32-(v-lo)/(hi-lo||1)*30).toFixed(1)}`).join(" ");
 return `<div class="mk"><b>${nm(s)}</b><span>${f(m.price)}</span><svg viewBox="0 0 110 34" aria-label="${nm(s)} ዋጋ"><polyline points="${p}" fill="none" stroke="var(--b)" stroke-width="1.6"/></svg><span class="mu">${m.trend} · RSI ${m.rsi}</span></div>`}).join("")||`<div class="mu">ዳታ የለም</div>`;
const op=S.filter(s=>s.status==="OPEN"),dn=S.filter(s=>s.status!=="OPEN").sort((a,b)=>b.closed.localeCompare(a.closed));
$("#oh").textContent=`ክፍት ሲግናሎች (${op.length})`;
$("#cur").innerHTML=op.length?op.map((s,i)=>{const m=MK[s.symbol]||{},p=m.price??s.entry,lo=Math.min(s.sl,s.tp),hi=Math.max(s.sl,s.tp),
 pos=v=>Math.max(0,Math.min(100,(v-lo)/(hi-lo)*100)),L=s.side==="LONG",e=pos(s.entry);
 return `<div class="op"><header><b class="${L?"g":"r"}">${nm(s.symbol)} ${s.side}</b><span class="mu">${TR[s.track]||s.track} · ${s.time} UTC</span></header>
 <div class="rb"><div style="width:${e}%;background:var(${L?"--r":"--g"})"></div><div style="width:${100-e}%;background:var(${L?"--g":"--r"})"></div><i style="left:${e}%"></i><i class="p" style="left:calc(${pos(p)}% - 2px)"></i></div>
 <div class="lv"><span class="${L?"r":"g"}">${L?"SL":"TP"} ${f(L?s.sl:s.tp)}</span><span>Entry ${f(s.entry)} · አሁን ${f(p)}</span><span class="${L?"g":"r"}">${L?"TP":"SL"} ${f(L?s.tp:s.sl)}</span></div>
 <div style="margin-top:8px"><button class="cp" data-i="${i}">ኮፒ አድርግ</button> <span class="mu">RSI ${s.snap.rsi} · ${s.snap.trend}</span></div></div>`}).join(""):
 `<div class="empty">አሁን ክፍት ሲግናል የለም። ሲስተሙ ሻማ ሲዘጋ (በየ4 ሰዓት) ይፈትሻል፤ ሁኔታዎቹ ካልተሟሉ ሲግናል አይሰጥም።</div>`;
document.querySelectorAll(".cp").forEach(b=>b.onclick=()=>{const s=op[+b.dataset.i];navigator.clipboard.writeText(`${s.symbol} ${s.side}\nEntry ${s.entry}\nSL ${s.sl}\nTP ${s.tp}`).then(()=>b.textContent="ኮፒ ተደርጓል")});
function st(a){a=[...a].sort((x,y)=>x.closed.localeCompare(y.closed));const R=a.map(s=>s.R),n=R.length,w=R.filter(x=>x>0).reduce((p,c)=>p+c,0),l=-R.filter(x=>x<=0).reduce((p,c)=>p+c,0),
 mu=n?R.reduce((p,c)=>p+c,0)/n:0,sd=n>2?Math.sqrt(R.reduce((p,c)=>p+(c-mu)**2,0)/(n-1)):0;let e=1,pk=1,dd=0;R.forEach(r=>{e*=1+.01*r;pk=Math.max(pk,e);dd=Math.min(dd,e/pk-1)});
 return{n,win:n?R.filter(x=>x>0).length/n:null,pf:l?w/l:null,avg:n?mu:null,t:sd?mu/(sd/Math.sqrt(n)):null,dd}}
const rows=[...Object.keys(TR).map(k=>[TR[k],dn.filter(s=>s.track===k)]),["ሁሉም",dn]];
$("#st").innerHTML=`<table><thead><tr><th>ስትራቴጂ</th><th>ንግዶች</th><th>Win</th><th>PF</th><th>avg R</th><th>t</th><th>DD</th></tr></thead><tbody>${rows.map(([n,a])=>{const x=st(a);
 return `<tr><td>${n}</td><td>${x.n}</td><td>${x.win==null?"–":(x.win*100).toFixed(0)+"%"}</td><td>${x.pf==null?"–":x.pf.toFixed(2)}</td><td>${x.avg==null?"–":x.avg.toFixed(2)}</td><td>${x.t==null?"–":x.t.toFixed(1)}</td><td>${(x.dd*100).toFixed(1)}%</td></tr>`}).join("")}</tbody></table>`;
$("#nt").textContent=`ሁሉም ስትራቴጂዎች ${Object.keys(MK).map(nm).join("፣ ")} ላይ በትይዩ ይከታተላሉ። ከ100 ንግድ በፊት ቁጥሮቹን አትመኑ፤ |t| ከ2 በታች ከሆነ ውጤቱ ከዕድል አይለይም።`;
const F={t:"",m:"",o:""},opt=(a,k)=>a.map(([v,t])=>`<option value="${v}">${t}</option>`).join("");
$("#fl").innerHTML=`<select id="ft"><option value="">ሁሉም ስትራቴጂ</option>${opt(Object.entries(TR))}</select><select id="fm"><option value="">ሁሉም ገበያ</option>${opt(Object.keys(MK).map(k=>[k,nm(k)]))}</select><select id="fo"><option value="">ትርፍ እና ኪሳራ</option><option value="WIN">ትርፍ</option><option value="LOSS">ኪሳራ</option></select>`;
function draw(){const a=dn.filter(s=>(!F.t||s.track===F.t)&&(!F.m||s.symbol===F.m)&&(!F.o||s.status===F.o));
 $("#list").innerHTML=a.length?a.map(s=>`<details><summary><span><b class="${s.status==="WIN"?"g":"r"}">${s.status==="WIN"?"ትርፍ":"ኪሳራ"}</b> · ${nm(s.symbol)} ${s.side} · ${TR[s.track]||""} · ${s.closed}</span><b>${s.R>0?"+":""}${s.R}R</b></summary>
 <div class="mu">Entry ${f(s.entry)} → ${f(s.exit)} (${s.reason}፣ ${s.candles} ሻማ) · RSI ${s.snap.rsi} · ${s.snap.trend}</div>
 ${s.tags.map(t=>`<span class="tag">${t}</span>`).join("")}
 <p class="mu">መለያዎቹ የስታቲስቲክስ ጥቆማዎች ናቸው፣ ምክንያቱን አያረጋግጡም።</p></details>`).join(""):`<p class="mu">የተዘጋ ሲግናል የለም።</p>`}
[["#ft","t"],["#fm","m"],["#fo","o"]].forEach(([q,k])=>$(q).onchange=function(){F[k]=this.value;draw()});
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

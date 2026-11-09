"""
testnet_copier.py - የሰዓታዊ S2 ቦታዎችን በ Binance Spot TESTNET (የውሸት ገንዘብ) ዲሞ ላይ ኮፒ ያደርጋል
  -> data/copylog.json + docs/copylog.html  (ለእያንዳንዱ ንግድ: ትክክለኛ ዋጋ፣ ከ paper ዋጋ ልዩነት፣ ክፍያ፣ ትርፍ/ኪሳራ)
ደህንነት: ይህ ስክሪፕት የ testnet አድራሻ ብቻ ይጠቀማል (ከታች ASSERT ይመልከቱ)፤ እውነተኛ ገንዘብ ማንቀሳቀስ አይችልም።
ቁልፎች: BINANCE_TESTNET_KEY እና BINANCE_TESTNET_SECRET (GitHub secrets ብቻ፤ በፋይል ውስጥ አይጻፉም)
ሙከራ: python testnet_copier.py demo   (የውሸት ደንበኛ፣ ኢንተርኔት አያስፈልግም)
"""
import hashlib, hmac, json, os, random, sys, time
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode
import requests

try:
    from notify import notify
except Exception:
    def notify(text):
        print("[ማሳወቂያ]", text)

DEMO = "demo" in sys.argv
BASE = "https://testnet.binance.vision"
assert "testnet" in BASE, "ይህ ስክሪፕት testnet ብቻ ነው የሚጠቀመው"
ALLOC = 1000.0  # ለእያንዳንዱ ሳንቲም የሚመደብ የውሸት USDT (ቦታ 100% ሲሆን)
DIR = os.path.dirname(os.path.abspath(__file__))
HOURLY = os.path.join(DIR, "data", "hourly_demo.json" if DEMO else "hourly.json")
LOG = os.path.join(DIR, "data", "copylog_demo.json" if DEMO else "copylog.json")
SITE = os.path.join(DIR, "docs", "copylog_demo.html" if DEMO else "copylog.html")
os.makedirs(os.path.dirname(LOG), exist_ok=True)
os.makedirs(os.path.dirname(SITE), exist_ok=True)


class Client:
    def __init__(self, key, secret):
        self.k, self.s = key, secret.encode()

    def _req(self, method, path, params=None, signed=False):
        p = dict(params or {})
        if signed:
            p["timestamp"] = int(time.time() * 1000)
            p["recvWindow"] = 10000
            p["signature"] = hmac.new(self.s, urlencode(p).encode(), hashlib.sha256).hexdigest()
        r = requests.request(method, BASE + path, params=p if method == "GET" else None,
                             data=p if method != "GET" else None, headers={"X-MBX-APIKEY": self.k}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
        return r.json()

    def info(self, sym):
        return self._req("GET", "/api/v3/exchangeInfo", {"symbol": sym})["symbols"][0]

    def balances(self):
        return {b["asset"]: float(b["free"]) for b in self._req("GET", "/api/v3/account", signed=True)["balances"]}

    def buy_quote(self, sym, usdt):
        return self._req("POST", "/api/v3/order", dict(symbol=sym, side="BUY", type="MARKET",
                                                       quoteOrderQty=f"{usdt:.2f}", newOrderRespType="FULL"), True)

    def sell_qty(self, sym, qty):
        return self._req("POST", "/api/v3/order", dict(symbol=sym, side="SELL", type="MARKET", quantity=qty,
                                                       newOrderRespType="FULL"), True)


class FakeClient:
    """ለሙከራ ብቻ: ኢንተርኔት የሌለው የውሸት ልውውጥ።"""
    def __init__(self, prices):
        self.prices, self.missing = prices, {"LINKUSDT"}
        self.path = os.path.join(DIR, "data", "fake_state.json")
        self.bal = json.load(open(self.path)) if os.path.exists(self.path) else {"USDT": 10000.0}

    def _save(self):
        json.dump(self.bal, open(self.path, "w"))

    def _px(self, sym):
        return self.prices[sym] * (1 + random.uniform(-0.0006, 0.0006))

    def info(self, sym):
        if sym in self.missing:
            raise RuntimeError("HTTP 400: Invalid symbol")
        return dict(status="TRADING", filters=[dict(filterType="LOT_SIZE", stepSize="0.0001"),
                                               dict(filterType="NOTIONAL", minNotional="5.0")])

    def balances(self):
        return dict(self.bal)

    def buy_quote(self, sym, usdt):
        p, base = self._px(sym), sym[:-4]
        q = round(usdt / p, 6)
        self.bal["USDT"] -= usdt
        self.bal[base] = self.bal.get(base, 0) + q * 0.999
        self._save()
        return dict(fills=[dict(price=str(p), qty=str(q), commission=str(q * 0.001), commissionAsset=base)])

    def sell_qty(self, sym, qty):
        p, base, q = self._px(sym), sym[:-4], float(qty)
        self.bal[base] -= q
        self.bal["USDT"] += q * p * 0.999
        self._save()
        return dict(fills=[dict(price=str(p), qty=str(q), commission=str(q * p * 0.001), commissionAsset="USDT")])


def parse_fills(o, base):
    qty = quote = fee = bfee = 0.0
    for f in o.get("fills", []):
        q, p, c = float(f["qty"]), float(f["price"]), float(f["commission"])
        qty += q
        quote += q * p
        if f["commissionAsset"] == base:
            bfee += c
        elif f["commissionAsset"] == "USDT":
            fee += c
    avg = quote / qty if qty else 0.0
    return qty, quote, avg, bfee, fee + bfee * avg


def utc():
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime())


def run(client, hourly):
    log = json.load(open(LOG, encoding="utf-8")) if os.path.exists(LOG) else {}
    for k, v in (("ledger", {}), ("orders", []), ("episodes", []), ("errors", []), ("symbols", {})):
        log.setdefault(k, v)
    msgs = []
    try:
        bal = client.balances()
    except Exception as ex:
        print("ሒሳብ ማንበብ አልተቻለም:", str(ex)[:160])
        return
    for sym, st in hourly["coins"].items():
        pos, ref, base = float(st["pos"]), float(st["price"]), sym[:-4]
        L = log["ledger"].setdefault(sym, dict(qty=0.0, target=0.0, cost=0.0, proceeds=0.0, entry_time=None,
                                               ref_entry=None, fill_entry=None))
        small = abs(pos - L["target"]) < 0.2 and pos != 0 and L["target"] != 0
        if abs(pos - L["target"]) < 1e-9 or small:  # ትንሽ ለውጦችን (ከ20% ምደባ በታች) አንገበያይም፤ ክፍያ እንዳይበላው
            continue
        try:
            info = client.info(sym)
            if info.get("status") != "TRADING":
                raise RuntimeError("not trading")
        except Exception:
            log["symbols"][sym] = "አይገኝም"
            continue
        log["symbols"][sym] = "ይገኛል"
        try:
            flt = {f["filterType"]: f for f in info["filters"]}
            step = Decimal(flt["LOT_SIZE"]["stepSize"])
            mn = float((flt.get("NOTIONAL") or flt.get("MIN_NOTIONAL") or {}).get("minNotional", 10))
            now = utc()
            if pos > L["target"]:
                usdt = (pos - L["target"]) * ALLOC
                if usdt < mn * 1.05 or bal.get("USDT", 0) < usdt:
                    raise RuntimeError(f"መግዛት አልተቻለም ({usdt:.0f} USDT)")
                qty, quote, avg, bfee, fee_usdt = parse_fills(client.buy_quote(sym, usdt), base)
                L["qty"] += qty - bfee
                L["cost"] += quote + fee_usdt
                if L["entry_time"] is None:
                    L.update(entry_time=now, ref_entry=ref, fill_entry=avg)
                side, slip = "BUY", (avg / ref - 1) * 100
                bal["USDT"] = bal.get("USDT", 0) - usdt
            else:
                free = client.balances().get(base, 0.0)
                want = L["qty"] * (1.0 if pos == 0 else 1 - pos / L["target"])
                if want > 0 and free < want * 0.5:  # testnet ዳግም ተጀምሮ ሊሆን ይችላል
                    log["errors"].append(f"{now} {sym}: ሒሳብ ከታሰበው በጣም ያነሰ ነው (testnet ዳግም ተጀምሯል?)፤ ዜሮ ተደርጓል")
                    L.update(qty=0.0, target=0.0, cost=0.0, proceeds=0.0, entry_time=None)
                    continue
                q = min(want, free)
                qd = (Decimal(str(q)) / step).to_integral_value(rounding=ROUND_DOWN) * step
                if float(qd) * ref < mn:
                    if pos == 0:  # ትንሽ ቀሪ (dust) ብቻ ነው፤ እንደተዘጋ እንቆጥራለን
                        L["qty"] = 0.0
                    raise RuntimeError("ለመሸጥ ከዝቅተኛው መጠን በታች ነው")
                qty, quote, avg, bfee, fee_usdt = parse_fills(client.sell_qty(sym, format(qd, "f")), base)
                L["qty"] = max(0.0, L["qty"] - qty)
                L["proceeds"] += quote - fee_usdt
                side, slip = "SELL", (1 - avg / ref) * 100
            log["orders"].append(dict(t=now, s=sym, side=side, qty=round(qty, 8), px=round(avg, 6), usdt=round(quote, 2),
                                      fee=round(fee_usdt, 4), ref=ref, slip=round(slip, 3)))
            L["target"] = pos
            msgs.append(f"{'🟢 ገዛ' if side == 'BUY' else '🟠 ሸጠ'} {base} · {quote:.0f} USDT · ከ paper ዋጋ ልዩነት {slip:+.2f}%")
            if pos == 0:
                pnl = L["proceeds"] - L["cost"]
                log["episodes"].append(dict(s=sym, e=L["entry_time"], x=now, cost=round(L["cost"], 2), pnl=round(pnl, 2),
                                            ret=round(pnl / ALLOC, 5), ref_in=L["ref_entry"], fill_in=L["fill_entry"],
                                            ref_out=ref, fill_out=avg))
                msgs.append(f"{'✅ ትርፍ' if pnl > 0 else '🔴 ኪሳራ'} {base} (ዲሞ): {pnl:+.2f} USDT")
                L.update(qty=0.0, cost=0.0, proceeds=0.0, entry_time=None, ref_entry=None, fill_entry=None)
        except Exception as ex:
            log["errors"].append(f"{utc()} {sym}: {str(ex)[:140]}")
    log["errors"], log["orders"] = log["errors"][-20:], log["orders"][-300:]
    log["updated"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False)
    with open(SITE, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("%%DATA%%", json.dumps(log, ensure_ascii=False).replace("</", "<\\/")))
    if msgs:
        notify("🧪 Testnet ዲሞ (የውሸት ገንዘብ)\n" + "\n".join(msgs[:15]))
    print(f"[{log['updated']}] ትዕዛዞች: {len(log['orders'])} | የተዘጉ ንግዶች: {len(log['episodes'])} | ስህተቶች: {len(log['errors'])}")


PAGE = r"""<!doctype html><html lang="am"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>Demo copy log</title>
<style>
:root{--bg:#eef1f4;--card:#fff;--tx:#101828;--mu:#5b6574;--ln:#d5dbe3;--g:#0f7b55;--r:#c0392b;--b:#1f4fd8;--w:#fff4d6}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--tx:#e6e9ef;--mu:#8b95a5;--ln:#262d38;--g:#3ecf8e;--r:#ff7066;--b:#7aa2ff;--w:#2a2410}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.55 "Ebrima","Nyala","Segoe UI",system-ui,sans-serif;padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}
main{max-width:900px;margin:0 auto;padding:20px 16px 48px}h1{font-size:1.5rem;margin:0}h2{font-size:1.15rem;margin:26px 0 8px}a{color:var(--b)}
.top{display:flex;justify-content:space-between;align-items:center;gap:10px}.mu{color:var(--mu);font-size:.92rem}.g{color:var(--g)}.r{color:var(--r)}
button{font:inherit;background:transparent;color:var(--tx);border:1px solid var(--ln);border-radius:6px;padding:6px 12px;cursor:pointer}button:focus-visible{outline:3px solid var(--b);outline-offset:2px}
.warn{background:var(--w);border:1px solid var(--ln);border-radius:6px;padding:10px 12px;margin:14px 0;font-size:.92rem}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--ln);white-space:nowrap}th:first-child,td:first-child{text-align:left}th{color:var(--mu);font-weight:400;font-size:.88rem}
.sm{display:flex;gap:22px;flex-wrap:wrap;margin:10px 0}.sm b{font-size:1.25rem;display:block}
</style></head><body><main>
<div class="top"><h1 id="h"></h1><button id="lg" type="button" aria-label="Language"></button></div>
<div class="mu" id="ck"></div><div class="mu"><a href="hourly.html" id="l1"></a> · <a href="./" id="l2"></a></div>
<div class="warn" id="wn"></div><div class="sm" id="sm"></div>
<h2 id="h1"></h2><div class="tw" id="ep"></div>
<h2 id="h2"></h2><div class="tw" id="od"></div>
<h2 id="h3"></h2><div class="mu" id="sy"></div><div class="mu" id="er"></div><div class="mu" id="nt" style="margin-top:12px"></div>
</main><script>
const D=%%DATA%%,$=q=>document.querySelector(q);
const I={am:{h:"ዲሞ ኮፒ ምዝግብ (Testnet)",chk:"ተዘምኗል ",l1:"← 1 ሰዓት S2",l2:"ዋና ገጽ",warn:"<b>ይህ የውሸት ገንዘብ ያለው ዲሞ (Binance Spot Testnet) ነው።</b> ሲግናሎቹ ከ paper trader ጋር አንድ ናቸው፤ እዚህ የሚታየው ትዕዛዞቹ በዲሞ ሲፈጸሙ ምን እንደሆነ ነው። ዲሞ ከእውነተኛ ገበያ የተሻለ (ፈጣንና ንጹህ) ዋጋ ሊሰጥ ይችላል፣ ስለዚህ ይህ እውነተኛ ግብይትን አያረጋግጥም። የገንዘብ ምክር አይደለም።",
n:"የተዘጉ ንግዶች",w:"ትርፍ",l:"ኪሳራ",pnl:"ጠቅላላ ትርፍ/ኪሳራ (USDT)",slip:"አማካይ የዋጋ ልዩነት",fee:"ጠቅላላ ክፍያ (USDT)",
h1:"የተዘጉ ንግዶች (ዲሞ)",coin:"ሳንቲም",ent:"መግቢያ",ext:"መውጫ",pl:"ትርፍ/ኪሳራ (USDT)",ret:"ከምደባ %",res:"ውጤት",pw:"ትርፍ ✅",ls:"ኪሳራ 🔴",none:"እስካሁን የተዘጋ የዲሞ ንግድ የለም።",
h2:"ትዕዛዞች (የቅርብ 30)",t:"ሰዓት",side:"ዓይነት",buy:"ግዢ",sell:"ሽያጭ",px:"ዋጋ",ref:"paper ዋጋ",sl:"ልዩነት %",fe:"ክፍያ",
h3:"ሁኔታ",sy:"በ testnet ላይ ያሉ ሳንቲሞች: ",er:"የቅርብ ስህተቶች: ",ok:"ምንም",
nt:"ማስታወሻ: 'ልዩነት' ከ paper ማስመሰያ ዋጋ ጋር ሲነጻጸር ነው (አዎንታዊ = የባሰ)፤ GitHub በየሰዓቱ ከ5–30 ደቂቃ ሊዘገይ ስለሚችል የተወሰነ ልዩነት ይጠበቃል። Testnet በየጊዜው ዳግም ይጀምራል (ምዝግብ ይቀጥላል፣ ሒሳብ ይጸዳል)። ሁሉም ሳንቲሞች በ testnet ላይ ላይኖሩ ይችላሉ።"},
en:{h:"Demo copy log (Testnet)",chk:"Updated ",l1:"← Hourly S2",l2:"Main page",warn:"<b>This is a demo with fake money (Binance Spot Testnet).</b> The signals are the same as the paper trader; this shows what happened when the orders were executed on the demo. A demo can give better (faster, cleaner) prices than the real market, so this does not prove real trading. Not financial advice.",
n:"Closed trades",w:"Profit",l:"Loss",pnl:"Total profit/loss (USDT)",slip:"Average price gap",fee:"Total fees (USDT)",
h1:"Closed trades (demo)",coin:"Coin",ent:"Entry",ext:"Exit",pl:"Profit/Loss (USDT)",ret:"of allocation %",res:"Result",pw:"Profit ✅",ls:"Loss 🔴",none:"No closed demo trades yet.",
h2:"Orders (latest 30)",t:"Time",side:"Side",buy:"Buy",sell:"Sell",px:"Price",ref:"Paper price",sl:"Gap %",fe:"Fee",
h3:"Status",sy:"Coins available on testnet: ",er:"Recent errors: ",ok:"none",
nt:"Note: ‘gap’ is versus the paper model price (positive = worse); GitHub can delay hourly runs by 5–30 minutes, so some gap is expected. The testnet resets from time to time (the log continues, balances are cleared). Not every coin may exist on the testnet."}};
let lg="am";try{lg=localStorage.getItem("lg")||"am"}catch(e){}
const f=(x,d=2)=>Number(x).toLocaleString("en-US",{maximumFractionDigits:d}),sg=x=>(x>0?"+":"")+f(x),cl=x=>`<span class="${x>0?"g":"r"}">${sg(x)}</span>`;
function render(){const T=I[lg],E=D.episodes||[],O=D.orders||[],w=E.filter(x=>x.pnl>0).length;
 document.documentElement.lang=lg;document.title=T.h;$("#h").textContent=T.h;$("#lg").textContent=lg==="am"?"English":"አማርኛ";$("#ck").textContent=T.chk+(D.updated||"–");
 $("#l1").textContent=T.l1;$("#l2").textContent=T.l2;$("#wn").innerHTML=T.warn;
 const tp=E.reduce((a,x)=>a+x.pnl,0),ft=O.reduce((a,x)=>a+x.fee,0),sa=O.length?O.reduce((a,x)=>a+x.slip,0)/O.length:null;
 $("#sm").innerHTML=`<div class="mu">${T.n}<b>${E.length}</b></div><div class="mu">${T.w} / ${T.l}<b>${w} / ${E.length-w}</b></div><div class="mu">${T.pnl}<b>${E.length?cl(tp):"–"}</b></div><div class="mu">${T.slip}<b>${sa==null?"–":sg(sa)+"%"}</b></div><div class="mu">${T.fee}<b>${f(ft)}</b></div>`;
 $("#h1").textContent=T.h1;
 $("#ep").innerHTML=E.length?`<table><thead><tr><th>${T.coin}</th><th>${T.ent}</th><th>${T.ext}</th><th>${T.pl}</th><th>${T.ret}</th><th>${T.res}</th></tr></thead><tbody>${[...E].reverse().slice(0,40).map(x=>`<tr><td>${x.s.replace("USDT","")}</td><td>${x.e}</td><td>${x.x}</td><td>${cl(x.pnl)}</td><td>${sg(x.ret*100)}%</td><td class="${x.pnl>0?"g":"r"}">${x.pnl>0?T.pw:T.ls}</td></tr>`).join("")}</tbody></table>`:`<div class="mu">${T.none}</div>`;
 $("#h2").textContent=T.h2;
 $("#od").innerHTML=O.length?`<table><thead><tr><th>${T.t}</th><th>${T.coin}</th><th>${T.side}</th><th>${T.px}</th><th>${T.ref}</th><th>${T.sl}</th><th>${T.fe}</th></tr></thead><tbody>${[...O].reverse().slice(0,30).map(x=>`<tr><td>${x.t}</td><td>${x.s.replace("USDT","")}</td><td>${x.side==="BUY"?T.buy:T.sell}</td><td>${f(x.px,6)}</td><td>${f(x.ref,6)}</td><td class="${x.slip>0?"r":"g"}">${sg(x.slip)}</td><td>${f(x.fee,3)}</td></tr>`).join("")}</tbody></table>`:`<div class="mu">${T.none}</div>`;
 $("#h3").textContent=T.h3;
 $("#sy").textContent=T.sy+(Object.keys(D.symbols||{}).length?Object.entries(D.symbols).map(([k,v])=>k.replace("USDT","")+" ("+v+")").join(", "):"–");
 $("#er").textContent=T.er+((D.errors||[]).length?(D.errors||[]).slice(-3).join(" | "):T.ok);$("#nt").textContent=T.nt}
$("#lg").onclick=()=>{lg=lg==="am"?"en":"am";try{localStorage.setItem("lg",lg)}catch(e){}render()};
render();
</script></body></html>"""


def main():
    if not os.path.exists(HOURLY):
        print("data/hourly.json የለም፤ መጀመሪያ hourly_tracker.py መሮጥ አለበት")
        return
    hourly = json.load(open(HOURLY, encoding="utf-8"))
    if DEMO:
        client = FakeClient({s: float(m["price"]) for s, m in hourly["coins"].items()})
    else:
        key, sec = os.environ.get("BINANCE_TESTNET_KEY"), os.environ.get("BINANCE_TESTNET_SECRET")
        if not key or not sec:
            print("BINANCE_TESTNET_KEY/SECRET አልተቀመጡም (GitHub secrets)፤ ዲሞ ኮፒ ይዘላል")
            return
        client = Client(key, sec)
    run(client, hourly)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""VCP 走勢圖：產生可在瀏覽器開啟的單一 HTML（只用 Python 標準函式庫；圖表用 TradingView lightweight-charts）。

版面（類似看盤軟體）：
  主圖：K 線（紅漲綠跌）＋ MA10／20／50／150／200，標出 VCP 收縮（每段高點→低點與回檔 %）、突破點、停損、停利線
  附圖：成交量（＋10 日、50 日均量）→ KD(9,3,3) → MACD(12,26,9) → RSI(14)
價格為除權息還原價（與 compute_vcp.py 相同）。多檔股票時左側有清單可切換。

用法：
  python3 chart_vcp.py --data vcp-data --codes 3293:20240604                 # 單一案例；冒號後為標記日（突破日）
  python3 chart_vcp.py --data vcp-data --codes 3293:20240604,1717:20251223 --after 80
  python3 chart_vcp.py --data vcp-data --list vcp-data/output/vcp_addon_20250912.csv   # 名單內所有股票（標記日＝名單日期）
輸出：<data>/output/charts/<檔名>.html（--out 可指定）
"""
import argparse, csv, glob, json, os, re
from collections import defaultdict

p = argparse.ArgumentParser()
p.add_argument("--data", required=True)
p.add_argument("--codes", default="", help="代號[:標記日]，逗號分隔，例如 3293:20240604")
p.add_argument("--list", default="", help="compute_vcp.py 產出的名單 CSV（檔名含日期）")
p.add_argument("--days", type=int, default=250, help="標記日之前顯示幾個交易日")
p.add_argument("--after", type=int, default=0, help="標記日之後顯示幾個交易日（看案例結果用）")
p.add_argument("--base-days", type=int, default=120, help="找收縮的回看天數（同 compute_vcp.py）")
p.add_argument("--out", default="")
p.add_argument("--title", default="")
args = p.parse_args()
D = os.path.abspath(os.path.expanduser(args.data))


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


# ---------- 要畫哪些股票 ----------
targets = []  # (code, mark_date or "", extra info dict)
if args.list:
    m = re.search(r"(\d{8})", os.path.basename(args.list))
    asof = m.group(1) if m else ""
    for r in csv.DictReader(open(args.list, encoding="utf-8-sig")):
        targets.append((r["code"], asof, r))
for item in filter(None, (x.strip() for x in args.codes.split(","))):
    code, _, mark = item.partition(":")
    targets.append((code, mark, {}))
if not targets:
    raise SystemExit("請用 --codes 或 --list 指定股票")
want = {t[0] for t in targets}

# ---------- 讀日線（只留需要的股票） ----------
bars, names = defaultdict(dict), {}
for mk in ("twse", "tpex"):
    for f in sorted(glob.glob(os.path.join(D, mk, "*.csv"))):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            if r["code"] not in want:
                continue
            c = num(r["close"])
            if c is None or c <= 0:
                continue
            o, h, l = num(r["open"]) or c, num(r["high"]) or c, num(r["low"]) or c
            bars[r["code"]][r["date"]] = [o, h, l, c, num(r["volume"]) or 0]
            names[r["code"]] = r["name"]
factors = defaultdict(list)
for f in glob.glob(os.path.join(D, "exright", "*.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8")):
        if r["code"] in want:
            cb, ref = num(r["close_before"]), num(r["reference"])
            if cb and ref and 0 < ref < cb:
                factors[r["code"]].append((r["date"], ref / cb))
INDUSTRY_NAMES = {
    "01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷",
    "09": "造紙", "10": "鋼鐵", "11": "橡膠", "12": "汽車", "14": "建材營造", "15": "航運", "16": "觀光餐旅",
    "17": "金融保險", "18": "貿易百貨", "19": "綜合", "20": "其他", "21": "化學", "22": "生技醫療", "23": "油電燃氣",
    "24": "半導體", "25": "電腦及週邊設備", "26": "光電", "27": "通信網路", "28": "電子零組件", "29": "電子通路",
    "30": "資訊服務", "31": "其他電子", "32": "文化創意", "33": "農業科技", "34": "電子商務", "35": "綠能環保",
    "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "80": "管理股票", "91": "存託憑證",
}

industry = {}
if os.path.exists(os.path.join(D, "industry.csv")):
    for r in csv.DictReader(open(os.path.join(D, "industry.csv"), encoding="utf-8")):
        industry[r["code"]] = r["industry"]


# ---------- 指標 ----------
def sma(xs, n):
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(xs, n):
    out, k = [], 2 / (n + 1)
    for i, x in enumerate(xs):
        out.append(x if i == 0 else x * k + out[-1] * (1 - k))
    return out


def kd(hi, lo, cl, n=9):
    ks, ds, k, d = [], [], 50.0, 50.0
    for i in range(len(cl)):
        if i >= n - 1:
            hh, ll = max(hi[i - n + 1:i + 1]), min(lo[i - n + 1:i + 1])
            rsv = 50.0 if hh == ll else (cl[i] - ll) / (hh - ll) * 100
            k = k * 2 / 3 + rsv / 3
            d = d * 2 / 3 + k / 3
        ks.append(k)
        ds.append(d)
    return ks, ds


def rsi(cl, n=14):
    out, ag, al = [None] * len(cl), 0.0, 0.0
    for i in range(1, len(cl)):
        g, l = max(cl[i] - cl[i - 1], 0), max(cl[i - 1] - cl[i], 0)
        if i <= n:
            ag, al = ag + g / n, al + l / n
            if i < n:
                continue
        else:
            ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def contractions(hi, lo, end, look, w=4):
    """同 compute_vcp.py：end（不含）之前 look 天內每段「高點→低點」回檔；回傳 [(高點 idx, 高, 低點 idx, 低, %)]."""
    start = max(0, end - look)
    piv = []
    for i in range(start + w, end - w):
        if hi[i] == max(hi[i - w:i + w + 1]):
            piv.append(("H", i, hi[i]))
        if lo[i] == min(lo[i - w:i + w + 1]):
            piv.append(("L", i, lo[i]))
    piv.sort(key=lambda x: x[1])
    out, cur = [], None
    for t, i, v in piv:
        if t == "H":
            if cur is None or v > cur[1]:
                cur = (i, v)
        elif cur:
            out.append((cur[0], cur[1], i, v, round((cur[1] - v) / cur[1] * 100, 1)))
            cur = None
    return out


def r2(x):
    return None if x is None else round(x, 4)


def build(code, mark, info):
    ds = sorted(bars.get(code, {}))
    if not ds:
        return None
    rows = [list(bars[code][d]) for d in ds]
    for exd, ratio in factors.get(code, []):
        for i, d in enumerate(ds):
            if d < exd:
                for k in range(4):
                    rows[i][k] *= ratio
    o, h, l, c, v = (list(x) for x in zip(*rows))
    mi = max([i for i, d in enumerate(ds) if d <= mark], default=len(ds) - 1) if mark else len(ds) - 1
    lo_i, hi_i = max(0, mi - args.days), min(len(ds), mi + args.after + 1)
    ma = {n: sma(c, n) for n in (10, 20, 50, 150, 200)}
    vma = {n: sma(v, n) for n in (10, 50)}
    k, d = kd(h, l, c)
    dif = [a - b for a, b in zip(ema(c, 12), ema(c, 26))]
    sig = ema(dif, 9)
    rs = rsi(c)
    t = [f"{x[:4]}-{x[4:6]}-{x[6:]}" for x in ds]
    rng = range(lo_i, hi_i)
    # 收縮：以標記日（突破日）之前的基底計算；突破日＝收盤首次站上最後一段收縮的高點
    cons = [x for x in contractions(h, l, mi, args.base_days) if x[0] >= lo_i]
    pivot = cons[-1][1] if cons else None
    marks = []
    for hi_idx, hv, lo_idx, lv, dep in cons[-4:]:  # 只標最近 4 次（同篩選規則）
        marks.append({"time": t[hi_idx], "position": "aboveBar", "color": "#f0b90b", "shape": "arrowDown", "text": ""})
        marks.append({"time": t[lo_idx], "position": "belowBar", "color": "#f0b90b", "shape": "arrowUp", "text": f"-{dep}%"})
    bo = None
    if pivot:
        for i in range(mi, hi_i):  # 標記日起第一個收盤站上突破點的日子
            if c[i] > pivot and c[i - 1] <= pivot:
                bo = i
                break
        if bo is not None:
            vx = v[bo] / vma[50][bo - 1] if vma[50][bo - 1] else 0
            marks.append({"time": t[bo], "position": "belowBar", "color": "#ff4d6d", "shape": "arrowUp",
                          "text": f"突破 量{vx:.1f}x"})
    marks.sort(key=lambda m: m["time"])
    entry = c[bo] if bo is not None else (pivot or c[mi])
    after = None
    if bo is not None and bo + 1 < len(c):
        end = min(len(c), bo + 61)
        best = max(h[bo + 1:end])
        after = {"max60": round((best / c[bo] - 1) * 100, 1)}
    return {
        "code": code, "name": names.get(code, ""), "mark": mark,
        "industry": INDUSTRY_NAMES.get(industry.get(code, ""), industry.get(code, "")),
        "info": {k2: info.get(k2, "") for k2 in ("stage", "rs", "rev_3m_yoy", "rev_growth", "contractions", "addon_score",
                                                   "tech_score", "total_score")},
        "candles": [{"time": t[i], "open": r2(o[i]), "high": r2(h[i]), "low": r2(l[i]), "close": r2(c[i])} for i in rng],
        "ma": {str(n): [{"time": t[i], "value": r2(ma[n][i])} for i in rng if ma[n][i] is not None] for n in ma},
        "vol": [{"time": t[i], "value": v[i] / 1000, "up": c[i] >= o[i]} for i in rng],  # 張
        "vma": {str(n): [{"time": t[i], "value": r2(vma[n][i] / 1000)} for i in rng if vma[n][i] is not None] for n in vma},
        "k": [{"time": t[i], "value": r2(k[i])} for i in rng], "d": [{"time": t[i], "value": r2(d[i])} for i in rng],
        "dif": [{"time": t[i], "value": r2(dif[i])} for i in rng], "sig": [{"time": t[i], "value": r2(sig[i])} for i in rng],
        "osc": [{"time": t[i], "value": r2(dif[i] - sig[i])} for i in rng],
        "rsi": [{"time": t[i], "value": r2(rs[i])} for i in rng if rs[i] is not None],
        "markers": marks,
        "levels": {"pivot": r2(pivot), "stop": r2(entry * 0.92), "tp10": r2(entry * 1.1), "tp20": r2(entry * 1.2)},
        "contractions": [x[4] for x in cons[-4:]], "breakout": t[bo] if bo is not None else "", "after": after,
        "focus": max(0, (cons[-4:][0][0] if cons else mi) - lo_i - 30),  # 預設畫面從第一次收縮前 30 天開始
    }


charts = [x for x in (build(*t) for t in targets) if x]
if not charts:
    raise SystemExit("找不到資料")
title = args.title or ("VCP 走勢圖 " + (os.path.basename(args.list).replace(".csv", "") if args.list
                                        else "、".join(f"{x['code']} {x['name']}" for x in charts[:3])))

HTML = r"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root{--bg:#0f1115;--panel:#151821;--grid:#1f2430;--text:#c9d1d9;--muted:#7d8590;--line:#2a3140;--up:#ef4444;--down:#22c55e}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
font:13px/1.4 -apple-system,BlinkMacSystemFont,"PingFang TC","Noto Sans TC",sans-serif}
.app{display:flex;height:100vh}
.side{width:230px;border-right:1px solid var(--line);overflow:auto;flex:none;background:var(--panel)}
.side h1{font-size:14px;margin:0;padding:12px;border-bottom:1px solid var(--line)}
.item{padding:8px 12px;border-bottom:1px solid var(--grid);cursor:pointer}
.item:hover{background:#1b2030}.item.on{background:#22293a;border-left:3px solid #f0b90b;padding-left:9px}
.item b{font-variant-numeric:tabular-nums}.item small{display:block;color:var(--muted)}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.head{padding:8px 12px;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline}
.head .t{font-size:16px;font-weight:600}.head .m{color:var(--muted)}.head .tag{color:#f0b90b}
.panes{flex:1;display:flex;flex-direction:column;min-height:0}
.pane{position:relative;border-bottom:1px solid var(--line);min-height:0}
.pane .chart{position:absolute;inset:0}
.legend{position:absolute;left:8px;top:4px;z-index:3;pointer-events:none;font-size:12px;white-space:nowrap;
font-variant-numeric:tabular-nums;text-shadow:0 0 3px var(--bg)}
.legend span{margin-right:10px}
@media (max-width:760px){.app{flex-direction:column}.side{width:auto;max-height:120px;border-right:0;border-bottom:1px solid var(--line)}
.legend{font-size:11px}}
</style></head><body>
<div class="app">
  <div class="side" id="side"><h1>__TITLE__</h1></div>
  <div class="main">
    <div class="head" id="head"></div>
    <div class="panes">
      <div class="pane" style="flex:46"><div class="chart" id="c0"></div><div class="legend" id="l0"></div></div>
      <div class="pane" style="flex:14"><div class="chart" id="c1"></div><div class="legend" id="l1"></div></div>
      <div class="pane" style="flex:13"><div class="chart" id="c2"></div><div class="legend" id="l2"></div></div>
      <div class="pane" style="flex:14"><div class="chart" id="c3"></div><div class="legend" id="l3"></div></div>
      <div class="pane" style="flex:13"><div class="chart" id="c4"></div><div class="legend" id="l4"></div></div>
    </div>
  </div>
</div>
<script>
const DATA = __DATA__;
const C = {up:'#ef4444', down:'#22c55e', ma10:'#f5f5f5', ma20:'#f0b90b', ma50:'#e879f9', ma150:'#22d3ee', ma200:'#60a5fa',
  v10:'#f0b90b', v50:'#e879f9', k:'#f0b90b', d:'#60a5fa', dif:'#f5f5f5', sig:'#f0b90b', rsi:'#e879f9'};
const LW = LightweightCharts;
const base = (last) => ({autoSize:true, layout:{background:{color:'#0f1115'}, textColor:'#7d8590', fontSize:11},
  grid:{vertLines:{color:'#1a1f2a'}, horzLines:{color:'#1a1f2a'}},
  rightPriceScale:{borderColor:'#2a3140', minimumWidth:70}, timeScale:{borderColor:'#2a3140', visible:last, rightOffset:4},
  crosshair:{mode:LW.CrosshairMode.Normal}, localization:{dateFormat:'yyyy-MM-dd'}, handleScale:{axisPressedMouseMove:{price:false}}});
const charts = [0,1,2,3,4].map(i => LW.createChart(document.getElementById('c'+i), base(i===4)));
const line = (ch, color, w=1, extra={}) => ch.addLineSeries({color, lineWidth:w, priceLineVisible:false, lastValueVisible:false,
  crosshairMarkerVisible:false, ...extra});
const S = {
  candle: charts[0].addCandlestickSeries({upColor:C.up, downColor:C.down, borderUpColor:C.up, borderDownColor:C.down,
    wickUpColor:C.up, wickDownColor:C.down, priceLineVisible:false}),
  ma10: line(charts[0], C.ma10), ma20: line(charts[0], C.ma20), ma50: line(charts[0], C.ma50, 2),
  ma150: line(charts[0], C.ma150), ma200: line(charts[0], C.ma200, 2),
  vol: charts[1].addHistogramSeries({priceFormat:{type:'volume'}, priceLineVisible:false, lastValueVisible:false}),
  v10: line(charts[1], C.v10), v50: line(charts[1], C.v50),
  k: line(charts[2], C.k), d: line(charts[2], C.d),
  osc: charts[3].addHistogramSeries({priceLineVisible:false, lastValueVisible:false}),
  dif: line(charts[3], C.dif), sig: line(charts[3], C.sig),
  rsi: line(charts[4], C.rsi),
};
[[S.k,[20,80]],[S.rsi,[30,50,70]]].forEach(([s,lv]) => lv.forEach(p => s.createPriceLine({price:p, color:'#3a4252',
  lineWidth:1, lineStyle:LW.LineStyle.Dashed, axisLabelVisible:false})));
S.osc.createPriceLine({price:0, color:'#3a4252', lineWidth:1, lineStyle:LW.LineStyle.Solid, axisLabelVisible:false});
let levelLines = [];

// 同步縮放與十字線
let syncing = false;
charts.forEach((ch, i) => ch.timeScale().subscribeVisibleLogicalRangeChange(r => {
  if (syncing || !r) return; syncing = true;
  charts.forEach((o, j) => j !== i && o.timeScale().setVisibleLogicalRange(r)); syncing = false;
}));
const firstSeries = [S.candle, S.vol, S.k, S.dif, S.rsi];
let idx = {};
charts.forEach((ch, i) => ch.subscribeCrosshairMove(p => {
  const t = p && p.time;
  charts.forEach((o, j) => {
    if (j === i || !o.setCrosshairPosition) return;
    if (t && idx[t] !== undefined) o.setCrosshairPosition(NaN, t, firstSeries[j]); else o.clearCrosshairPosition();
  });
  legend(t);
}));

const f = (x, d=2) => x === undefined || x === null ? '-' : Number(x).toFixed(d);
const sp = (c, s) => `<span style="color:${c}">${s}</span>`;
let cur = null;
function legend(t) {
  if (!cur) return;
  const i = t && idx[t] !== undefined ? idx[t] : cur.candles.length - 1;
  const g = (arr) => cur.by[arr] && cur.by[arr][cur.candles[i].time];
  const k = cur.candles[i], prev = cur.candles[i-1], chg = prev ? (k.close/prev.close-1)*100 : 0;
  const cc = chg >= 0 ? C.up : C.down;
  document.getElementById('l0').innerHTML = `<span>${k.time}</span>`
    + sp(cc, `開 ${f(k.open)} 高 ${f(k.high)} 低 ${f(k.low)} 收 ${f(k.close)} (${chg>=0?'+':''}${f(chg)}%)`) + '<br>'
    + ['10','20','50','150','200'].map(n => sp(C['ma'+n], `MA${n} ${f(g('ma'+n))}`)).join('');
  const v = cur.vol[i];
  document.getElementById('l1').innerHTML = sp(v.up?C.up:C.down, `量 ${Math.round(v.value).toLocaleString()} 張`)
    + sp(C.v10, `均量10 ${Math.round(g('v10')||0).toLocaleString()}`) + sp(C.v50, `均量50 ${Math.round(g('v50')||0).toLocaleString()}`);
  document.getElementById('l2').innerHTML = sp('#7d8590','KD(9,3,3)') + sp(C.k, `K ${f(g('k'))}`) + sp(C.d, `D ${f(g('d'))}`);
  const o = g('osc');
  document.getElementById('l3').innerHTML = sp('#7d8590','MACD(12,26,9)') + sp(C.dif, `DIF ${f(g('dif'))}`)
    + sp(C.sig, `MACD ${f(g('sig'))}`) + sp(o>=0?C.up:C.down, `OSC ${f(o)}`);
  document.getElementById('l4').innerHTML = sp('#7d8590','RSI') + sp(C.rsi, `RSI14 ${f(g('rsi'))}`);
}

function show(n) {
  cur = DATA[n];
  document.querySelectorAll('.item').forEach((e, j) => e.classList.toggle('on', j === n));
  S.candle.setData(cur.candles); S.candle.setMarkers(cur.markers);
  ['10','20','50','150','200'].forEach(m => S['ma'+m].setData(cur.ma[m] || []));
  S.vol.setData(cur.vol.map(x => ({time:x.time, value:x.value, color: x.up ? 'rgba(239,68,68,.7)' : 'rgba(34,197,94,.7)'})));
  S.v10.setData(cur.vma['10'] || []); S.v50.setData(cur.vma['50'] || []);
  S.k.setData(cur.k); S.d.setData(cur.d);
  S.osc.setData(cur.osc.map(x => ({...x, color: x.value >= 0 ? 'rgba(239,68,68,.75)' : 'rgba(34,197,94,.75)'})));
  S.dif.setData(cur.dif); S.sig.setData(cur.sig); S.rsi.setData(cur.rsi);
  levelLines.forEach(l => S.candle.removePriceLine(l)); levelLines = [];
  const L = cur.levels, add = (price, color, title, style=LW.LineStyle.Dashed) => price && levelLines.push(
    S.candle.createPriceLine({price, color, lineWidth:1, lineStyle:style, axisLabelVisible:true, title}));
  add(L.pivot, '#f0b90b', '突破點', LW.LineStyle.Solid);
  if (cur.breakout) { add(L.stop, '#22c55e', '停損 -8%'); add(L.tp10, '#ef4444', '+10%'); add(L.tp20, '#ef4444', '+20%'); }
  idx = {}; cur.candles.forEach((c, i) => idx[c.time] = i);
  cur.by = {};
  const put = (name, arr) => { cur.by[name] = {}; arr.forEach(x => cur.by[name][x.time] = x.value); };
  ['10','20','50','150','200'].forEach(m => put('ma'+m, cur.ma[m] || []));
  put('v10', cur.vma['10'] || []); put('v50', cur.vma['50'] || []);
  put('k', cur.k); put('d', cur.d); put('dif', cur.dif); put('sig', cur.sig); put('osc', cur.osc); put('rsi', cur.rsi);
  const inf = cur.info, bits = [];
  if (cur.contractions.length) bits.push(`收縮 ${cur.contractions.map(x=>x+'%').join(' → ')}`);
  if (cur.breakout) bits.push(`突破 ${cur.breakout}`);
  if (cur.after) bits.push(`突破後 60 日內最高 <span class="tag">+${cur.after.max60}%</span>`);
  if (inf.rs) bits.push(`RS ${inf.rs}`);
  if (inf.rev_3m_yoy !== '') bits.push(`營收年增 ${inf.rev_3m_yoy}%`);
  if (inf.stage) bits.push(inf.stage);
  document.getElementById('head').innerHTML = `<span class="t">${cur.code} ${cur.name}</span>`
    + `<span class="m">${cur.industry ? '產業 '+cur.industry+'　' : ''}${cur.mark ? '標記日 '+cur.mark : ''}</span>`
    + bits.map(b => `<span>${b}</span>`).join('');
  const r = {from: cur.focus, to: cur.candles.length + 3};
  charts.forEach(c => c.timeScale().setVisibleLogicalRange(r));
  legend(null);
}

const side = document.getElementById('side');
DATA.forEach((d, n) => {
  const e = document.createElement('div'); e.className = 'item';
  e.innerHTML = `<b>${d.code}</b> ${d.name}<small>${d.breakout ? '突破 '+d.breakout : (d.info.stage || d.mark || '')}`
    + `${d.after ? '　60 日最高 +'+d.after.max60+'%' : ''}</small>`;
  e.onclick = () => show(n); side.appendChild(e);
});
if (DATA.length < 2) side.style.display = 'none';
show(0);
</script></body></html>
"""

out = args.out or os.path.join(D, "output", "charts",
                               (os.path.basename(args.list).replace(".csv", "") if args.list
                                else "_".join(x["code"] for x in charts[:5])) + ".html")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    fh.write(HTML.replace("__TITLE__", title).replace("__DATA__", json.dumps(charts, ensure_ascii=False, separators=(",", ":"))))
print(f"完成：{len(charts)} 檔 → {out}")

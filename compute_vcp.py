#!/usr/bin/env python3
"""從 collect_vcp.py 收集的資料計算 VCP 指標並產出候選名單（只用 Python 標準函式庫；查營收時需 --repo）。

以除權息還原後的價格計算：
  均線 MA10/20/50/60/150/200、52 週高低點、RS（全市場相對強度百分位 1–99）、KD(9,3,3)、RSI(14)、
  MACD(12,26,9)、量能、VCP 收縮序列與突破點、產業強度、大盤狀態。

篩選（參考 Minervini 趨勢模板，並加入 J Law 的調整）：
  趨勢模板（Stage 2）：收盤 > MA150 > MA200、MA50 > MA150、收盤 > MA50、MA200 比一個月前高、
    距 52 週低點 ≥ +30%、距 52 週高點 ≤ 15%（J Law；Minervini 原版 25%，可用 --max-from-high 調整）、RS ≥ 70、
    20 日均成交金額 ≥ 5,000 萬元（--min-value）。
  產業強度（J Law 由上而下）：各產業成員 RS 中位數排名前 20% 為「強勢產業」；股票 RS 在產業內前 3 名為「產業領頭」。
  大盤狀態：加權指數相對 MA50／MA200，寫在摘要最上方（大盤轉弱時突破的成功率會下降）。

名單：
  A 正在形成 VCP：趨勢模板＋收縮逐次變小（≥2 次，最後一次 ≤ 12%）＋量縮（或剛放量突破）＋
    收盤在突破點下方 8% 內（接近突破點）或剛放量突破 5% 內。依 tech_score 排序。
  C 突破後回踩（J Law 買點）：趨勢模板＋最近 3–20 個交易日曾從 VCP 基底放量突破（量 ≥ 50 日均量 1.5 倍）＋
    之後從高點拉回 ≥ 3%、收盤守在突破位 -3% 以上，且回到突破位或 10／20 日均線附近＋回踩縮量（近 3 日均量 < 50 日均量）。
    附停損參考價（突破位與 MA20 較低者再下 3%）與風險 %。依 tech_score 排序。
  B、D：A、C 名單中月營收「成長」或「加速」者（需 --repo），依 total_score 排序。

tech_score：RS 為底；強勢產業 +10、產業領頭 +5；A：量縮 +10、近 10 日振幅 ≤ 8% +10、距突破點 3% 內 +10、
  每次收縮 +5（最多 4 次）；C：縮量回踩 +10、守住支撐 +10、近 10 日振幅 ≤ 8% +10、突破量 ≥ 2 倍 +5。
月營收（只查 A、C 名單）：近 3 個月年增率都 > 0＝成長（+8）；成長且最新月年增率 > 前 3 個月平均＝加速（+15）；
  最新月年增率 < 0＝衰退（-10）。total_score＝tech_score＋營收分。

用法：
  python3 compute_vcp.py --data ~/vcp-data                                   # 只算技術面（A、C）
  uv run python compute_vcp.py --data ~/vcp-data --repo ~/TWSEMCPServer       # 再加營收（B、D）
  可加 --date YYYYMMDD、--max-from-high 25、--min-value 2e7、--base-days 120、--near-pivot 8
輸出（<data>/output/）：
  indicators_YYYYMMDD.csv            全市場指標
  vcp_forming_YYYYMMDD.csv           A 正在形成 VCP
  vcp_forming_revenue_YYYYMMDD.csv   B＝A＋營收成長（需 --repo）
  vcp_pullback_YYYYMMDD.csv          C 突破後回踩
  vcp_pullback_revenue_YYYYMMDD.csv  D＝C＋營收成長（需 --repo）
  summary_YYYYMMDD.md                大盤狀態、強勢產業與各名單前 20 名（手機好讀）
"""
import argparse, csv, glob, json, os, statistics, sys, time
from collections import defaultdict

p = argparse.ArgumentParser()
p.add_argument("--data", required=True)
p.add_argument("--date", default="")
p.add_argument("--repo", default="", help="TWSEMCPServer 資料夾；有給才會對候選股查月營收（MOPS）")
p.add_argument("--min-value", type=float, default=5e7, help="20 日平均成交金額下限（元），預設 5,000 萬")
p.add_argument("--max-from-high", type=float, default=15.0, help="距 52 週高點最多幾 %%，預設 15（J Law）")
p.add_argument("--base-days", type=int, default=120, help="往回找收縮的交易日數，預設 120（約 6 個月）")
p.add_argument("--near-pivot", type=float, default=8.0, help="收盤距突破點幾 %% 內算接近突破，預設 8")
args = p.parse_args()
D = os.path.abspath(os.path.expanduser(args.data))

INDUSTRY_NAMES = {
    "01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷",
    "09": "造紙", "10": "鋼鐵", "11": "橡膠", "12": "汽車", "14": "建材營造", "15": "航運", "16": "觀光餐旅",
    "17": "金融保險", "18": "貿易百貨", "19": "綜合", "20": "其他", "21": "化學", "22": "生技醫療", "23": "油電燃氣",
    "24": "半導體", "25": "電腦及週邊設備", "26": "光電", "27": "通信網路", "28": "電子零組件", "29": "電子通路",
    "30": "資訊服務", "31": "其他電子", "32": "文化創意", "33": "農業科技", "34": "電子商務", "35": "綠能環保",
    "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "80": "管理股票", "91": "存託憑證",
}


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


# ---------- 讀資料 ----------
bars = defaultdict(dict)  # code -> {date: [o,h,l,c,v,value]}
names, market = {}, {}
for mk in ("twse", "tpex"):
    for f in sorted(glob.glob(os.path.join(D, mk, "*.csv"))):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            c = num(r["close"])
            if c is None:
                continue
            o, h, l = num(r["open"]) or c, num(r["high"]) or c, num(r["low"]) or c
            bars[r["code"]][r["date"]] = [o, h, l, c, num(r["volume"]) or 0, num(r["value"]) or 0]
            names[r["code"]], market[r["code"]] = r["name"], ("上市" if mk == "twse" else "上櫃")
all_dates = sorted({d for b in bars.values() for d in b})
if not all_dates:
    raise SystemExit("沒有日線資料")
asof = args.date or all_dates[-1]

industry = {}
ind_path = os.path.join(D, "industry.csv")
if os.path.exists(ind_path):
    for r in csv.DictReader(open(ind_path, encoding="utf-8")):
        industry[r["code"]] = r["industry"]

factors = defaultdict(list)  # code -> [(exdate, ratio)]
for f in glob.glob(os.path.join(D, "exright", "*.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8")):
        cb, ref = num(r["close_before"]), num(r["reference"])
        if cb and ref and 0 < ref < cb:
            factors[r["code"]].append((r["date"], ref / cb))


def series(code):
    ds = [d for d in sorted(bars[code]) if d <= asof]
    rows = [list(bars[code][d]) for d in ds]
    for exd, ratio in factors.get(code, []):
        for i, d in enumerate(ds):
            if d < exd:
                for k in range(4):
                    rows[i][k] *= ratio
    return ds, rows


# ---------- 指標 ----------
def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def ema_list(xs, n):
    out, k = [], 2 / (n + 1)
    for i, x in enumerate(xs):
        out.append(x if i == 0 else x * k + out[-1] * (1 - k))
    return out


def rsi(cl, n=14):
    if len(cl) <= n:
        return None
    g = [max(cl[i] - cl[i - 1], 0) for i in range(1, len(cl))]
    lo = [max(cl[i - 1] - cl[i], 0) for i in range(1, len(cl))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag, al = (ag * (n - 1) + g[i]) / n, (al * (n - 1) + lo[i]) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def kd(hi, lo, cl, n=9):
    if len(cl) < n:
        return None, None
    k = d = 50.0
    for i in range(n - 1, len(cl)):
        hh, ll = max(hi[i - n + 1:i + 1]), min(lo[i - n + 1:i + 1])
        rsv = 50.0 if hh == ll else (cl[i] - ll) / (hh - ll) * 100
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
    return k, d


def contractions(hi, lo, look, w=4):
    """以左右各 w 天的轉折高低點，找最近 look 天內每次「高點→低點」的回檔幅度(%)與最後一次收縮的高點。"""
    h, l = hi[-look:], lo[-look:]
    piv = []
    for i in range(w, len(h) - w):
        if h[i] == max(h[i - w:i + w + 1]):
            piv.append(("H", i, h[i]))
        if l[i] == min(l[i - w:i + w + 1]):
            piv.append(("L", i, l[i]))
    piv.sort(key=lambda x: x[1])
    depths, last_high, cur_h = [], None, None
    for t, i, v in piv:
        if t == "H":
            cur_h = v if cur_h is None or v > cur_h else cur_h
        elif cur_h:
            depths.append(round((cur_h - v) / cur_h * 100, 1))
            last_high, cur_h = cur_h, None
    return depths, last_high


def is_tight(dep):
    return (len(dep) >= 2 and all(b <= a * 1.1 for a, b in zip(dep, dep[1:])) and dep[0] <= 35
            and dep[-1] <= 12 and dep[0] > dep[-1])


def find_breakout(hi, lo, cl, vol):
    """最近 3–20 個交易日內，第一個「從收縮逐次變小的基底放量突破」的日子：(距今天數, 突破位, 量倍數) 或 None。"""
    n = len(cl)
    for back in range(20, 2, -1):
        t = n - 1 - back
        if t < 60:
            continue
        dep, pivot = contractions(hi[:t], lo[:t], args.base_days)
        v50 = sum(vol[t - 50:t]) / 50
        if pivot and is_tight(dep) and cl[t] > pivot and cl[t - 1] <= pivot and v50 and vol[t] >= 1.5 * v50:
            return back, pivot, vol[t] / v50
    return None


rows_out, perf, data = [], {}, {}
for code in bars:
    ds, r = series(code)
    if len(r) < 60 or ds[-1] != asof:
        continue
    o, hi, lo, cl, vol, val = (list(x) for x in zip(*r))
    data[code] = (hi, lo, cl, vol)
    c = cl[-1]

    def ret(n):
        return cl[-1] / cl[-1 - n] - 1 if len(cl) > n else None
    if len(cl) > 252:
        perf[code] = 0.4 * ret(63) + 0.2 * ret(126) + 0.2 * ret(189) + 0.2 * ret(252)
    elif len(cl) > 126:
        perf[code] = 0.6 * ret(63) + 0.4 * ret(126)
    e12, e26 = ema_list(cl, 12), ema_list(cl, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    sig = ema_list(dif, 9)
    k, d = kd(hi, lo, cl)
    ma = {n: sma(cl, n) for n in (10, 20, 50, 60, 150, 200)}
    ma200_prev = sum(cl[-221:-21]) / 200 if len(cl) >= 221 else None
    h52, l52 = max(hi[-250:]), min(lo[-250:])
    depths, pivot = contractions(hi, lo, args.base_days)
    v10, v50 = sma(vol, 10), sma(vol, 50)
    ind = industry.get(code, "")
    rows_out.append(dict(
        code=code, name=names[code], market=market[code], industry=INDUSTRY_NAMES.get(ind, ind), close=c,
        ma10=ma[10], ma20=ma[20], ma50=ma[50], ma60=ma[60], ma150=ma[150], ma200=ma[200],
        ma200_up_1m=(ma[200] > ma200_prev) if ma[200] and ma200_prev else None,
        high52=h52, low52=l52, pct_from_high=(c / h52 - 1) * 100, pct_above_low=(c / l52 - 1) * 100,
        k=k, d=d, rsi14=rsi(cl), dif=dif[-1], macd=sig[-1], osc=dif[-1] - sig[-1],
        vol_ratio_10_50=(v10 / v50) if v50 else None, vol3_vs50=(sma(vol, 3) / v50) if v50 else None,
        vol_today_vs50=(vol[-1] / v50) if v50 else None, avg_value20=sma(val, 20),
        contractions=" > ".join(map(str, depths[-4:])), pivot=pivot,
        pct_to_pivot=(c / pivot - 1) * 100 if pivot else None,
        range10=(max(hi[-10:]) - min(lo[-10:])) / c * 100, days=len(cl)))

# ---------- RS、產業強度 ----------
ranked = sorted((v, c) for c, v in perf.items() if v is not None)
rs_rank = {c: max(1, min(99, round((i + 1) / len(ranked) * 99))) for i, (_, c) in enumerate(ranked)}
by_ind = defaultdict(list)
for r in rows_out:
    r["rs"] = rs_rank.get(r["code"])
    if r["industry"] and r["rs"] is not None:
        by_ind[r["industry"]].append(r)
ind_med = {k: statistics.median(x["rs"] for x in v) for k, v in by_ind.items() if len(v) >= 3}
ind_sorted = sorted(ind_med, key=lambda k: -ind_med[k])
strong_inds = set(ind_sorted[:max(1, round(len(ind_sorted) * 0.2))]) if ind_sorted else set()
leaders = {x["code"] for v in by_ind.values() for x in sorted(v, key=lambda x: -x["rs"])[:3]}

# ---------- 大盤狀態 ----------
idx = {}
for f in sorted(glob.glob(os.path.join(D, "index", "taiex_*.csv"))):
    for r in csv.DictReader(open(f, encoding="utf-8")):
        if r["date"] <= asof and num(r["close"]):
            idx[r["date"]] = num(r["close"])
ic = [idx[d] for d in sorted(idx)]
if len(ic) >= 200:
    i50, i200 = sma(ic, 50), sma(ic, 200)
    i200p = sum(ic[-221:-21]) / 200 if len(ic) >= 221 else None
    if ic[-1] > i50 > i200 and i200p is not None and i200 > i200p:
        market_state = f"多頭（加權 {ic[-1]:,.0f} > MA50 {i50:,.0f} > MA200 {i200:,.0f}）：適合找突破"
    elif ic[-1] < i200:
        market_state = f"偏空（加權 {ic[-1]:,.0f} < MA200 {i200:,.0f}）：突破失敗率高，宜降低部位或觀望"
    else:
        market_state = f"整理（加權 {ic[-1]:,.0f}，MA50 {i50:,.0f}，MA200 {i200:,.0f}）：精選個股、控制部位"
else:
    market_state = "加權指數資料不足 200 天，無法判斷"

# ---------- 篩選 ----------
EXTRA = ("breakout_days_ago", "breakout_pivot", "breakout_vol_x", "pullback_from_high", "support", "stop_ref", "risk_pct")
forming, pullback = [], []
for r in rows_out:
    m50, m150, m200 = r["ma50"], r["ma150"], r["ma200"]
    r["strong_industry"] = r["industry"] in strong_inds
    r["industry_leader"] = r["code"] in leaders
    trend = bool(m50 and m150 and m200 and r["close"] > m150 > m200 and m50 > m150 and r["close"] > m50
                 and r["ma200_up_1m"] and r["pct_above_low"] >= 30 and r["pct_from_high"] >= -args.max_from_high
                 and (r["rs"] or 0) >= 70 and (r["avg_value20"] or 0) >= args.min_value)
    r["trend_template"] = trend
    dep = [float(x) for x in r["contractions"].split(" > ")] if r["contractions"] else []
    tight, dry = is_tight(dep), (r["vol_ratio_10_50"] or 9) < 1
    near = r["pct_to_pivot"] is not None and -args.near_pivot <= r["pct_to_pivot"] <= 0
    broke = r["pct_to_pivot"] is not None and 0 < r["pct_to_pivot"] <= 5 and (r["vol_today_vs50"] or 0) >= 1.5
    r["contracting"], r["volume_dry"] = tight, dry
    base = (r["rs"] or 0) + (10 if r["strong_industry"] else 0) + (5 if r["industry_leader"] else 0)
    r["stage"] = "剛突破（放量）" if broke else ("接近突破點" if near else "")
    r["tech_score"] = base + (10 if dry else 0) + (10 if r["range10"] <= 8 else 0) \
        + (10 if near and r["pct_to_pivot"] >= -3 else 0) + 5 * min(len(dep), 4)
    for k in EXTRA:
        r[k] = ""
    if not trend:
        r["stage"] = ""
        continue
    hi, lo, cl, vol = data[r["code"]]
    bo = find_breakout(hi, lo, cl, vol)  # 近 3–20 日已突破的，歸到回踩名單判斷，不算「正在形成」
    if not bo:
        if tight and (dry or broke) and r["stage"]:
            forming.append(r)
        else:
            r["stage"] = ""
        continue
    r["stage"] = ""
    back, bpiv, bvx = bo
    c = r["close"]
    pull = (c / max(hi[-back - 1:]) - 1) * 100
    near_piv = -3 <= (c / bpiv - 1) * 100 <= 3
    near10 = bool(r["ma10"]) and abs(c / r["ma10"] - 1) <= 0.02
    near20 = bool(r["ma20"]) and abs(c / r["ma20"] - 1) <= 0.02
    if pull <= -3 and c >= bpiv * 0.97 and (near_piv or near10 or near20) and (r["vol3_vs50"] or 9) < 1:
        stop = min(bpiv, r["ma20"] or bpiv) * 0.97
        r.update(stage="突破後回踩", breakout_days_ago=back, breakout_pivot=bpiv, breakout_vol_x=bvx,
                 pullback_from_high=pull, stop_ref=stop, risk_pct=(c / stop - 1) * 100,
                 support="、".join(x for x, ok in (("突破位", near_piv), ("MA10", near10), ("MA20", near20)) if ok))
        r["tech_score"] = base + 20 + (10 if r["range10"] <= 8 else 0) + (5 if bvx >= 2 else 0)
        pullback.append(r)

# ---------- 月營收（只查 A、C 名單） ----------
REV_POINTS = {"加速": 15, "成長": 8, "衰退": -10}
REV_COLS = ("rev_month", "rev_yoy", "rev_yoy_3m", "rev_3m_yoy", "rev_growth")


def revenue_reports(code, client, monthly_revenue, cache_dir):
    """最近 4 個月的 [年月, 本月, 去年同期, 年增率%]（新到舊）；過去月份存快取，最新月份每次重查。"""
    def parse(rep):
        get = lambda lab: next((i.value for i in rep.items if i.label == lab), "")
        yoy = next((num(i.value) for i in rep.items if i.label == "增減百分比"), None)
        return [f"{rep.period[0]}{rep.period[1]:02d}", num(get("本月")), num(get("去年同期")), yoy]
    latest = monthly_revenue(client, code)
    time.sleep(3)
    if latest is None or latest.period is None or not latest.items:
        return []
    out = [parse(latest)]
    y, m = latest.period
    for _ in range(3):
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        path = os.path.join(cache_dir, f"{code}_{y}{m:02d}.json")
        if os.path.exists(path):
            out.append(json.load(open(path)))
            continue
        rep = monthly_revenue(client, code, y, m)
        time.sleep(3)
        if rep is None or not rep.items:
            break
        row = parse(rep)
        json.dump(row, open(path, "w"))
        out.append(row)
    return out


def add_revenue(cands):
    import logging
    sys.path.insert(0, os.path.abspath(os.path.expanduser(args.repo)))
    logging.disable(logging.CRITICAL)
    from utils.api_client import TWSEAPIClient
    from sources.mops.monthly_revenue import monthly_revenue
    client, cache_dir, fails = TWSEAPIClient(), os.path.join(D, "revenue"), 0
    os.makedirs(cache_dir, exist_ok=True)
    for r in cands:
        r["rev_growth"] = "無資料"
        if fails >= 3:
            continue  # MOPS 連續失敗（多半被擋），剩下的不查
        try:
            reps = revenue_reports(r["code"], client, monthly_revenue, cache_dir)
            fails = 0
        except Exception as e:
            fails += 1
            print(f"  {r['code']} 月營收查詢失敗：{str(e)[:80]}")
            continue
        yoys = [x[3] for x in reps if x[3] is not None]
        if len(yoys) < 3:
            continue
        r["rev_month"], r["rev_yoy"] = reps[0][0], yoys[0]
        r["rev_yoy_3m"] = " / ".join(f"{v:.1f}" for v in yoys[:3])
        cur3, prev3 = sum(x[1] or 0 for x in reps[:3]), sum(x[2] or 0 for x in reps[:3])
        r["rev_3m_yoy"] = round((cur3 / prev3 - 1) * 100, 1) if prev3 else ""
        growing = all(v > 0 for v in yoys[:3])
        accel = growing and len(yoys) >= 4 and yoys[0] > sum(yoys[1:4]) / 3
        r["rev_growth"] = "加速" if accel else ("成長" if growing else ("衰退" if yoys[0] < 0 else "持平/不穩"))
        print(f"  {r['code']} {r['name']} 月營收 {r['rev_month']} 年增 {r['rev_yoy_3m']} → {r['rev_growth']}")


for r in rows_out:
    for k in REV_COLS:
        r[k] = ""
if args.repo and (forming or pullback):
    print(f"查詢 {len(forming) + len(pullback)} 檔候選股的月營收…")
    add_revenue(forming + pullback)
    if all(r["rev_growth"] == "無資料" for r in forming + pullback):
        print("  ⚠ 全部查不到月營收（MOPS 可能擋下或格式改變），營收名單會是空的；請稍後重跑。")
for r in rows_out:
    r["rev_points"] = REV_POINTS.get(r["rev_growth"], 0)
    r["total_score"] = r["tech_score"] + r["rev_points"]
    for k, v in list(r.items()):
        if isinstance(v, float):
            r[k] = round(v, 2)

# ---------- 輸出 ----------
def grow(lst):
    return [r for r in lst if r["rev_growth"] in ("加速", "成長")]


def by(lst, key):
    return sorted(lst, key=lambda r: -r[key])


lists = [("A 正在形成 VCP（技術面）", f"vcp_forming_{asof}.csv", by(forming, "tech_score"), "tech_score", False)]
if args.repo:
    lists.append(("B 正在形成 VCP＋營收成長", f"vcp_forming_revenue_{asof}.csv",
                  by(grow(forming), "total_score"), "total_score", False))
lists.append(("C 突破後回踩（技術面）", f"vcp_pullback_{asof}.csv", by(pullback, "tech_score"), "tech_score", True))
if args.repo:
    lists.append(("D 突破後回踩＋營收成長", f"vcp_pullback_revenue_{asof}.csv",
                  by(grow(pullback), "total_score"), "total_score", True))

out_dir = os.path.join(D, "output")
os.makedirs(out_dir, exist_ok=True)
KEY = ["code", "name", "market", "industry", "stage", "close", "pivot", "pct_to_pivot", "contractions",
       "breakout_days_ago", "breakout_pivot", "breakout_vol_x", "pullback_from_high", "support", "stop_ref", "risk_pct",
       "range10", "vol_ratio_10_50", "vol3_vs50", "rs", "strong_industry", "industry_leader", "tech_score",
       "rev_growth", "rev_yoy_3m", "rev_3m_yoy", "rev_points", "total_score"]
cols = (KEY + [k for k in rows_out[0] if k not in KEY]) if rows_out else KEY
for fn, lst in [(f"indicators_{asof}.csv", by(rows_out, "tech_score"))] + [(x[1], x[2]) for x in lists]:
    with open(os.path.join(out_dir, fn), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(lst)


def md_table(lst, score, pull):
    if not lst:
        return "（無）\n"
    if pull:
        head = ["代號", "名稱", "產業", "收盤", "突破位", "幾天前突破", "拉回 %", "支撐", "停損參考", "風險 %", "RS", "營收", "分數"]
        keys = ["code", "name", "industry", "close", "breakout_pivot", "breakout_days_ago", "pullback_from_high",
                "support", "stop_ref", "risk_pct", "rs", "rev_growth", score]
    else:
        head = ["代號", "名稱", "產業", "狀態", "收盤", "突破點", "距突破 %", "收縮 %", "RS", "營收", "分數"]
        keys = ["code", "name", "industry", "stage", "close", "pivot", "pct_to_pivot", "contractions", "rs",
                "rev_growth", score]
    lines = ["| # | " + " | ".join(head) + " |", "|" + "---|" * (len(head) + 1)]
    for i, r in enumerate(lst[:20], 1):
        lines.append(f"| {i} | " + " | ".join(str(r[k]) if r[k] not in ("", None) else "-" for k in keys) + " |")
    return "\n".join(lines) + "\n"


md = [f"# VCP 候選股 {asof}\n", f"**大盤**：{market_state}\n",
      "**強勢產業**（成員 RS 中位數前 20%）：" + (
          "、".join(f"{k}（{ind_med[k]:.0f}）" for k in ind_sorted[:len(strong_inds)]) or "無產業資料") + "\n",
      f"全市場計算 {len(rows_out)} 檔；" + "；".join(f"{t.split('（')[0]} {len(l)} 檔" for t, _, l, _, _ in lists)
      + ("" if args.repo else "（未查營收：沒有加 --repo）") + "\n"]
for title, _, lst, score, pull in lists:
    md += [f"\n## {title}\n", md_table(lst, score, pull)]
md.append("\n停損參考＝突破位與 MA20 較低者再下 3%；風險 %＝收盤到停損參考的距離。名單是規則初篩，進場前請看線圖確認。\n")
open(os.path.join(out_dir, f"summary_{asof}.md"), "w", encoding="utf-8").write("\n".join(md))
print(f"{asof}：大盤 {market_state}")
print("；".join(f"{t} {len(l)} 檔" for t, _, l, _, _ in lists) + f" → {out_dir}")

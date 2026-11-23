# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pandas"]
# ///
"""VCP 突破回測：用 collect_vcp.py 收集的日線，找出過去每一次「從收縮基底放量突破」，統計之後的報酬。

規則與 compute_vcp.py 一致（趨勢模板、收縮判斷、放量突破、突破後回踩），但先用「寬鬆」條件抓事件，
再按各條件分組比較，方便判斷門檻要不要調整。

進場方式：
  突破進場：突破當日收盤買進，停損 -8%（盤中觸及即出），收盤跌破 MA50 出場，最多持有 250 個交易日。
  回踩進場（C）：突破後 3–20 日第一次符合回踩條件的收盤買進，停損＝min(突破位, MA20) × 0.97，其餘同上。
交易報酬已扣來回成本（預設 0.6%：手續費 0.1425% × 2 ＋ 證交稅 0.3%）。

用法：
  uv run backtest_vcp.py --data vcp-data
輸出：
  vcp-data/output/backtest_events.csv   每一次事件與之後報酬
  vcp-data/output/backtest_summary.md   分組統計
"""
import argparse, glob, os, re
from collections import defaultdict

import numpy as np
import pandas as pd

p = argparse.ArgumentParser()
p.add_argument("--data", required=True)
p.add_argument("--out", default="", help="輸出資料夾，預設 <data>/output")
p.add_argument("--base-days", type=int, default=120)
p.add_argument("--loose-from-high", type=float, default=25.0, help="寬鬆：距 52 週高點最多幾 %%")
p.add_argument("--loose-rs", type=float, default=60.0, help="寬鬆：RS 下限")
p.add_argument("--loose-value", type=float, default=3e7, help="寬鬆：20 日均成交金額下限")
p.add_argument("--stop", type=float, default=8.0, help="突破進場停損 %%")
p.add_argument("--cost", type=float, default=0.6, help="來回交易成本 %%")
args = p.parse_args()
D = os.path.abspath(os.path.expanduser(args.data))
OUT = args.out or os.path.join(D, "output")
os.makedirs(OUT, exist_ok=True)
COST = args.cost / 100


def num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")


# ---------- 讀日線 ----------
frames = []
for mk in ("twse", "tpex"):
    for f in sorted(glob.glob(os.path.join(D, mk, "*.csv"))):
        frames.append(pd.read_csv(f, dtype=str))
df = pd.concat(frames, ignore_index=True)
df = df[df["code"].str.fullmatch(r"\d{4}")]  # 只看一般股票（4 碼）
for col in ("open", "high", "low", "close", "volume", "value"):
    df[col] = num(df[col])
df = df[df["close"].notna() & (df["close"] > 0)]
for col in ("open", "high", "low"):
    df[col] = df[col].where(df[col] > 0, df["close"])
df[["volume", "value"]] = df[["volume", "value"]].fillna(0)
df["date"] = df["date"].astype(int)
df = df.sort_values(["code", "date"]).drop_duplicates(["code", "date"])
names = df.groupby("code")["name"].last().to_dict()
print(f"日線 {len(df):,} 筆，{df['code'].nunique()} 檔", flush=True)

# ---------- 除權息還原 ----------
factors = defaultdict(list)
for f in glob.glob(os.path.join(D, "exright", "*.csv")):
    ex = pd.read_csv(f, dtype=str)
    for code, d, cb, ref in zip(ex["code"], ex["date"], num(ex["close_before"]), num(ex["reference"])):
        if cb and ref and 0 < ref < cb:
            factors[code].append((int(d), ref / cb))

# ---------- 大盤狀態 ----------
idx = []
for f in sorted(glob.glob(os.path.join(D, "index", "taiex_*.csv"))):
    x = pd.read_csv(f, dtype=str)
    idx.append(pd.DataFrame({"date": x["date"].astype(int), "close": num(x["close"])}))
idx = pd.concat(idx).dropna().drop_duplicates("date").sort_values("date").set_index("date")["close"]
i50, i200 = idx.rolling(50).mean(), idx.rolling(200).mean()
i200p = i200.shift(21)
mstate = pd.Series(np.where(idx < i200, "偏空", np.where((idx > i50) & (i50 > i200) & (i200 > i200p), "多頭", "整理")),
                   index=idx.index)
mstate[i200.isna()] = ""

# ---------- 個股指標 ----------
stocks, perf = {}, {}
for code, g in df.groupby("code", sort=False):
    if len(g) < 260:
        continue
    dates = g["date"].to_numpy()
    o, h, l, c = (g[k].to_numpy(dtype=float).copy() for k in ("open", "high", "low", "close"))
    adj = np.ones(len(g))
    for exd, ratio in factors.get(code, []):
        adj[dates < exd] *= ratio
    o, h, l, c = o * adj, h * adj, l * adj, c * adj
    v, val = g["volume"].to_numpy(dtype=float), g["value"].to_numpy(dtype=float)
    cs, vs = pd.Series(c), pd.Series(v)
    s = dict(dates=dates, o=o, h=h, l=l, c=c, v=v)
    for n in (10, 20, 50, 150, 200):
        s[f"ma{n}"] = cs.rolling(n).mean().to_numpy()
    s["ma200p"] = pd.Series(s["ma200"]).shift(21).to_numpy()
    s["h52"] = pd.Series(h).rolling(250, min_periods=1).max().to_numpy()
    s["l52"] = pd.Series(l).rolling(250, min_periods=1).min().to_numpy()
    s["v50"] = vs.rolling(50).mean().to_numpy()
    s["v50prev"] = vs.rolling(50).mean().shift(1).to_numpy()
    s["v3"] = vs.rolling(3).mean().to_numpy()
    s["val20"] = pd.Series(val).rolling(20).mean().to_numpy()
    s["h20"] = pd.Series(h).rolling(20).max().to_numpy()
    # KD(9)：K、D 以 1/3 平滑，起始 50（台股常用）
    hh9, ll9 = pd.Series(h).rolling(9).max(), pd.Series(l).rolling(9).min()
    rsv = ((cs - ll9) / (hh9 - ll9) * 100).where(hh9 > ll9, 50).fillna(50)
    kk = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    s["k"], s["d"] = kk.to_numpy(), kk.ewm(alpha=1 / 3, adjust=False).mean().to_numpy()
    # RSI(14)，Wilder 平滑
    dl = cs.diff()
    ag = dl.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    al = (-dl.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    s["rsi"] = (100 - 100 / (1 + ag / al)).where(al > 0, 100).to_numpy()
    # MACD(12,26,9)，DIF、OSC 以收盤價百分比表示，方便跨股票比較
    dif = cs.ewm(span=12, adjust=False).mean() - cs.ewm(span=26, adjust=False).mean()
    osc = dif - dif.ewm(span=9, adjust=False).mean()
    s["dif"], s["osc"] = (dif / cs * 100).to_numpy(), (osc / cs * 100).to_numpy()
    r = {k: cs / cs.shift(k) - 1 for k in (63, 126, 189, 252)}
    i = np.arange(len(c))
    pf = np.where(i >= 252, 0.4 * r[63] + 0.2 * r[126] + 0.2 * r[189] + 0.2 * r[252],
                  np.where(i >= 126, 0.6 * r[63] + 0.4 * r[126], np.nan))
    perf[code] = pd.Series(pf, index=dates)
    stocks[code] = s
print(f"可計算 {len(stocks)} 檔", flush=True)

pf = pd.DataFrame(perf)
rs_df = (pf.rank(axis=1, method="first").div(pf.notna().sum(axis=1), axis=0) * 99).round().clip(1, 99)
print("RS 完成", flush=True)


# ---------- 月營收（collect_revenue.py） ----------
def month_index(ym):
    return (ym // 100) * 12 + ym % 100


rev = defaultdict(list)  # code -> [(公布日, 月份序號, 本月, 去年同期, 年增率)]
for f in glob.glob(os.path.join(D, "revenue_monthly", "*.csv")):
    x = pd.read_csv(f, dtype=str)
    if x.empty:
        continue
    ym = int(os.path.basename(f).split("_")[1][:6])
    y, m = divmod(ym, 100)
    pub = (y + (m == 12)) * 10000 + (m % 12 + 1) * 100 + 10  # 次月 10 日
    for code, cur, ly, yoy in zip(x["code"], num(x["revenue"]), num(x["last_year"]), num(x["yoy_pct"])):
        if pd.isna(yoy) and cur and ly:
            yoy = (cur / ly - 1) * 100
        rev[code].append((pub, month_index(ym), cur, ly, yoy))
for v in rev.values():
    v.sort(key=lambda z: z[1])
print(f"月營收 {len(rev)} 家", flush=True)


def rev_info(code, d):
    """d（YYYYMMDD）當天已公布、且在 6 個月內的最近 4 個月營收 →
    {rev_growth: 加速／成長／衰退／持平/不穩／無資料（同 compute_vcp.py）, rev_yoy: 最新月年增率, rev_yoy3: 近 3 月合計年增率}"""
    due = (d // 10000) * 12 + (d // 100) % 100 - (1 if d % 100 > 10 else 2)
    reps = [z for z in rev.get(code, []) if z[0] <= d and due - z[1] < 6][-4:][::-1]
    yoys = [z[4] for z in reps if pd.notna(z[4])]
    out = {"rev_growth": "無資料", "rev_yoy": np.nan, "rev_yoy3": np.nan}
    if len(yoys) < 3:
        return out
    cur3, ly3 = sum(z[2] or 0 for z in reps[:3]), sum(z[3] or 0 for z in reps[:3])
    growing = all(v > 0 for v in yoys[:3])
    if growing and len(yoys) >= 4 and yoys[0] > sum(yoys[1:4]) / 3:
        g = "加速"
    else:
        g = "成長" if growing else ("衰退" if yoys[0] < 0 else "持平/不穩")
    return {"rev_growth": g, "rev_yoy": yoys[0], "rev_yoy3": (cur3 / ly3 - 1) * 100 if ly3 else np.nan}


# ---------- 收縮（與 compute_vcp.py 相同） ----------
def contractions(hi, lo, look, w=4):
    h, l = hi[-look:], lo[-look:]
    piv = []
    for i in range(w, len(h) - w):
        if h[i] == h[i - w:i + w + 1].max():
            piv.append(("H", i, h[i]))
        if l[i] == l[i - w:i + w + 1].min():
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
    dep = dep[-4:]  # 只看最近 4 次回檔（同 compute_vcp.py）
    return (len(dep) >= 2 and all(b <= a * 1.1 for a, b in zip(dep, dep[1:])) and dep[0] <= 35
            and dep[-1] <= 12 and dep[0] > dep[-1])


def loose_tight(dep):
    return len(dep) >= 2 and dep[-1] <= 15 and dep[0] > dep[-1] and dep[0] <= 40


def simulate(s, e, entry, stop):
    """從第 e 天收盤 entry 買進；停損 stop；收盤跌破 MA50 出場；最多 250 天。回傳 (報酬, 持有天數, 出場原因)。"""
    n = len(s["c"])
    j = e
    for j in range(e + 1, min(n, e + 251)):
        if s["o"][j] <= stop:
            return s["o"][j] / entry - 1 - COST, j - e, "停損(跳空)"
        if s["l"][j] <= stop:
            return stop / entry - 1 - COST, j - e, "停損"
        if s["c"][j] < s["ma50"][j]:
            return s["c"][j] / entry - 1 - COST, j - e, "破MA50"
    if j == e:
        return np.nan, 0, "無後續資料"
    return s["c"][j] / entry - 1 - COST, j - e, ("滿250天" if j - e >= 250 else "資料結束")


def forward(s, e):
    c, n = s["c"], len(s["c"])
    out = {f"ret{k}": (c[e + k] / c[e] - 1 if e + k < n else np.nan) for k in (5, 10, 20, 60, 120)}
    out["mfe60"] = s["h"][e + 1:e + 61].max() / c[e] - 1 if e + 1 < n else np.nan
    out["mae60"] = s["l"][e + 1:e + 61].min() / c[e] - 1 if e + 1 < n else np.nan
    return out


def mfe_until_stop(s, e, stop, days=60):
    """進場後 days 個交易日內、碰到停損之前的最高價報酬（停損當天不計）；一進場就停損則為 0 以下的最高點。"""
    best = -np.inf
    for j in range(e + 1, min(len(s["c"]), e + days + 1)):
        if s["l"][j] <= stop:
            break
        best = max(best, s["h"][j] / s["c"][e] - 1)
    return best if best > -np.inf else stop / s["c"][e] - 1


def indicators(s, t):
    return dict(k=s["k"][t], d=s["d"][t], k_up=bool(s["k"][t] > s["k"][t - 1]), k_gt_d=bool(s["k"][t] > s["d"][t]),
                rsi=s["rsi"][t], rsi_up=bool(s["rsi"][t] > s["rsi"][t - 1]), dif=s["dif"][t], osc=s["osc"][t],
                osc_up=bool(s["osc"][t] > s["osc"][t - 1]))


# ---------- 找事件 ----------
events, pb_events = [], []
for k, (code, s) in enumerate(stocks.items()):
    dates, c, h, l, v = s["dates"], s["c"], s["h"], s["l"], s["v"]
    n = len(c)
    rs = rs_df[code].reindex(dates).to_numpy() if code in rs_df else np.full(n, np.nan)
    fh = c / s["h52"] - 1
    with np.errstate(invalid="ignore"):
        stack = ((c > s["ma150"]) & (s["ma150"] > s["ma200"]) & (s["ma50"] > s["ma150"]) & (c > s["ma50"])
                 & (s["ma200"] > s["ma200p"]) & (c / s["l52"] - 1 >= 0.30))
        loose = stack & (fh >= -args.loose_from_high / 100) & (rs >= args.loose_rs) & (s["val20"] >= args.loose_value)
        strict = stack & (fh >= -0.15) & (rs >= 70) & (s["val20"] >= 5e7)
        cand = loose & (v >= 1.5 * s["v50prev"]) & (c > np.roll(c, 1))
    last = -999
    for t in np.nonzero(cand)[0]:
        if t < 130 or t - last < 20:
            continue
        dep, pivot = contractions(h[:t], l[:t], args.base_days)
        if not pivot or not (c[t] > pivot >= c[t - 1]) or not loose_tight(dep):
            continue
        last = t
        tight = is_tight(dep)
        bo_pct = c[t] / pivot - 1
        base = dict(code=code, name=names.get(code, ""), date=int(dates[t]), **rev_info(code, int(dates[t])),
                    market_state=mstate.get(int(dates[t]), ""), rs=rs[t], pct_from_high=fh[t] * 100,
                    avg_value20=s["val20"][t], vol_x=v[t] / s["v50prev"][t], contractions=len(dep),
                    depths=" > ".join(map(str, dep[-4:])), dep_first=dep[0], dep_last=dep[-1],
                    pivot=pivot, breakout_pct=bo_pct * 100, strict_trend=bool(strict[t]), strict_tight=tight)
        base["current_rule"] = bool(strict[t]) and tight and bo_pct <= 0.05 and v[t] < 3 * s["v50prev"][t]
        fail10 = bool((c[t + 1:t + 11] < pivot).any())
        # 突破進場
        ret, hold, why = simulate(s, t, c[t], c[t] * (1 - args.stop / 100))
        events.append(dict(base, entry="突破", entry_date=int(dates[t]), entry_px=c[t], stop_px=c[t] * (1 - args.stop / 100),
                           trade_ret=ret, hold_days=hold, exit_reason=why, fell_back_10d=fail10, **forward(s, t), **indicators(s, t),
                           mfe60_stop=mfe_until_stop(s, t, c[t] * (1 - args.stop / 100))))
        # 回踩進場（C）
        for j in range(t + 3, min(t + 21, n)):
            if not loose[j]:
                continue
            pull = c[j] / h[t:j + 1].max() - 1
            near_piv = abs(c[j] / pivot - 1) <= 0.03
            near10 = abs(c[j] / s["ma10"][j] - 1) <= 0.02
            near20 = abs(c[j] / s["ma20"][j] - 1) <= 0.02
            if pull <= -0.03 and c[j] >= pivot * 0.97 and (near_piv or near10 or near20) and s["v3"][j] / s["v50"][j] < 1:
                stop = min(pivot, s["ma20"][j]) * 0.97
                ret, hold, why = simulate(s, j, c[j], stop)
                b = dict(base, current_rule=bool(strict[t]) and bool(strict[j]) and tight)
                events.append(dict(b, entry="回踩", entry_date=int(dates[j]), entry_px=c[j], stop_px=stop,
                                   trade_ret=ret, hold_days=hold, exit_reason=why, fell_back_10d=fail10, **forward(s, j), **indicators(s, j),
                                   mfe60_stop=mfe_until_stop(s, j, stop)))
                break
    # 量縮回檔進場：基底內回檔 3–15%、近 3 日均量 < 50 日均量 0.7 倍；每段回檔（同一個 20 日高點）只記第一次
    with np.errstate(invalid="ignore", divide="ignore"):
        pb_trend = ((s["ma50"] > s["ma150"]) & (s["ma150"] > s["ma200"]) & (s["ma200"] > s["ma200p"]) & (c > s["ma150"])
                    & (fh >= -args.loose_from_high / 100) & (rs >= args.loose_rs) & (s["val20"] >= args.loose_value))
        pull = c / s["h20"] - 1
        dry = s["v3"] / s["v50"]
        pb_cand = pb_trend & (pull <= -0.03) & (pull >= -0.15) & (dry < 0.7)
    last_h = None
    for t in np.nonzero(pb_cand)[0]:
        if t < 130 or t + 1 >= n or s["h20"][t] == last_h:
            continue
        last_h = H = s["h20"][t]
        stop = c[t] * (1 - args.stop / 100)
        ok, days = False, np.nan
        for j in range(t + 1, min(n, t + 41)):
            if l[j] <= stop:
                break
            if c[j] > H:
                ok, days = True, j - t
                break
        dep, _ = contractions(h[:t + 1], l[:t + 1], args.base_days)
        ret, hold, why = simulate(s, t, c[t], stop)
        pb_events.append(dict(code=code, name=names.get(code, ""), date=int(dates[t]), **rev_info(code, int(dates[t])),
                              market_state=mstate.get(int(dates[t]), ""), rs=rs[t], pct_from_high=fh[t] * 100,
                              strict_trend=bool(strict[t]), above_ma50=bool(c[t] > s["ma50"][t]),
                              pull=pull[t] * 100, dry=dry[t], prior_contractions=len(dep),
                              prev_dep=dep[-1] if dep else np.nan,
                              shallower=bool(dep) and -pull[t] * 100 < dep[-1],
                              success=ok, days_to_breakout=days, trade_ret=ret, hold_days=hold, exit_reason=why,
                              **{f"ret{x}": (c[t + x] / c[t] - 1 if t + x < n else np.nan) for x in (20, 60)},
                              mfe60=h[t + 1:t + 61].max() / c[t] - 1, mfe60_stop=mfe_until_stop(s, t, stop),
                              **indicators(s, t)))
    if k % 300 == 0:
        print(f"  {k}/{len(stocks)}，事件 {len(events)}", flush=True)

ev = pd.DataFrame(events)
ev.to_csv(os.path.join(OUT, "backtest_events.csv"), index=False, encoding="utf-8-sig", float_format="%.4f")
print(f"事件 {len(ev)} 筆", flush=True)


# ---------- 統計 ----------
def stats(x):
    t = x["trade_ret"].dropna()
    if len(t) == 0:
        return None
    w, lo = t[t > 0], t[t <= 0]
    row = {"筆數": len(t), "勝率": f"{len(w) / len(t):.0%}", "平均報酬": f"{t.mean():+.1%}", "中位數": f"{t.median():+.1%}",
           "平均賺": f"{w.mean():+.1%}" if len(w) else "-", "平均賠": f"{lo.mean():+.1%}" if len(lo) else "-",
           "賺賠比": f"{w.mean() / -lo.mean():.2f}" if len(w) and len(lo) and lo.mean() < 0 else "-",
           "持有天數": f"{x['hold_days'].median():.0f}"}
    for k in (20, 60, 120):
        r = x[f"ret{k}"].dropna()
        row[f"{k}日報酬"] = f"{r.mean():+.1%}" if len(r) else "-"
    row["3月內最高(中位)"] = f"{x['mfe60'].median():+.1%}"
    row["停損前達+20%"] = f"{(x['mfe60_stop'] >= 0.2).mean():.0%}"
    row["10日內跌回基底"] = f"{x['fell_back_10d'].mean():.0%}"
    return row


def table(title, groups, note=""):
    rows = []
    for label, x in groups:
        st = stats(x)
        if st:
            rows.append({"分組": label, **st})
    if not rows:
        return f"### {title}\n\n（無資料）\n"
    cols = list(rows[0])
    md = [f"### {title}\n", note + "\n" if note else "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(md) + "\n"


def buckets(x, col, edges, fmt):
    out = []
    for a, b in zip(edges, edges[1:]):
        out.append((fmt(a, b), x[(x[col] >= a) & (x[col] < b)]))
    return out


bo, pb = ev[ev["entry"] == "突破"], ev[ev["entry"] == "回踩"]
cur_bo, cur_pb = bo[bo["current_rule"]], pb[pb["current_rule"]]
span = f"{ev['entry_date'].min()}–{ev['entry_date'].max()}" if len(ev) else "-"
md = [f"# VCP 突破回測\n",
      f"期間：{span}；股票：{len(stocks)} 檔（含已下市）；事件：突破 {len(bo)} 筆、回踩 {len(pb)} 筆。\n",
      f"出場：突破進場停損 -{args.stop:.0f}%、回踩進場停損 min(突破位, MA20)×0.97；收盤跌破 MA50 出場；最多 250 日。"
      f"交易報酬已扣成本 {args.cost}%。「N日報酬」是進場後固定 N 個交易日的報酬（未扣成本、不含停損）。「3月內最高」＝進場後 60 個交易日內最高價的漲幅（理論上限）；「停損前達+20%」＝碰到 -8% 停損前曾漲到 +20% 的比例。\n",
      "「目前規則」＝ compute_vcp.py 預設門檻（距高點 ≤15%、RS ≥70、均額 ≥5,000 萬、最近 4 次收縮逐次變小且最後一次 ≤12%、突破量 1.5–3 倍、突破 ≤5%）。"
      f"其他分組用寬鬆條件（距高點 ≤{args.loose_from_high:.0f}%、RS ≥{args.loose_rs:.0f}、均額 ≥{args.loose_value / 1e7:.0f},000 萬、"
      "最後一次收縮 ≤15%）找事件後再切分。\n",
      "## 總覽\n",
      table("目前規則 vs 寬鬆", [("突破進場－目前規則", cur_bo), ("回踩進場－目前規則", cur_pb),
                                ("突破進場－寬鬆", bo), ("回踩進場－寬鬆", pb)]),
      "## 目前規則：依大盤狀態\n",
      table("突破進場", [(m, cur_bo[cur_bo["market_state"] == m]) for m in ("多頭", "整理", "偏空")]),
      table("回踩進場", [(m, cur_pb[cur_pb["market_state"] == m]) for m in ("多頭", "整理", "偏空")]),
      "## 目前規則：依年度（突破進場）\n",
      table("年度", [(str(y), cur_bo[cur_bo["entry_date"] // 10000 == y]) for y in sorted(set(cur_bo["entry_date"] // 10000))]),
      "## 條件敏感度（寬鬆事件，突破進場）\n",
      table("距 52 週高點", buckets(bo, "pct_from_high", [-25, -15, -10, -5, 0.01], lambda a, b: f"{a:.0f}% ~ {min(b, 0):.0f}%")),
      table("RS", buckets(bo, "rs", [60, 70, 80, 90, 100], lambda a, b: f"{a:.0f}–{b - 1:.0f}")),
      table("突破量倍數（對 50 日均量）", buckets(bo, "vol_x", [1.5, 2, 3, 1e9], lambda a, b: f"{a:g}x 以上" if b > 1e8 else f"{a:g}–{b:g}x")),
      table("收縮次數", [("2 次", bo[bo["contractions"] == 2]), ("3 次", bo[bo["contractions"] == 3]),
                        ("4 次以上", bo[bo["contractions"] >= 4])]),
      table("最後一次收縮深度", buckets(bo, "dep_last", [0, 5, 8, 12, 15.01], lambda a, b: f"{a:.0f}–{min(b, 15):.0f}%")),
      table("最近 4 次收縮逐次變小", [("是", bo[bo["strict_tight"]]), ("否", bo[~bo["strict_tight"]])]),
      table("20 日均成交金額", buckets(bo, "avg_value20", [3e7, 5e7, 2e8, 1e12],
                                      lambda a, b: f"{a / 1e8:g} 億以上" if b > 1e11 else f"{a / 1e8:g}–{b / 1e8:g} 億")),
      table("突破當日離突破位", buckets(bo, "breakout_pct", [0, 2, 5, 100], lambda a, b: f"{a:.0f}% 以上" if b > 50 else f"{a:.0f}–{b:.0f}%")),
      table("大盤狀態", [(m, bo[bo["market_state"] == m]) for m in ("多頭", "整理", "偏空")]),
      "## 月營收（突破當時已公布的最近 4 個月）\n",
      table("突破進場－目前趨勢模板，依近 3 個月合計年增率", buckets(bo[bo["strict_trend"]], "rev_yoy3", [-1e9, 0, 10, 20, 40, 1e9],
            lambda a, b: f"{a:.0f}% 以上" if b > 1e8 else (f"< {b:.0f}%" if a < -1e8 else f"{a:.0f}–{b:.0f}%"))
            + [("≥ 20%", bo[bo["strict_trend"] & (bo["rev_yoy3"] >= 20)]), ("< 20%", bo[bo["strict_trend"] & (bo["rev_yoy3"] < 20)])]),
      table("突破進場－寬鬆", [(g, bo[bo["rev_growth"] == g]) for g in ("加速", "成長", "持平/不穩", "衰退", "無資料")]
            + [("成長或加速", bo[bo["rev_growth"].isin(["加速", "成長"])])]),
      table("突破進場－目前趨勢模板", [(g, bo[bo["strict_trend"] & (bo["rev_growth"] == g)]) for g in ("加速", "成長", "持平/不穩", "衰退")]
            + [("成長或加速", bo[bo["strict_trend"] & bo["rev_growth"].isin(["加速", "成長"])])]),
      table("回踩進場－寬鬆", [(g, pb[pb["rev_growth"] == g]) for g in ("加速", "成長", "持平/不穩", "衰退")]
            + [("成長或加速", pb[pb["rev_growth"].isin(["加速", "成長"])])]),
      "## 出場原因（目前規則，突破進場）\n",
      "\n".join(f"- {k}：{v}" for k, v in cur_bo["exit_reason"].value_counts().items()),
      "\n## 目前規則：報酬最高的 15 筆（突破進場）\n",
      "\n".join(f"- {r.entry_date} {r.code} {r.name}：{r.trade_ret:+.0%}（{r.hold_days} 天，{r.exit_reason}）"
                for r in cur_bo.nlargest(15, "trade_ret").itertuples()),
      ]
open(os.path.join(OUT, "backtest_summary.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
print(f"完成 → {OUT}/backtest_summary.md")


# ---------- 指標特徵（KD、MACD、RSI） ----------
from itertools import combinations

pbv = pd.DataFrame(pb_events)
pbv.to_csv(os.path.join(OUT, "backtest_pullback_events.csv"), index=False, encoding="utf-8-sig", float_format="%.4f")
print(f"量縮回檔事件 {len(pbv)} 筆", flush=True)


def pct(x):
    return f"{x:+.1%}" if pd.notna(x) else "-"


def st_pb(x):
    t = x["trade_ret"].dropna()
    return {"筆數": len(x), "成功突破率": f"{x['success'].mean():.0%}", "交易勝率": f"{(t > 0).mean():.0%}",
            "平均報酬": pct(t.mean()), "中位數": pct(t.median()), "20日報酬": pct(x["ret20"].mean()),
            "60日報酬": pct(x["ret60"].mean()), "3月內最高(中位)": pct(x["mfe60"].median()),
            "停損前達+20%": f"{(x['mfe60_stop'] >= 0.2).mean():.0%}"}


def st_bo(x):
    t = x["trade_ret"].dropna()
    return {"筆數": len(x), "勝率": f"{(t > 0).mean():.0%}", "平均報酬": pct(t.mean()), "中位數": pct(t.median()),
            "大賺(>20%)比例": f"{(t > 0.2).mean():.0%}", "20日報酬": pct(x["ret20"].mean()), "60日報酬": pct(x["ret60"].mean()), "3月內最高(中位)": pct(x["mfe60"].median()),
            "停損前達+20%": f"{(x['mfe60_stop'] >= 0.2).mean():.0%}"}


def md_table(rows):
    if not rows:
        return "（無資料）\n"
    cols = list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(out) + "\n"


IND_GROUPS = {
    "K 值": [("K < 20", lambda x: x.k < 20), ("K 20–40", lambda x: (x.k >= 20) & (x.k < 40)),
             ("K 40–60", lambda x: (x.k >= 40) & (x.k < 60)), ("K 60–80", lambda x: (x.k >= 60) & (x.k < 80)),
             ("K ≥ 80", lambda x: x.k >= 80)],
    "KD 交叉與方向": [("K > D", lambda x: x.k_gt_d), ("K < D", lambda x: ~x.k_gt_d),
                    ("K 上彎", lambda x: x.k_up), ("K 下彎", lambda x: ~x.k_up),
                    ("K < D 但 K 上彎（將黃金交叉）", lambda x: ~x.k_gt_d & x.k_up)],
    "RSI(14)": [("RSI < 40", lambda x: x.rsi < 40), ("RSI 40–50", lambda x: (x.rsi >= 40) & (x.rsi < 50)),
                ("RSI 50–60", lambda x: (x.rsi >= 50) & (x.rsi < 60)), ("RSI 60–70", lambda x: (x.rsi >= 60) & (x.rsi < 70)),
                ("RSI ≥ 70", lambda x: x.rsi >= 70), ("RSI 上升", lambda x: x.rsi_up), ("RSI 下降", lambda x: ~x.rsi_up)],
    "MACD": [("DIF > 0 且 OSC > 0", lambda x: (x.dif > 0) & (x.osc > 0)),
             ("DIF > 0 且 OSC < 0（多頭中回檔）", lambda x: (x.dif > 0) & (x.osc <= 0)),
             ("DIF < 0", lambda x: x.dif <= 0), ("OSC 翻揚（柱狀體變大）", lambda x: x.osc_up),
             ("OSC 下降", lambda x: ~x.osc_up)],
}
PB_GROUPS = {
    "量縮程度（近 3 日均量 / 50 日均量）": [("< 0.4", lambda x: x.dry < 0.4), ("0.4–0.55", lambda x: (x.dry >= 0.4) & (x.dry < 0.55)),
                                    ("0.55–0.7", lambda x: x.dry >= 0.55)],
    "回檔深度（距 20 日高點）": [("3–6%", lambda x: x.pull > -6), ("6–10%", lambda x: (x.pull <= -6) & (x.pull > -10)),
                          ("10–15%", lambda x: x.pull <= -10)],
    "基底結構": [("比前一次收縮淺", lambda x: x.shallower), ("比前一次收縮深", lambda x: ~x.shallower),
             ("站上 MA50", lambda x: x.above_ma50), ("跌破 MA50", lambda x: ~x.above_ma50),
             ("目前規則趨勢模板", lambda x: x.strict_trend)],
    "大盤": [(m, (lambda m: lambda x: x.market_state == m)(m)) for m in ("多頭", "整理", "偏空")],
    "近 3 個月合計營收年增率": [("< 0%", lambda x: x.rev_yoy3 < 0), ("0–20%", lambda x: (x.rev_yoy3 >= 0) & (x.rev_yoy3 < 20)),
                     ("20–40%", lambda x: (x.rev_yoy3 >= 20) & (x.rev_yoy3 < 40)), ("≥ 40%", lambda x: x.rev_yoy3 >= 40),
                     ("≥ 20% 且加速", lambda x: (x.rev_yoy3 >= 20) & (x.rev_growth == "加速"))],
    "月營收": [(g, (lambda g: lambda x: x.rev_growth == g)(g)) for g in ("加速", "成長", "持平/不穩", "衰退", "無資料")] + [("成長或加速", lambda x: x.rev_growth.isin(["加速", "成長"]))],
}
RULES = {
    "K<30": lambda x: x.k < 30, "K<50": lambda x: x.k < 50, "K>D": lambda x: x.k_gt_d, "K上彎": lambda x: x.k_up,
    "RSI<50": lambda x: x.rsi < 50, "RSI 40-60": lambda x: (x.rsi >= 40) & (x.rsi < 60), "RSI>50": lambda x: x.rsi >= 50,
    "RSI上升": lambda x: x.rsi_up, "DIF>0": lambda x: x.dif > 0, "OSC>0": lambda x: x.osc > 0, "OSC翻揚": lambda x: x.osc_up,
    "量縮<0.5": lambda x: x.dry < 0.5, "回檔≤8%": lambda x: x.pull > -8, "比前次淺": lambda x: x.shallower,
    "站上MA50": lambda x: x.above_ma50, "非偏空": lambda x: x.market_state != "偏空",
    "營收成長": lambda x: x.rev_growth.isin(["加速", "成長"]),
}
BO_RULES = {k: v for k, v in RULES.items() if k in ("K<50", "K>D", "K上彎", "RSI<50", "RSI 40-60", "RSI>50", "RSI上升",
                                                     "DIF>0", "OSC>0", "OSC翻揚", "非偏空")}
BO_RULES.update({"營收成長": RULES["營收成長"], "K<80": lambda x: x.k < 80, "RSI<70": lambda x: x.rsi < 70, "RSI≥70": lambda x: x.rsi >= 70})


def group_tables(x, groups, stf):
    md = []
    for title, items in groups.items():
        rows = [{"條件": lab, **stf(x[f(x)])} for lab, f in items if f(x).sum() > 0]
        md += [f"### {title}\n", md_table(rows)]
    return md


def best_combos(x, rules, key, min_n, top=15):
    res = []
    for r in (1, 2, 3):
        for names_ in combinations(rules, r):
            m = np.logical_and.reduce([rules[nm](x).to_numpy(dtype=bool) for nm in names_])
            sub = x[m]
            if len(sub) >= min_n:
                res.append((key(sub), " + ".join(names_), sub))
    res.sort(key=lambda z: -z[0])
    return res[:top]


bo_ind = ev[(ev["entry"] == "突破") & ev["strict_trend"]].copy()
md = ["# VCP 指標特徵回測（KD、MACD、RSI）\n",
      "指標定義：KD(9,3,3)、RSI(14)、MACD(12,26,9)；DIF、OSC 以收盤價百分比表示，正負號與一般 MACD 相同。\n",
      "## 一、量縮回檔進場（基底內逐步買進）\n",
      f"事件：趨勢向上（MA50 > MA150 > MA200、MA200 上揚、收盤 > MA150、距 52 週高點 ≤{args.loose_from_high:.0f}%、RS ≥{args.loose_rs:.0f}）"
      "的股票，收盤從 20 日高點回檔 3–15%，且近 3 日均量 < 50 日均量 0.7 倍；每段回檔只記第一次量縮。\n",
      f"「成功突破」＝之後 40 個交易日內收盤站上該 20 日高點，且之前沒有跌破 -{args.stop:.0f}% 停損。"
      f"交易報酬：停損 -{args.stop:.0f}%、收盤跌破 MA50 出場、最多 250 日，已扣成本 {args.cost}%。\n",
      "### 基準\n", md_table([{"條件": "全部", **st_pb(pbv)}, {"條件": "目前規則趨勢模板", **st_pb(pbv[pbv.strict_trend])}])]
md += group_tables(pbv, IND_GROUPS, st_pb)
md += group_tables(pbv, PB_GROUPS, st_pb)
md += ["### 成功突破率最高的條件組合（至少 300 筆）\n",
       md_table([{"組合": nm, **st_pb(sub)} for _, nm, sub in best_combos(pbv, RULES, lambda z: z["success"].mean(), 300)]),
       "### 平均交易報酬最高的條件組合（至少 300 筆）\n",
       md_table([{"組合": nm, **st_pb(sub)} for _, nm, sub in best_combos(pbv, RULES, lambda z: z["trade_ret"].mean(), 300)]),
       "## 二、突破當日的指標（目前規則趨勢模板＋寬鬆收縮，突破進場）\n",
       "### 基準\n", md_table([{"條件": "全部", **st_bo(bo_ind)}])]
md += group_tables(bo_ind, IND_GROUPS, st_bo)
md += group_tables(bo_ind, {k: PB_GROUPS[k] for k in ("近 3 個月合計營收年增率", "月營收")}, st_bo)
md += ["### 勝率最高的條件組合（至少 150 筆）\n",
       md_table([{"組合": nm, **st_bo(sub)} for _, nm, sub in best_combos(bo_ind, BO_RULES, lambda z: (z["trade_ret"] > 0).mean(), 150)]),
       "### 平均報酬最高的條件組合（至少 150 筆）\n",
       md_table([{"組合": nm, **st_bo(sub)} for _, nm, sub in best_combos(bo_ind, BO_RULES, lambda z: z["trade_ret"].mean(), 150)])]
open(os.path.join(OUT, "backtest_indicators.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
print(f"完成 → {OUT}/backtest_indicators.md")

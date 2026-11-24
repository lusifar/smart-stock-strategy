#!/usr/bin/env python3
"""股利、殖利率、本益比、淨值比計算與配息名單（只用 Python 標準函式庫）。

讀 <data>/ 的日線（twse/、tpex/，未還原收盤）、每日估值（valuation/）、除權息股利（exright/），輸出到 <data>/output/：
  dividend_YYYYMMDD.csv           每檔一列：官方本益比／淨值比／殖利率、近四季 EPS、每股淨值、近 12 個月殖利率、
                                  5 年平均殖利率、連續配息年數、股利穩定度、歷史百分位、河流圖分位數
  dividend_history.csv            每檔每年股利（依除權息日的西元年）；cash＝當年公告，cash_adj＝換算成今天股數
  river/<代號>.csv                每週一列（每週最後一個交易日）：收盤、本益比、淨值比、EPS、每股淨值、殖利率
  dividend_list_G_YYYYMMDD.csv    G 長期配息
  dividend_list_H_YYYYMMDD.csv    H 長期配息＋相對低估
  dividend_summary_YYYYMMDD.md    摘要（手機好讀）

用法：python3 compute_dividend.py --data vcp-data [--date YYYYMMDD]
"""
import argparse, csv, datetime as dt, glob, os
from collections import defaultdict

from dividend_calc import (adjust_cash, annual_cash, cash_ttm, percentile_rank, quantiles, share_changes,
                           stock_factor, weekly_last, yearly_stats)

p = argparse.ArgumentParser()
p.add_argument("--data", required=True)
p.add_argument("--date", default="")
p.add_argument("--min-yield", type=float, default=4.0, help="G：5 年平均殖利率下限 %%")
p.add_argument("--min-years", type=int, default=5, help="G：連續配息年數下限")
p.add_argument("--max-cv", type=float, default=0.5, help="G：近 5 年股利變異係數上限")
p.add_argument("--min-value", type=float, default=2e7, help="G：20 日平均成交金額下限（元）")
p.add_argument("--min-yield-pctile", type=float, default=70, help="H：殖利率歷史百分位下限")
p.add_argument("--max-pb-pctile", type=float, default=50, help="H：淨值比歷史百分位上限")
args = p.parse_args()
D = os.path.abspath(os.path.expanduser(args.data))
OUT = os.path.join(D, "output")


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def days_before(ds, n):
    d = dt.date(int(ds[:4]), int(ds[4:6]), int(ds[6:])) - dt.timedelta(n)
    return d.strftime("%Y%m%d")


# ---------- 日線（未還原收盤、成交金額） ----------
close, opn, value = defaultdict(dict), defaultdict(dict), defaultdict(dict)
names, market = {}, {}
for mk, label in (("twse", "上市"), ("tpex", "上櫃")):
    for f in sorted(glob.glob(os.path.join(D, mk, "*.csv"))):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            c = num(r["close"])
            if c is None:
                continue
            close[r["code"]][r["date"]] = c
            opn[r["code"]][r["date"]] = num(r["open"])
            value[r["code"]][r["date"]] = num(r["value"]) or 0
            names[r["code"]], market[r["code"]] = r["name"], label
all_dates = sorted({d for v in close.values() for d in v})
if not all_dates:
    raise SystemExit("沒有日線資料")
asof = args.date or all_dates[-1]
last_year = int(asof[:4]) - 1

# ---------- 估值 ----------
val = defaultdict(list)  # code -> [(date, pe, pb, yield_pct)]
for mk in ("twse", "tpex"):
    for f in sorted(glob.glob(os.path.join(D, "valuation", mk, "*.csv"))):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            if r["date"] <= asof:
                val[r["code"]].append((r["date"], num(r["pe"]), num(r["pb"]), num(r["yield_pct"])))
for v in val.values():
    v.sort()

# ---------- 除權息股利 ----------
events = defaultdict(list)  # code -> [(date, cash, stock_per_1000)]
for f in glob.glob(os.path.join(D, "exright", "*_[0-9][0-9][0-9][0-9].csv")):
    for r in csv.DictReader(open(f, encoding="utf-8")):
        if r["date"] <= asof:
            events[r["code"]].append((r["date"], num(r.get("cash_dividend", "")), num(r.get("stock_per_1000", ""))))
            names.setdefault(r["code"], r["name"])


def r2(x):
    return "" if x is None else round(x, 2)


# ---------- 每檔指標 ----------
FIELDS = ["code", "name", "market", "close", "pe", "pb", "yield_pct", "eps_ttm", "bvps", "cash_ttm", "yield_ttm",
          "cash_avg5", "yield_avg5", "paid_years", "cash_cv5", "up_years5", "yield_pctile", "pe_pctile", "pb_pctile",
          "pe_q10", "pe_q25", "pe_q50", "pe_q75", "pe_q90", "pb_q10", "pb_q25", "pb_q50", "pb_q75", "pb_q90",
          "avg_value20"]
RIVER = ["date", "close", "pe", "pb", "eps_ttm", "bvps", "yield_pct"]
d10, d5 = days_before(asof, 3650), days_before(asof, 1826)
os.makedirs(os.path.join(OUT, "river"), exist_ok=True)
rows = []
adj_factors = {}
for code, cl in close.items():
    c = cl.get(asof)
    if c is None:
        continue
    vs = [value[code][d] for d in sorted(value[code]) if d <= asof][-20:]
    ev_all = events.get(code, [])
    cl_asof = {d: x for d, x in cl.items() if d <= asof}
    factors = share_changes(cl_asof, opn[code], all_dates, {d for d, _, _ in ev_all})
    factors += [(d, stock_factor(s)) for d, _, s in ev_all if stock_factor(s)]
    adj_factors[code] = factors = sorted(factors)
    ev = adjust_cash([(d, cash) for d, cash, _ in ev_all], factors)
    st = yearly_stats(annual_cash(ev), last_year)
    ttm = cash_ttm(ev, asof)
    hist = val.get(code, [])
    today = hist[-1] if hist and hist[-1][0] == asof else (asof, None, None, None)
    _, pe, pb, yp = today
    h10 = [x for x in hist if x[0] > d10]
    h5 = [x for x in hist if x[0] > d5]
    row = dict(code=code, name=names[code], market=market[code], close=c, pe=pe, pb=pb, yield_pct=yp,
               eps_ttm=(c / pe) if pe else None, bvps=(c / pb) if pb else None,
               cash_ttm=ttm, yield_ttm=ttm / c * 100, cash_avg5=st["cash_avg5"],
               yield_avg5=st["cash_avg5"] / c * 100, paid_years=st["paid_years"], cash_cv5=st["cash_cv5"],
               up_years5=st["up_years5"],
               yield_pctile=percentile_rank([x[3] for x in h10 if x[3] is not None], yp) if yp is not None else None,
               pe_pctile=percentile_rank([x[1] for x in h10 if x[1] is not None], pe) if pe is not None else None,
               pb_pctile=percentile_rank([x[2] for x in h10 if x[2] is not None], pb) if pb is not None else None,
               avg_value20=sum(vs) / len(vs) if vs else None)
    for k, qv in zip(("pe_q10", "pe_q25", "pe_q50", "pe_q75", "pe_q90"),
                     quantiles([x[1] for x in h5 if x[1] is not None])):
        row[k] = qv
    for k, qv in zip(("pb_q10", "pb_q25", "pb_q50", "pb_q75", "pb_q90"),
                     quantiles([x[2] for x in h5 if x[2] is not None])):
        row[k] = qv
    rows.append({k: (r2(v) if isinstance(v, float) or v is None else v) for k, v in row.items()})
    if hist:
        river = []
        for d, rpe, rpb, ryp in hist:
            rc = cl.get(d)
            if rc is None:
                continue
            river.append((d, rc, r2(rpe), r2(rpb), r2(rc / rpe) if rpe else "", r2(rc / rpb) if rpb else "", r2(ryp)))
        with open(os.path.join(OUT, "river", f"{code}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(RIVER)
            w.writerows(weekly_last(river))


def write(fn, fields, lst):
    with open(os.path.join(OUT, fn), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(lst)


def ok(x, cmp, lim):
    return x != "" and x is not None and cmp(x, lim)


rows.sort(key=lambda r: -(r["yield_avg5"] or 0))
G = [r for r in rows if r["paid_years"] >= args.min_years and ok(r["yield_avg5"], float.__ge__, args.min_yield)
     and ok(r["cash_cv5"], float.__le__, args.max_cv) and ok(r["avg_value20"], float.__ge__, args.min_value)]
H = sorted([r for r in G if ok(r["yield_pctile"], lambda a, b: a >= b, args.min_yield_pctile)
            and ok(r["pb_pctile"], lambda a, b: a <= b, args.max_pb_pctile)], key=lambda r: -r["yield_pctile"])
write(f"dividend_{asof}.csv", FIELDS, rows)
write(f"dividend_list_G_{asof}.csv", FIELDS, G)
write(f"dividend_list_H_{asof}.csv", FIELDS, H)

hist_rows = []
for code, ev in sorted(events.items()):
    by_year = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    adj = adjust_cash([(d, cash) for d, cash, _ in ev], adj_factors.get(code, []))
    for (d, cash, stock), (_, cash_adj) in zip(ev, adj):
        y = by_year[int(d[:4])]
        y[0] += cash or 0
        y[1] += cash_adj or 0
        y[2] += stock or 0
        y[3] += 1 if cash else 0
    for y, (cash, cash_adj, stock, n) in sorted(by_year.items()):
        hist_rows.append(dict(code=code, name=names.get(code, ""), year=y, cash=round(cash, 4),
                              cash_adj=round(cash_adj, 4), stock_per_1000=round(stock, 2), times=n))
write("dividend_history.csv", ["code", "name", "year", "cash", "cash_adj", "stock_per_1000", "times"], hist_rows)

# ---------- 摘要 ----------
COLS = [("代號", "code"), ("名稱", "name"), ("收盤", "close"), ("殖利率 %", "yield_pct"), ("近12月殖利率 %", "yield_ttm"),
        ("5年均殖利率 %", "yield_avg5"), ("連續配息年", "paid_years"), ("股利變異", "cash_cv5"),
        ("殖利率百分位", "yield_pctile"), ("本益比", "pe"), ("淨值比", "pb"), ("淨值比百分位", "pb_pctile")]


def table(lst):
    if not lst:
        return "（無）\n"
    out = ["| # | " + " | ".join(h for h, _ in COLS) + " |", "|" + "---|" * (len(COLS) + 1)]
    for i, r in enumerate(lst[:20], 1):
        out.append(f"| {i} | " + " | ".join(str(r[k]) if r[k] not in ("", None) else "-" for _, k in COLS) + " |")
    return "\n".join(out) + "\n"


md = [f"# 配息名單 {asof}\n",
      f"全市場 {len(rows)} 檔；G 長期配息 {len(G)} 檔；H 長期配息＋相對低估 {len(H)} 檔\n",
      f"\n## G 長期配息\n\n連續配息 ≥ {args.min_years} 年、5 年平均殖利率 ≥ {args.min_yield}%、"
      f"股利變異係數 ≤ {args.max_cv}、20 日均成交金額 ≥ {args.min_value / 1e4:,.0f} 萬。依 5 年平均殖利率排序。\n",
      table(G),
      f"\n## H 長期配息＋相對低估\n\nG 且殖利率在自己近 10 年的百分位 ≥ {args.min_yield_pctile:g}、"
      f"淨值比百分位 ≤ {args.max_pb_pctile:g}。依殖利率百分位排序。\n",
      table(H),
      "\n殖利率 %＝官方殖利率；近12月殖利率＝近 365 天除息現金合計 ÷ 收盤；5年均殖利率＝近 5 個完整年度平均現金股利 ÷ 收盤；過去股利已依其後的配股、減資、面額變更換算成今天的股數。"
      "百分位越高代表殖利率相對自己歷史越高（股價相對便宜）；淨值比百分位越低越便宜。\n"]
open(os.path.join(OUT, f"dividend_summary_{asof}.md"), "w", encoding="utf-8").write("\n".join(md))
print(f"{asof}：配息指標 {len(rows)} 檔；G {len(G)} 檔；H {len(H)} 檔 → {OUT}")

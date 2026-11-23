#!/usr/bin/env python3
"""VCP 每日資料收集腳本（使用 TWSEMCPServer 的資料來源模組）。

收集內容（全部存成 CSV，已存在的檔案會跳過，可隨時中斷、重跑續抓）：
  data/twse/YYYYMMDD.csv     上市全市場日線：開高低收、成交股數、金額、筆數
  data/tpex/YYYYMMDD.csv     上櫃全市場日線（股票＋ETF）
  data/index/taiex_YYYYMM.csv  加權指數每日開高低收（當月每次重抓）
  data/exright/twse_YYYY.csv   上市除權息結果（當年每次重抓）
  data/exright/tpex_YYYY.csv   上櫃除權息結果（當年每次重抓）
  data/industry.csv          上市、上櫃公司產業別（每 30 天更新一次）
  data/holidays.txt          查過確定沒開盤的日期（避免重查）

KD、MACD、RSI、均線都由日線計算（見 compute_vcp.py），不需要另外收。

用法（在 TWSEMCPServer 資料夾內，先 `uv sync`）：
  uv run python collect_vcp.py --out ~/vcp-data                         # 每日：抓最近一個交易日
  uv run python collect_vcp.py --out ~/vcp-data --date 20261120         # 指定某一天
  uv run python collect_vcp.py --out ~/vcp-data --backfill 20250501     # 回補：從該日抓到今天
若本檔不在 TWSEMCPServer 資料夾內，加 --repo /path/to/TWSEMCPServer。
"""
import argparse, csv, datetime as dt, os, sys, time, logging

p = argparse.ArgumentParser()
p.add_argument("--out", required=True, help="資料輸出資料夾")
p.add_argument("--repo", default=os.path.dirname(os.path.abspath(__file__)), help="TWSEMCPServer 資料夾")
p.add_argument("--date", default="", help="只抓這一天 YYYYMMDD")
p.add_argument("--backfill", default="", help="從這一天 YYYYMMDD 回補到 --until（預設今天）")
p.add_argument("--until", default="", help="回補結束日 YYYYMMDD（預設今天）")
p.add_argument("--sleep", type=float, default=8.0, help="每次請求間隔秒數（被擋時調大）")
args = p.parse_args()

sys.path.insert(0, os.path.abspath(os.path.expanduser(args.repo)))
logging.disable(logging.CRITICAL)
from utils.api_client import TWSEAPIClient                     # noqa: E402
from sources.twse.mi_index import stock_quotes                 # noqa: E402
from sources.tpex.daily_quotes import otc_daily_quotes         # noqa: E402
from sources.twse.mi_5mins_hist import taiex_history           # noqa: E402
from sources.twse.twt49u import exright_results                # noqa: E402
from sources.tpex.ex_daily_q import otc_exright_results        # noqa: E402
from services.market_snapshot import TPEX_PROFILE_URL, LISTED_PROFILE_ENDPOINT  # noqa: E402

OUT = os.path.abspath(os.path.expanduser(args.out))
for sub in ("twse", "tpex", "index", "exright"):
    os.makedirs(os.path.join(OUT, sub), exist_ok=True)
HOLI = os.path.join(OUT, "holidays.txt")
holidays = set(open(HOLI).read().split()) if os.path.exists(HOLI) else set()
client = TWSEAPIClient()
FIELDS = ["date", "code", "name", "open", "high", "low", "close", "volume", "value", "transactions"]


def s(x):
    return "" if x is None else str(x)


def call(label, fn):
    """執行一次請求；失敗就退避重試（60、120、240、480 秒），仍失敗則結束程式（下次重跑會續抓）。"""
    for wait in (0, 60, 120, 240, 480):
        if wait:
            print(f"  {label} 失敗，{wait} 秒後重試…", flush=True)
            time.sleep(wait)
        try:
            r = fn()
            time.sleep(args.sleep)
            return r
        except Exception as e:  # 被防爬擋下、連線中斷、格式錯誤
            print(f"  {label} 錯誤：{str(e)[:120]}", flush=True)
    sys.exit(f"{label} 連續失敗，先停止。稍後（或隔幾小時）重跑同一個指令即可續抓。")


def write_csv(path, header, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


def fetch_day(ds):
    """抓 ds（YYYYMMDD）上市＋上櫃日線；回傳實際交易日（空字串＝休市）。"""
    if ds in holidays:
        return ""
    tw_path = os.path.join(OUT, "twse", f"{ds}.csv")
    tp_path = os.path.join(OUT, "tpex", f"{ds}.csv")
    if os.path.exists(tw_path) and os.path.exists(tp_path):
        return ds
    if not os.path.exists(tw_path):
        t = call(f"上市 {ds}", lambda: stock_quotes(client, "ALLBUT0999", ds))
        if t is None or t.date != ds:
            holidays.add(ds)
            open(HOLI, "a").write(ds + "\n")
            print(f"{ds} 休市", flush=True)
            return ""
        write_csv(tw_path, FIELDS, [[ds, q.code, q.name, s(q.open), s(q.high), s(q.low), s(q.close),
                                     s(q.volume), s(q.value), s(q.transactions)] for q in t.quotes])
    if not os.path.exists(tp_path):
        o = call(f"上櫃 {ds}", lambda: otc_daily_quotes(client, ds))
        if o is not None:
            write_csv(tp_path, FIELDS, [[ds, q.code, q.name, s(q.open), s(q.high), s(q.low), s(q.close),
                                         s(q.volume), s(q.value), s(q.transactions)] for q in o.quotes])
    print(f"{ds} 完成", flush=True)
    return ds


def fetch_index(months, this_month):
    for ym in sorted(months):
        path = os.path.join(OUT, "index", f"taiex_{ym}.csv")
        if os.path.exists(path) and ym != this_month:
            continue
        m = call(f"加權指數 {ym}", lambda: taiex_history(client, ym + "01"))
        if m:
            write_csv(path, ["date", "open", "high", "low", "close"],
                      [[d.day.strftime("%Y%m%d"), s(d.open), s(d.high), s(d.low), s(d.close)] for d in m.days])


def fetch_exright(years, this_year):
    for y in sorted(years):
        start, end = dt.date(y, 1, 1), min(dt.date(y, 12, 31), dt.date.today())
        p1 = os.path.join(OUT, "exright", f"twse_{y}.csv")
        if not os.path.exists(p1) or y == this_year:
            r = call(f"上市除權息 {y}", lambda: exright_results(client, start, end))
            write_csv(p1, ["date", "code", "name", "close_before", "reference", "kind"],
                      [[x.day.strftime("%Y%m%d"), x.code, x.name, s(x.close_before), s(x.reference), x.kind]
                       for x in r.results])
        p2 = os.path.join(OUT, "exright", f"tpex_{y}.csv")
        if not os.path.exists(p2) or y == this_year:
            r = call(f"上櫃除權息 {y}", lambda: otc_exright_results(client, start, end))
            write_csv(p2, ["date", "code", "name", "close_before", "reference", "kind"],
                      [[x.day.strftime("%Y%m%d"), x.code, x.name, s(x.close_before), s(x.reference), x.kind]
                       for x in r.results])


def fetch_industry():
    """上市（t187ap03_L）＋上櫃（t187ap03_O）公司產業別；檔案超過 30 天才重抓。"""
    path = os.path.join(OUT, "industry.csv")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 30 * 86400:
        return
    listed = call("上市產業別", lambda: client.fetch_data(LISTED_PROFILE_ENDPOINT)) or []
    otc = call("上櫃產業別", lambda: client.fetch_json(TPEX_PROFILE_URL)) or []
    rows = [[str(r.get("公司代號", "")).strip(), str(r.get("產業別", "")).strip(), "上市"] for r in listed]
    rows += [[str(r.get("SecuritiesCompanyCode", "")).strip(), str(r.get("SecuritiesIndustryCode", "")).strip(), "上櫃"]
             for r in (otc if isinstance(otc, list) else [])]
    rows = [r for r in rows if r[0] and r[1]]
    if rows:
        write_csv(path, ["code", "industry", "market"], rows)
        print(f"產業別更新：{len(rows)} 家", flush=True)


today = dt.date.today()
fetch_industry()
if args.backfill:
    start = dt.datetime.strptime(args.backfill, "%Y%m%d").date()
    end = dt.datetime.strptime(args.until, "%Y%m%d").date() if args.until else today
    days = [start + dt.timedelta(n) for n in range((end - start).days + 1)]
elif args.date:
    days = [dt.datetime.strptime(args.date, "%Y%m%d").date()]
else:  # 每日模式：今天起往回找最近一個交易日（最多 10 天）
    days = []
    for n in range(10):
        d = today - dt.timedelta(n)
        if d.weekday() < 5 and fetch_day(d.strftime("%Y%m%d")):
            days = [d]
            break

got = [d for d in days if d.weekday() < 5 and fetch_day(d.strftime("%Y%m%d"))]
if got:
    fetch_index({d.strftime("%Y%m") for d in got}, today.strftime("%Y%m"))
    fetch_exright({d.year for d in got}, today.year)
print(f"完成：{len(got)} 個交易日，資料在 {OUT}")

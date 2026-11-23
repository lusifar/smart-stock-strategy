#!/usr/bin/env python3
"""月營收收集：MOPS「營業收入統計表」，每個市場每月一頁，含全部上市／上櫃公司（只用 Python 標準函式庫）。

來源：https://mopsov.twse.com.tw/nas/t21/{sii|otc}/t21sc03_{民國年}_{月}_{0|1}.html（Big5；0 國內、1 國外公司）
輸出：<out>/revenue_monthly/{twse|tpex|twse-ky|tpex-ky}_YYYYMM.csv（-ky＝外國公司頁 _1）
  欄位：ym,code,name,revenue,prev_month,last_year,mom_pct,yoy_pct,cum,cum_last_year,cum_yoy_pct,note（金額單位：千元）
已存在的月份會跳過，但最近 2 個月每次重抓（公司可能更正）。營收在次月 10 日前公布，還沒公布的月份會略過。

用法：
  python3 collect_revenue.py --out vcp-data                  # 補到最新（預設從 2016-01 開始）
  python3 collect_revenue.py --out vcp-data --start 202001   # 指定起始月份
collect_vcp.py 每次執行也會自動更新最近 2 個月。
"""
import argparse, csv, datetime as dt, html, os, re, sys, time, urllib.request

URL = "https://mopsov.twse.com.tw/nas/t21/{mk}/t21sc03_{roc}_{m}_{kind}.html"
# (MOPS 市場, 檔名前綴, 頁別)：頁別 0＝國內公司、1＝國外公司（-KY）
PAGES = (("sii", "twse", 0), ("otc", "tpex", 0), ("sii", "twse-ky", 1), ("otc", "tpex-ky", 1))
FIELDS = ["ym", "code", "name", "revenue", "prev_month", "last_year", "mom_pct", "yoy_pct", "cum", "cum_last_year",
          "cum_yoy_pct", "note"]


def months(start, end):
    y, m = divmod(start, 100)
    while y * 100 + m <= end:
        yield y * 100 + m
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)


def latest_published(today=None):
    """最近一個應已公布的月份：每月 10 日後才算上個月已公布。"""
    t = today or dt.date.today()
    y, m = t.year, t.month - (1 if t.day > 10 else 2)
    while m < 1:
        y, m = y - 1, m + 12
    return y * 100 + m


def fetch(mk, ym, kind=0):
    y, m = divmod(ym, 100)
    req = urllib.request.Request(URL.format(mk=mk, roc=y - 1911, m=m, kind=kind), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        text = r.read().decode("big5-hkscs", "replace")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if len(cells) >= 10 and re.fullmatch(r"[0-9A-Z]{4,6}", cells[0]):
            vals = [c.replace(",", "") for c in cells[2:10]]
            rows.append([str(ym), cells[0], cells[1], *vals, cells[10] if len(cells) > 10 else ""])
    return rows


def update(out, start=201601, end=None, sleep=5.0, refresh=2, log=print):
    """抓 start..end（YYYYMM）的月營收；回傳新寫入的檔案數。連續失敗會丟出 RuntimeError。"""
    end = end or latest_published()
    folder = os.path.join(out, "revenue_monthly")
    os.makedirs(folder, exist_ok=True)
    recent = set(list(months(start, end))[-refresh:]) if refresh else set()
    written = 0
    for ym in months(start, end):
        for mk, name, kind in PAGES:
            path = os.path.join(folder, f"{name}_{ym}.csv")
            if os.path.exists(path) and ym not in recent:
                continue
            for wait in (0, 60, 180, 600):
                if wait:
                    log(f"  {name} {ym} 失敗，{wait} 秒後重試…")
                    time.sleep(wait)
                try:
                    rows = fetch(mk, ym, kind)
                    time.sleep(sleep)
                    break
                except Exception as e:  # 被擋、逾時、格式錯誤
                    log(f"  {name} {ym} 錯誤：{str(e)[:120]}")
            else:
                raise RuntimeError(f"{name} {ym} 連續失敗，先停止；稍後重跑即可續抓。")
            if not rows:
                log(f"{name} {ym} 無資料（可能尚未公布）")
                continue
            tmp = path + ".tmp"
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(FIELDS)
                w.writerows(rows)
            os.replace(tmp, path)
            written += 1
            log(f"{name} {ym} 完成：{len(rows)} 家")
    return written


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="資料資料夾（與 collect_vcp.py 的 --out 相同）")
    p.add_argument("--start", type=int, default=201601, help="起始月份 YYYYMM，預設 201601")
    p.add_argument("--end", type=int, default=0, help="結束月份 YYYYMM，預設最近已公布月份")
    p.add_argument("--sleep", type=float, default=5.0, help="每次請求間隔秒數")
    a = p.parse_args()
    try:
        n = update(os.path.abspath(os.path.expanduser(a.out)), a.start, a.end or None, a.sleep,
                   log=lambda s: print(s, flush=True))
    except RuntimeError as e:
        sys.exit(str(e))
    print(f"完成：寫入 {n} 個檔案")

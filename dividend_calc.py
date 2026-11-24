"""股利與估值的純計算（只用 Python 標準函式庫），給 collect_vcp.py 與 compute_dividend.py 使用。"""
import bisect
import datetime as dt
import re
import statistics

DETAIL_CASH = "(每股配發現金股利)除息"
DETAIL_STOCK = "A. 按普通股股東持股比例每千股無償配股"


def split_twse(kind, close_before, reference, total):
    """上市除權息拆成 (每股現金股利, 每千股配股)；「權息」無法由合計拆開，回 (None, None) 交給明細查詢。"""
    if kind == "息" and total is not None:
        return total, 0
    if kind == "權" and close_before and reference:
        return 0, round((close_before / reference - 1) * 1000)
    return None, None


def _first_number(text):
    m = re.search(r"-?\d+(?:\.\d+)?", str(text).replace(",", ""))
    return float(m.group()) if m else 0


def parse_detail(resp):
    """TWT49UDetail 回應 → (每股現金股利, 每千股配股)；查無或欄位變了回 (None, None)。"""
    if not isinstance(resp, dict) or str(resp.get("stat", "")).lower() != "ok" or not resp.get("data"):
        return None, None
    fields = [str(f).strip() for f in resp.get("fields") or []]
    if DETAIL_CASH not in fields or DETAIL_STOCK not in fields:
        return None, None
    row = resp["data"][0]
    return _first_number(row[fields.index(DETAIL_CASH)]), _first_number(row[fields.index(DETAIL_STOCK)])


def _fmt(v):
    return "" if v is None else f"{v:g}"


def twse_row(kind, close_before, reference, total, detail):
    """上市一筆除權息的 (cash_dividend, stock_per_1000) 字串；「權息」用明細 detail（None＝未知，留空）。"""
    if kind == "權息":
        cash, stock = detail if detail else (None, None)
    else:
        cash, stock = split_twse(kind, close_before, reference, total)
    return _fmt(cash), _fmt(stock)


def annual_cash(events):
    """[(除權息日 YYYYMMDD, 現金股利)] → {西元年: (現金股利合計, 配息次數)}；現金為 None 的略過。"""
    out = {}
    for d, cash in events:
        if cash is None:
            continue
        y = int(d[:4])
        s, n = out.get(y, (0.0, 0))
        out[y] = (s + cash, n + (1 if cash > 0 else 0))
    return out


def _date(s):
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def cash_ttm(events, asof):
    """除息日在 (asof − 365 天, asof] 內的現金股利合計。"""
    end = _date(asof)
    start = end - dt.timedelta(365)
    return sum(c for d, c in events if c is not None and start < _date(d) <= end)


def yearly_stats(annual, last_year):
    """近 5 個完整年度（last_year−4..last_year，缺年＝0）的平均、連續配息年數、變異係數、未減少年數。"""
    cash = {y: v[0] for y, v in annual.items()}
    five = [cash.get(y, 0) for y in range(last_year - 4, last_year + 1)]
    mean = sum(five) / 5
    paid = 0
    y = last_year
    while cash.get(y, 0) > 0:
        paid += 1
        y -= 1
    up = sum(1 for y in range(last_year - 4, last_year + 1) if cash.get(y, 0) >= cash.get(y - 1, 0))
    return {"cash_avg5": mean, "paid_years": paid,
            "cash_cv5": (statistics.pstdev(five) / mean) if mean else None, "up_years5": up}


def percentile_rank(history, value):
    """history 中 ≤ value 的比例 × 100（整數）；history 空回 None。"""
    if not history:
        return None
    xs = sorted(history)
    return round(bisect.bisect_right(xs, value) / len(xs) * 100)


def share_changes(close, opn, market_dates, exdates, threshold=0.15, min_halt=3):
    """減資、面額變更等股數變動：停止交易 ≥ min_halt 個市場交易日後，恢復日開盤 ÷ 停牌前收盤偏離 1 超過 threshold，
    且不是除權息日。close/opn：{YYYYMMDD: 價}；market_dates：全市場交易日（排序）。回 [(恢復日, 股價比例)]。"""
    out = []
    ds = sorted(close)
    for a, b in zip(ds, ds[1:]):
        skipped = bisect.bisect_left(market_dates, b) - bisect.bisect_right(market_dates, a)
        o = opn.get(b)
        if skipped < min_halt or b in exdates or not o or not close[a]:
            continue
        r = o / close[a]
        if abs(r - 1) > threshold:
            out.append((b, round(r, 6)))
    return out


def stock_factor(stock_per_1000):
    """配股造成的每股股利稀釋比例；沒配股回 None。"""
    return 1 / (1 + stock_per_1000 / 1000) if stock_per_1000 else None


def adjust_cash(events, factors):
    """把每筆現金股利乘上同日及其後所有股數變動比例，換算成以今天股數計的每股股利。"""
    out = []
    for d, cash in events:
        if cash is not None:
            for fd, f in factors:
                if fd >= d:
                    cash *= f
        out.append((d, cash))
    return out


BWIBBU_NEED = ("證券代號", "本益比", "殖利率(%)", "股價淨值比")


def _cell(row, fields, name):
    if name not in fields:
        return ""
    v = str(row[fields.index(name)]).strip().replace(",", "")
    return "" if v in ("-", "--", "") else v


def parse_bwibbu(resp):
    """上市 BWIBBU_d 回應 → (實際日期, [[code, name, pe, pb, yield_pct, dividend_year, fiscal]])。
    新舊格式都吃：舊格式（約 2017 年前）沒有 股利年度、財報年/季，留空。查無資料回 ("", [])；缺必要欄位丟 ValueError。"""
    if not isinstance(resp, dict) or str(resp.get("stat", "")) != "OK" or not resp.get("data"):
        return "", []
    fields = [str(f).strip() for f in resp.get("fields") or []]
    missing = [f for f in BWIBBU_NEED if f not in fields]
    if missing:
        raise ValueError(f"BWIBBU_d 缺欄位 {missing}；目前欄位：{fields}")
    out = [[_cell(r, fields, k) for k in ("證券代號", "證券名稱", "本益比", "股價淨值比", "殖利率(%)", "股利年度", "財報年/季")]
           for r in resp["data"]]
    return str(resp.get("date") or ""), out

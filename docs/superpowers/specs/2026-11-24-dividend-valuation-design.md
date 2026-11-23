# 股利、殖利率、本益比、淨值比 設計

日期：2026-11-24　狀態：已確認

## 目的

1. 選股條件：殖利率、連續配息等可直接篩選。
2. 判斷股價是否低估：今天的殖利率、本益比、淨值比，和這檔股票自己 10 年的歷史比。
3. 找長期配息標的，持續有現金流：看配息年數、穩定度、成長。
4. 之後在網頁畫本益比、淨值比河流圖（網頁本身不在這次範圍）。

## 目標／非目標／反目標

- 目標：上市＋上櫃全部股票，10 年歷史（2016-11-22 起），每日自動更新，併入現有 `run_daily.sh` 的 commit／push。
- 非目標：財報、配息率（做法 C，之後再說）；網頁；改動 VCP 名單規則。
- 反目標：每天 commit 造成大量檔案整檔重寫、repo 暴增；影響現有 VCP 每日流程的穩定（新步驟失敗不能讓 VCP 資料不 commit）。

## 做法比較（已選 B）

| 做法 | 內容 | 取捨 |
| --- | --- | --- |
| A | 只抓官方每日本益比、淨值比、殖利率 | 簡單；但看不出配息年數與穩定度 |
| **B（選定）** | A＋保留除權息的股利金額，整理每年股利 | 三個目的都涵蓋；回補約 11 小時＋1.5 小時 |
| C | B＋財報 EPS、配息率 | 請求多、MOPS 格式易變，之後再做 |

## 資料收集（改 `collect_vcp.py`）

### 1. 除權息加股利金額

現有 `exright/{twse,tpex}_YYYY.csv` 在原欄位後面加兩欄（`compute_vcp.py` 依欄名讀取，不受影響）：

- `cash_dividend`：每股現金股利（元）
- `stock_per_1000`：每千股無償配股（股）

來源：
- 上櫃 `exDailyQ`：直接有 `現金股利`、`每仟股無償配股`（模組要補讀這兩欄）。
- 上市 `TWT49U` 只有「權值＋息值」合計：
  - `息`：cash ＝ 權值＋息值，stock ＝ 0。
  - `權`：cash ＝ 0，stock_per_1000 ＝ (close_before ÷ reference − 1) × 1000（四捨五入到整數）。
  - `權息`：查 `TWT49UDetail?STK_NO=<代號>&T1=<YYYYMMDD>`，讀「(每股配發現金股利)除息」與「A. 按普通股股東持股比例每千股無償配股」。已驗證 1773 勝一 20250613 回傳 2.5 元／股、200 股。每年約 65 筆。結果快取在 `exright/twse_detail.csv`（code,date,cash_dividend,stock_per_1000），查過不重查。
- 2016–2026 全部重抓一次（22 次請求＋約 715 次 detail，約 1.6 小時）。之後每天只重抓當年。

### 2. 每日估值

新資料夾，一天一檔，已存在就跳過（同日線）：

- `valuation/twse/YYYYMMDD.csv`：來源 `BWIBBU_d`（`sources/twse/bwibbu_d.py`）
- `valuation/tpex/YYYYMMDD.csv`：來源 `peQryDate`（`sources/tpex/pe_qry_date.py`）

欄位（兩市場相同）：`date,code,name,pe,pb,yield_pct,dividend_year,fiscal,dps`
- pe 虧損時留空；dps 只有上櫃有，上市留空。
- 檢查回傳的 `date` 等於要求的日期，不同就不寫檔（BWIBBU_d 對無法解析的日期會回最新一天）。

回補：`--valuation-backfill 20161122`，只抓估值、只抓日線已存在的交易日（不重查休市日）。上市、上櫃各約 2,430 次，間隔 8 秒約 11 小時，可中斷重跑續抓。每日流程：抓當天兩次。

容量：估值每天約 90 KB，10 年約 220 MB（目前日線 330 MB、.git 173 MB）。

## 計算（新檔 `compute_dividend.py`，只用標準函式庫）

讀 `exright/`、`valuation/`、日線。股價用**未還原**收盤（殖利率是實際拿到的錢 ÷ 實際價格）。

### 輸出

1. `output/dividend_YYYYMMDD.csv`：每檔一列（有估值資料的股票，ETF 沒有官方估值，只有股利欄位）。
2. `output/dividend_history.csv`：每檔每年一列：`code,name,year,cash,stock_per_1000,times`（year＝除權息日的西元年，times＝當年配息次數）。
3. `output/river/<代號>.csv`：每週一列（每週最後一個交易日），10 年約 520 列，欄位 `date,close,pe,pb,eps_ttm,bvps,yield_pct`。每天只會改到最後一列或新增一列，git 差異小。網頁畫河流圖與單檔走勢都用它。
4. `output/dividend_summary_YYYYMMDD.md`：名單摘要（手機好讀）。

### `dividend_*.csv` 欄位

| 欄位 | 算法 |
| --- | --- |
| close, pe, pb, yield_pct | 當天官方值 |
| eps_ttm | close ÷ pe（pe 空則空） |
| bvps | close ÷ pb |
| cash_ttm | 除息日在近 365 天內的現金股利合計 |
| yield_ttm | cash_ttm ÷ close × 100 |
| cash_avg5 | 最近 5 個完整年度（今年以前）每年現金股利平均 |
| yield_avg5 | cash_avg5 ÷ close × 100 |
| paid_years | 從去年往回連續有現金股利的年數 |
| cash_cv5 | 近 5 年每年現金股利的變異係數（標準差 ÷ 平均），越小越穩 |
| up_years5 | 近 5 年中，股利 ≥ 前一年的年數（0–5） |
| yield_pctile | 今天 yield_pct 在自己近 10 年每日 yield_pct 的百分位（越高＝相對越便宜） |
| pe_pctile, pb_pctile | 同上，pe、pb 的百分位（越低＝越便宜） |
| pe_q10, pe_q25, pe_q50, pe_q75, pe_q90 | 近 5 年每日 pe 的分位數，河流圖帶狀用 |
| pb_q10 … pb_q90 | 同上，pb |

### 名單（門檻是我的提案，皆可用參數調整）

- **G 長期配息**：paid_years ≥ 5、yield_avg5 ≥ 4%、cash_cv5 ≤ 0.5、20 日均成交金額 ≥ 2,000 萬。依 yield_avg5 排序。
- **H 長期配息＋相對低估**：G 且 yield_pctile ≥ 70 且 pb_pctile ≤ 50。依 yield_pctile 排序。

檔名 `output/dividend_list_G_YYYYMMDD.csv`、`dividend_list_H_YYYYMMDD.csv`。

## 每日流程

`run_daily.sh`：收集 → `compute_vcp.py` → `compute_dividend.py` → commit。`compute_dividend.py` 失敗只記 log、仍 commit VCP 資料（反目標：不影響既有流程）。

## 文件

更新 `README.md` 與 Claude Doc「VCP 資料結構與計算說明」加新資料夾與欄位。

## 我自己做的決定（請檢查）

- 年度用除權息日的西元年，不用股利所屬年度（季配、半年配股票較單純；代價是跨年配發會落在下一年）。
- yield_pctile 用 10 年、河流分位數用 5 年（太久以前的本益比水準參考性低）。
- G／H 門檻（4%、0.5、70、50、2,000 萬）。
- river 檔每週取樣，不存每日（控制 git 容量）。

## 留給實作者

- 先驗證官方上市殖利率是否以現金股利計算（用 2330 某天的 yield_pct × close 對比 exright 現金股利合計）。
- detail 端點失敗時：該筆 cash／stock 留空並在 log 列出，不中斷。

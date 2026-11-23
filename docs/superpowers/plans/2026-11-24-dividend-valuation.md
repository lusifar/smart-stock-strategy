# 股利與估值 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收集每檔股利金額與每日本益比／淨值比／殖利率 10 年歷史，計算殖利率、配息穩定度、歷史百分位與河流圖資料，產出 G、H 兩份配息名單，併入每日流程。

**Architecture:** 純計算放 `dividend_calc.py`（標準函式庫、可單元測試）；`collect_vcp.py` 加除權息股利欄位與每日估值收集（借用 TWSEMCPServer 的 gateway，不改 TWSEMCPServer）；新 `compute_dividend.py` 讀資料、呼叫 `dividend_calc`、寫 `output/`。`run_daily.sh` 在 `compute_vcp.py` 後執行它，失敗不擋 commit。

**Tech Stack:** Python 3 標準函式庫；`unittest`（`python3 -m unittest discover -s tests`）；收集用 `uv run --project TWSEMCPServer`。

**Spec:** `docs/superpowers/specs/2026-11-24-dividend-valuation-design.md`

## Global Constraints

- 不修改 `TWSEMCPServer/`（外部 clone、已 gitignore）；需要的新端點（TWT49UDetail）用 `client.fetch_json` 在本專案呼叫。
- `compute_dividend.py`、`dividend_calc.py` 只用 Python 標準函式庫。
- 所有日期 `YYYYMMDD` 字串、股票代號為字串；CSV 用 UTF-8（output 檔用 `utf-8-sig`，與 compute_vcp 一致）。
- 請求間隔沿用 `--sleep`（預設 8 秒）與 `call()` 退避。
- 股價用**未還原**收盤。
- `compute_dividend.py` 失敗只記 log，`run_daily.sh` 仍 commit VCP 資料。
- G：paid_years ≥ 5、yield_avg5 ≥ 4、cash_cv5 ≤ 0.5、avg_value20 ≥ 2e7；依 yield_avg5 降冪。
- H：G 且 yield_pctile ≥ 70 且 pb_pctile ≤ 50；依 yield_pctile 降冪。
- yield_pctile／pe_pctile／pb_pctile 用近 10 年（3,650 天）；pe_q*／pb_q* 用近 5 年（1,826 天）。

## Review Focus

1. 某年沒配息（中斷）→ paid_years 只算到中斷處，那年計入 cash_avg5 為 0。（Task 1 測試）
2. 虧損股 pe 為空 → eps_ttm 空、pe_pctile 與 pe_q* 只用非空值；全空則空。（Task 1、4 測試）
3. 季配股一年多次除息 → 同年加總，times 正確。（Task 1 測試）
4. BWIBBU_d 回傳日期 ≠ 要求日期（休市或解析失敗）→ 不寫檔、不中斷。（Task 3 驗證）
5. TWT49UDetail 失敗或欄位空 → cash／stock 留空並 log，不中斷；compute 視空為未知（不計入金額）。（Task 1、2 測試）

---

### Task 1: `dividend_calc.py` 純計算

**Files:**
- Create: `dividend_calc.py`
- Test: `tests/test_dividend_calc.py`

**Interfaces:**
- Produces:
  - `split_twse(kind: str, close_before: float|None, reference: float|None, total: float|None) -> tuple[float|None, float|None]`：回傳 (cash, stock_per_1000)。`息`→(total, 0)；`權`→(0, round((cb/ref−1)×1000))；`權息`→(None, None)（交給 detail）；其他或缺值→(None, None)。
  - `parse_detail(resp: dict) -> tuple[float|None, float|None]`：讀 TWT49UDetail JSON 第一列的「(每股配發現金股利)除息」（`"2.5 元／股"`→2.5，空字串→0）與「A. 按普通股股東持股比例每千股無償配股」（`"200 股"`→200）；stat 非 ok、無 data 或欄位缺→(None, None)。欄名用 `fields` 找，去頭尾空白比對。
  - `annual_cash(events: list[tuple[str, float|None]]) -> dict[int, tuple[float, int]]`：events = (ex_date, cash)；依西元年加總 cash（None 跳過），回傳 {year: (cash_sum, times)}，times 只算 cash > 0 的筆數。
  - `cash_ttm(events, asof: str) -> float`：ex_date 在 (asof−365 天, asof] 的 cash 合計。
  - `yearly_stats(annual: dict[int, tuple[float,int]], last_year: int) -> dict`：鍵 `cash_avg5`（last_year−4..last_year 的平均，缺年＝0）、`paid_years`（從 last_year 往回連續 cash > 0 的年數）、`cash_cv5`（同 5 年 pstdev/mean，mean==0→None）、`up_years5`（last_year−4..last_year 每年 cash ≥ 前一年的次數，前一年缺＝0）。
  - `percentile_rank(history: list[float], value: float) -> float|None`：history 中 ≤ value 的比例 ×100，四捨五入到整數；history 空→None。
  - `quantiles(values: list[float], qs=(10,25,50,75,90)) -> list[float|None]`：線性內插（同 numpy 預設 linear）；values 空→全 None。
  - `weekly_last(rows: list[tuple[str, ...]]) -> list[tuple]`：rows 依日期排序，每個 ISO 週取最後一列（含本週未完成的最後一列）。

- [ ] **Step 1: 寫失敗測試** `tests/test_dividend_calc.py`（`unittest`；`sys.path` 加專案根目錄）：
  - `split_twse("息", 1065, 1060.49, 4.500020) == (4.50002, 0)`
  - `split_twse("權", 22.0, 20.0, 2.0) == (0, 100)`
  - `split_twse("權息", 151, 123.75, 27.25) == (None, None)`
  - `parse_detail({"stat":"ok","fields":[...勝一實際 fields...],"data":[["1773  ","勝一","2.5 元／股","","200 股",...]]}) == (2.5, 200)`；`parse_detail({"stat":"很抱歉"}) == (None, None)`；現金欄 `""` → cash 0。
  - `annual_cash([("20250612",4.5),("20250916",5.0),("20240613",3.5),("20240101",None)]) == {2025:(9.5,2), 2024:(3.5,1)}`
  - `cash_ttm([("20251124",1.0),("20251125",2.0),("20261124",3.0),("20261125",9.0)], "20261124") == 5.0`
  - `yearly_stats({2021:(1,1),2022:(1,1),2024:(2,1),2025:(2,1)}, 2025)` → `paid_years==2`、`cash_avg5==(0+1+1+0+2+2... 取 2021–2025: 1,1,0,2,2)/5==1.2`、`up_years5`：2021≥2020(缺=0)✓、2022≥2021✓、2023(0)≥1✗、2024≥0✓、2025≥2✓ → 4。
  - `yearly_stats({}, 2025)["cash_cv5"] is None`
  - `percentile_rank([1,2,3,4], 3) == 75`；`percentile_rank([], 3) is None`
  - `quantiles([1,2,3,4,5], (0,50,100)) == [1,3,5]`；`quantiles([1,2], (25,)) == [1.25]`
  - `weekly_last([("20261117",),("20261120",),("20261123",),("20261124",)]) == [("20261120",),("20261124",)]`

- [ ] **Step 2: 執行確認失敗** `python3 -m unittest discover -s tests -v` → `ModuleNotFoundError: dividend_calc`
- [ ] **Step 3: 實作** `dividend_calc.py`（上列簽名；百分位用 `bisect`；週用 `datetime.date.isocalendar()[:2]`）。
- [ ] **Step 4: 執行確認通過** 同上指令 → 全 OK
- [ ] **Step 5: Commit** `feat: dividend calculation helpers`

---

### Task 2: 除權息加股利欄位（`collect_vcp.py`）

**Files:**
- Modify: `collect_vcp.py`（`fetch_exright`，新增 `fetch_twse_detail`、`--exright-refresh` 參數）

**Interfaces:**
- Consumes: `split_twse`、`parse_detail`（Task 1）。
- Produces: `exright/{twse,tpex}_YYYY.csv` 欄位 `date,code,name,close_before,reference,kind,cash_dividend,stock_per_1000`；`exright/twse_detail.csv` 欄位 `code,date,cash_dividend,stock_per_1000`。

- [ ] **Step 1: 寫失敗測試** `tests/test_dividend_calc.py` 加 `test_twse_row_uses_detail_cache`：測 `dividend_calc.twse_row(x_kind, cb, ref, total, detail: tuple|None) -> tuple[str,str]`，`權息`+detail (2.5,200) → `("2.5","200")`；`權息`+None → `("","")`；`息` → `("4.50002","0")`。（把「split＋detail＋轉字串」放在 `dividend_calc.twse_row`，collect 只負責 I/O。）
- [ ] **Step 2: 執行確認失敗**
- [ ] **Step 3: 實作**
  - `dividend_calc.twse_row(kind, close_before, reference, total, detail) -> tuple[str, str]`：None→`""`，數字用 `f"{v:g}"`。
  - `collect_vcp.py`：
    - 上市：`權息` 列查 `exright/twse_detail.csv` 快取，沒有才 `call(f"除權息明細 {code} {date}", lambda: client.fetch_json("https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail", params={"STK_NO": code, "T1": date, "response": "json"}))`，`parse_detail` 結果（含 None）寫入快取；None 時印 `除權息明細 {code} {date} 無法解析` 且不寫快取（下次重試）。
    - 上櫃：`cash_dividend`、`stock_dividend` 直接寫。
    - 新參數 `--exright-refresh`：重抓 2016–今年全部除權息年度檔（不抓日線），抓完結束。
- [ ] **Step 4: 執行測試通過**；再執行 `uv run --project TWSEMCPServer python collect_vcp.py --repo TWSEMCPServer --out vcp-data --exright-refresh`（約 1.6 小時，背景執行，log 到 `vcp-data/exright.log`）。驗證：`grep ',1773,' vcp-data/exright/twse_2025.csv` 結尾為 `,2.5,200`；`grep ',2330,' vcp-data/exright/twse_2025.csv` cash 為 4.5 與 5；`python3 compute_vcp.py --data vcp-data --date 20261123` 輸出與改前相同（`git diff --stat vcp-data/output/indicators_20261123.csv` 無變化）。
- [ ] **Step 5: Commit** 程式與重抓後的 `vcp-data/exright/`：`feat: keep dividend amounts in ex-rights data`

---

### Task 3: 每日估值收集（`collect_vcp.py`）

**Files:**
- Modify: `collect_vcp.py`（新增 `fetch_valuation(ds)`、`--valuation-backfill`）

**Interfaces:**
- Consumes: `sources.twse.bwibbu_d.valuation_ratios(client, date)`、`sources.tpex.pe_qry_date.otc_valuation_ratios(client, date)`。
- Produces: `valuation/{twse,tpex}/YYYYMMDD.csv`，欄位 `date,code,name,pe,pb,yield_pct,dividend_year,fiscal,dps`（上市 dps 空）。

- [ ] **Step 1: 實作 `fetch_valuation(ds: str) -> None`**：兩市場各自已存在就跳過；回傳 `date != ds` 或無資料 → 印 `估值 {市場} {ds} 無資料` 不寫檔；寫檔用 `write_csv`。每日模式與 `--date` 對 `got` 每天呼叫；`--valuation-backfill YYYYMMDD [--until]`：只對 `vcp-data/twse/` 已有日線檔的日期呼叫，不抓日線、指數、除權息、營收。
- [ ] **Step 2: 驗證單日** `uv run --project TWSEMCPServer python collect_vcp.py --repo TWSEMCPServer --out vcp-data --date 20261123`：`vcp-data/valuation/twse/20261123.csv` 約 1,000 列、`tpex` 約 800 列；`grep ',2330,'` 有 pe、pb、yield_pct。
- [ ] **Step 3: 驗證休市** `--valuation-backfill 20261121 --until 20261122`（週末、無日線檔）→ 不送請求、不產生檔案。
- [ ] **Step 4: 驗證殖利率定義**：2330 在 20261123 的 `yield_pct × close / 100` 與 `dividend_calc.annual_cash` 對 `dividend_year`＋1911 那年（2330 的股利所屬年度）現金合計比較；相差 > 5% 時在設計文件「留給實作者」記下實際定義。結果寫在 commit 訊息。
- [ ] **Step 5: Commit** 程式與 `vcp-data/valuation/*/20261123.csv`：`feat: collect daily PE/PB/yield`
- [ ] **Step 6: 啟動回補**（背景、過夜）：`uv run --project TWSEMCPServer python collect_vcp.py --repo TWSEMCPServer --out vcp-data --valuation-backfill 20161122 >> vcp-data/valuation.log 2>&1`。中斷就重跑。完成後 `ls vcp-data/valuation/twse | wc -l` ≈ `ls vcp-data/twse | wc -l`，commit `data: valuation backfill`。（Task 4 可在回補期間用已有部分資料開發。）

---

### Task 4: `compute_dividend.py`

**Files:**
- Create: `compute_dividend.py`
- Test: `tests/test_compute_dividend.py`

**Interfaces:**
- Consumes: Task 1 全部函式；`exright/*.csv`、`valuation/*/*.csv`、`twse/`、`tpex/` 日線。
- Produces: `output/dividend_YYYYMMDD.csv`、`output/dividend_history.csv`、`output/river/<code>.csv`、`output/dividend_list_G_YYYYMMDD.csv`、`output/dividend_list_H_YYYYMMDD.csv`、`output/dividend_summary_YYYYMMDD.md`。CLI：`python3 compute_dividend.py --data vcp-data [--date YYYYMMDD] [--min-yield 4] [--min-years 5] [--max-cv 0.5] [--min-value 2e7] [--min-yield-pctile 70] [--max-pb-pctile 50]`。
- `dividend_*.csv` 欄位順序：`code,name,market,close,pe,pb,yield_pct,eps_ttm,bvps,cash_ttm,yield_ttm,cash_avg5,yield_avg5,paid_years,cash_cv5,up_years5,yield_pctile,pe_pctile,pb_pctile,pe_q10,pe_q25,pe_q50,pe_q75,pe_q90,pb_q10,pb_q25,pb_q50,pb_q75,pb_q90,avg_value20`；數值四捨五入 2 位；不適用為空字串。
- river 欄位：`date,close,pe,pb,eps_ttm,bvps,yield_pct`。
- 範圍：asof 當天有日線收盤的股票，ETF 也算（無估值欄位）。asof 預設＝最新日線日期。last_year＝asof 年 − 1。

- [ ] **Step 1: 寫失敗測試** `tests/test_compute_dividend.py`：在 `tempfile.TemporaryDirectory()` 建迷你資料夾（1 檔 `9999`，上市；日線 3 天 20261120/20261123/20261124 收盤 100、value 3e7；估值 3 天 pe 10/空/12.5、pb 1/1/1.25、yield_pct 5；exright 2020–2025 每年一筆 `息` cash 5，`twse_2026.csv` 只有欄名），`subprocess.run([sys.executable, "compute_dividend.py", "--data", d])` 後斷言：
  - `dividend_20261124.csv` 的 9999：`eps_ttm==8`、`bvps==80`、`yield_avg5==5`、`paid_years==6`、`cash_cv5==0`、`pe_pctile==100`（歷史 [10,12.5]，空值略過）
  - `river/9999.csv` 有 2 列（20261120、20261124）
  - `dividend_list_G_20261124.csv` 含 9999；`dividend_history.csv` 有 2020–2025 共 6 列
- [ ] **Step 2: 執行確認失敗**
- [ ] **Step 3: 實作 `compute_dividend.py`**：讀日線只取 close、value（未還原）；avg_value20＝近 20 日 value 平均；估值歷史按 code 收成 `[(date, pe, pb, yield_pct)]`；eps_ttm＝close/pe、bvps＝close/pb（以該日日線收盤）；pctile 用近 3,650 天非空值；q 用近 1,826 天非空值；river 用 `weekly_last`；summary 與 `compute_vcp.py` 同風格（標題、G、H 前 20 名表、門檻說明）。
- [ ] **Step 4: 執行測試通過**；再對真實資料 `python3 compute_dividend.py --data vcp-data --date 20261123`，抽查 2330 的 cash_ttm、paid_years 合理（應 ≥ 10 年配息、季配 times=4）。
- [ ] **Step 5: Commit** `feat: dividend yield and valuation metrics`

---

### Task 5: 每日流程與文件

**Files:**
- Modify: `run_daily.sh`、`README.md`
- Update: Claude Doc「VCP 資料結構與計算說明」（https://claude.ai/artifact/HmVaso1YEbyt6Hx77aQR2j）

- [ ] **Step 1:** `run_daily.sh` 在 `compute_vcp.py` 後加：`python3 compute_dividend.py --data vcp-data || log "股利計算失敗（仍 commit 其他資料）。"`
- [ ] **Step 2: 驗證** `DRY_RUN=1 ./run_daily.sh`：log 出現 dividend 名單檔數；暫時把 `compute_dividend.py` 改名後再跑，log 出現「股利計算失敗」且腳本走到 DRY_RUN 結尾（exit 0）。改回檔名。
- [ ] **Step 3:** README 加腳本、資料夾、回補指令（`--exright-refresh`、`--valuation-backfill`）、G/H 說明；Claude Doc 目錄表、欄位定義、腳本表各加新內容，計算邏輯加「股利與估值」小節。
- [ ] **Step 4: Commit** `feat: run dividend metrics daily; docs`

# VCP 資料收集與計算

主要腳本：
- `collect_vcp.py`：每天收集上市、上櫃全市場日線（開高低收量）、加權指數、除權息結果、產業別（每 30 天更新），並更新最近 2 個月的月營收。
- `collect_revenue.py`：從 MOPS「營業收入統計表」收集全市場月營收（上市、上櫃各分國內與 -KY 外國公司，每月共 4 頁），用來回補歷史。
- `compute_vcp.py`：計算均線、52 週高低點、RS、KD、MACD、RSI、量縮、VCP 收縮、產業強度、大盤狀態與月營收，產出六份候選名單。
- `compute_dividend.py`：計算殖利率（官方、近 12 個月、5 年平均）、連續配息年數、股利穩定度、本益比／淨值比／殖利率的歷史百分位，產出 G、H 兩份配息名單。
- `dividend_calc.py`：股利計算的純函式（`compute_dividend.py`、`collect_vcp.py` 共用；測試在 `tests/`）。
- `backtest_vcp.py`：用歷史資料回測 VCP 突破、突破後回踩、量縮回檔加碼，以及 KD／MACD／RSI 的特徵。
- `chart_vcp.py`：產生走勢圖網頁（K 線＋均線主圖，附圖為成交量、KD、MACD、RSI），標出 VCP 收縮、突破點、停損與停利。
- `run_daily.sh`：每日流程（收集 → 計算 → commit `vcp-data/` → push 到 dev），由 launchd 排程執行。

KD、MACD、RSI 都是從日線算出來的，**不需要另外收集**。

## 專案結構

所有安裝與資料都放在本專案資料夾內（以下指令都在專案根目錄執行）：

```
smart-stock-strategy/
  collect_vcp.py                    每日資料收集
  collect_revenue.py                月營收收集
  compute_vcp.py                    指標計算與名單
  compute_dividend.py               殖利率、本益比、淨值比與配息名單
  dividend_calc.py                  股利計算純函式
  tests/                            單元測試（python3 -m unittest discover -s tests）
  backtest_vcp.py                   回測
  chart_vcp.py                      走勢圖網頁
  run_daily.sh                      每日流程（含 commit、push）
  launchd/com.vcp.collect.plist     每日排程（macOS launchd）
  TWSEMCPServer/                    資料來源模組（git clone，已列入 .gitignore）
  vcp-data/                         收集到的資料與輸出（log 與暫存檔已列入 .gitignore）
```

`collect_vcp.py` 不需要複製進 `TWSEMCPServer/`，用 `uv run --project TWSEMCPServer` 借用它的環境，並以 `--repo TWSEMCPServer` 指定模組位置。`collect_revenue.py`、`compute_vcp.py` 只用 Python 標準函式庫；`backtest_vcp.py` 需要 numpy、pandas，用 `uv run` 執行時會自動安裝在暫時環境。

## 安裝步驟（可直接貼給 Claude Code）

> 請幫我在這台 Mac 上設定 VCP 資料收集，所有東西放在目前專案資料夾內：
> 1. 把 https://github.com/twjackysu/TWSEMCPServer clone 到 `./TWSEMCPServer`，執行 `uv sync --project TWSEMCPServer`（沒有 uv 就先用 `brew install uv` 安裝）。
> 2. 先試抓一天：`uv run --project TWSEMCPServer python collect_vcp.py --repo TWSEMCPServer --out vcp-data --date 20261120`，確認 `vcp-data/twse/20261120.csv` 有約 1,390 行、`vcp-data/tpex/20261120.csv` 有約 1,020 行、`vcp-data/industry.csv` 有約 2,000 行。
> 3. 回補 10 年日線：`uv run --project TWSEMCPServer python collect_vcp.py --repo TWSEMCPServer --out vcp-data --backfill 20161122`，在背景執行並記錄到 `vcp-data/backfill.log`。如果中途因為被擋而停止，等 30 分鐘再執行同一個指令即可續抓（已抓的日期會跳過）。
> 4. 回補月營收：`python3 collect_revenue.py --out vcp-data --start 201601`，在背景執行並記錄到 `vcp-data/revenue.log`（約 520 次請求、2 小時，被擋停止時重跑即可續抓）。
> 5. 設定每個交易日 17:30 自動收集：使用 `launchd/com.vcp.collect.plist`（輸出附加到 `vcp-data/daily.log`），見下方「每日排程」。
> 6. 回補完成後執行 `python3 compute_vcp.py --data vcp-data`，把 `vcp-data/output/summary_*.md` 和各份名單給我看。
> 7. （選用）回測：`uv run backtest_vcp.py --data vcp-data`，看 `vcp-data/output/backtest_summary.md` 與 `backtest_indicators.md`。

## 每日排程

`launchd/com.vcp.collect.plist` 在週一到週五 17:30 執行 `run_daily.sh`：

1. 收集當天日線、每日估值、加權指數、除權息與最近 2 個月月營收
2. 執行 `compute_vcp.py` 產生名單，再執行 `compute_dividend.py`（失敗只記 log，不影響 commit）
3. 只 commit `vcp-data/` 的變更（訊息 `data: daily update YYYYMMDD`），並 push 到 `origin/dev`

安全措施：目前分支不是 `dev`、收集或計算失敗、資料沒有變更時都不會 commit；不會把你正在修改的程式一起 commit；push 失敗時 commit 留在本機，下次一起 push。push 使用 macOS 鑰匙圈裡的 GitHub 認證（`credential.helper=osxkeychain`）。

手動測試（只收集與計算，不 commit、不 push）：`DRY_RUN=1 ./run_daily.sh`

plist 內的路徑是本專案的絕對路徑（搬移專案後要修改）。啟用：

```sh
cp launchd/com.vcp.collect.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vcp.collect.plist
```

停用：

```sh
launchctl bootout gui/$(id -u)/com.vcp.collect
rm ~/Library/LaunchAgents/com.vcp.collect.plist
```

注意：專案不要放在 `~/Desktop`、`~/Documents`、`~/Downloads`，macOS 的隱私保護（TCC）會擋 launchd 讀取這些資料夾（log 出現 `Operation not permitted`）。目前放在 `~/Projects/smart-stock-strategy`。

## 預估時間與注意事項

- 回補 10 年約 2,610 個平日，每天上市、上櫃各 1 次請求，間隔 8 秒，**約 12 小時**，建議分段或過夜執行（可用 `--until YYYYMMDD` 分段，例如先抓近 1.5 年再往前補）。資料量約 350 MB。
- 越早期的掛牌家數越少（2016 年上市約 980 檔、上櫃約 720 檔），屬正常。
- 被擋時腳本會自動退避（1 到 8 分鐘），連續失敗就停止，重跑同一個指令即可續抓。實際回補時被擋過 2 次，等 30 分鐘、`--sleep` 調到 12–15 秒後就順利完成。
- 一直被擋的話，把 `--sleep` 調大（例如 `--sleep 15`）。
- 每天的收集只要 6 到 10 次請求，很快就跑完。
- TWSE 大約下午 2 點半後才會公布當天的收盤資料，所以排程定在 17:30。
- 月營收在次月 10 日前公布；`compute_vcp.py` 只會用到計算日當天已公布的月份，也不採用超過 6 個月前的舊資料。

## 資料夾結構

```
vcp-data/
  twse/YYYYMMDD.csv        上市日線：date,code,name,open,high,low,close,volume,value,transactions
  tpex/YYYYMMDD.csv        上櫃日線（同欄位）
  index/taiex_YYYYMM.csv   加權指數
  exright/twse_YYYY.csv    上市除權息：date,code,name,close_before,reference,kind,cash_dividend,stock_per_1000
  exright/tpex_YYYY.csv    上櫃除權息（同欄位）
  exright/twse_detail.csv  上市「權息」明細快取（TWT49UDetail）：code,date,cash_dividend,stock_per_1000
  valuation/twse/YYYYMMDD.csv  上市每日估值（BWIBBU_d）：date,code,name,pe,pb,yield_pct,dividend_year,fiscal,dps（dps 空）
  valuation/tpex/YYYYMMDD.csv  上櫃每日估值（peQryDate，同欄位，dps＝每股股利）
  industry.csv             產業別（上市＋上櫃）
  holidays.txt             已確認休市的日期
  revenue_monthly/twse_YYYYMM.csv  上市月營收：ym,code,name,revenue,prev_month,last_year,mom_pct,yoy_pct,cum,cum_last_year,cum_yoy_pct,note（千元）
  revenue_monthly/tpex_YYYYMM.csv  上櫃月營收（同欄位）
  revenue_monthly/twse-ky_YYYYMM.csv、tpex-ky_YYYYMM.csv  -KY 外國公司月營收（同欄位）
  output/indicators_YYYYMMDD.csv            全市場指標（含 KD、RSI、MACD、月營收）
  output/vcp_forming_YYYYMMDD.csv           A 正在形成 VCP（技術面）
  output/vcp_forming_revenue_YYYYMMDD.csv   B＝A＋營收年增 ≥20%
  output/vcp_pullback_YYYYMMDD.csv          C 突破後回踩（技術面）
  output/vcp_pullback_revenue_YYYYMMDD.csv  D＝C＋營收年增 ≥20%
  output/vcp_addon_YYYYMMDD.csv             E 量縮回檔加碼
  output/vcp_addon_revenue_YYYYMMDD.csv     F＝E＋營收年增 ≥20% 且加速
  output/summary_YYYYMMDD.md                大盤狀態、強勢產業、各名單前 20 名（手機好讀）
  output/dividend_YYYYMMDD.csv              每檔殖利率、配息穩定度、估值百分位
  output/dividend_history.csv               每檔每年股利（依除權息日的西元年）：code,name,year,cash,cash_adj,stock_per_1000,times
  output/dividend_list_G_YYYYMMDD.csv       G 長期配息
  output/dividend_list_H_YYYYMMDD.csv       H 長期配息＋相對低估
  output/dividend_summary_YYYYMMDD.md       配息名單摘要
  output/backtest_*.md / backtest_*.csv     回測結果
```

## 走勢圖

```sh
python3 chart_vcp.py --data vcp-data --codes 3293:20240604 --after 90          # 單一案例（冒號後為突破日）
python3 chart_vcp.py --data vcp-data --list vcp-data/output/vcp_addon_20250912.csv  # 名單內所有股票，左側可切換
```

輸出 `vcp-data/output/charts/*.html`，直接用瀏覽器開啟（需連網載入 lightweight-charts）。主圖為還原價 K 線（紅漲綠跌）與 MA10／20／50／150／200，
標出最近 4 次收縮的高低點與回檔 %、放量突破、突破點、停損 -8%、停利 +10%／+20%；附圖依序為成交量（10、50 日均量）、KD(9,3,3)、MACD(12,26,9)、RSI(14)。
五張圖的縮放與十字線同步，滑鼠移到哪天，左上角就顯示當天數值。

## 篩選邏輯

### 共同前提：趨勢模板（Stage 2）
- 收盤 > MA150 > MA200，且 MA50 > MA150，收盤 > MA50
- MA200 比一個月前高
- 距 52 週低點 ≥ +30%
- **距 52 週高點 ≤ 15%**（J Law 的做法；Minervini 原版是 25%，可用 `--max-from-high 25` 改回）
- RS（全市場相對強度百分位）≥ 70
- **20 日平均成交金額 ≥ 5,000 萬元**（台股版的流動性門檻，可用 `--min-value` 調整）

### 由上而下（J Law）
- **大盤狀態**：用加權指數相對 MA50、MA200 判斷「多頭／整理／偏空」，寫在摘要最上方。偏空時突破失敗率高，宜降低部位。
- **強勢產業**：每個產業取成員 RS 的中位數，排名前 20% 的產業為強勢產業（+10 分）。
- **產業領頭**：RS 在自己產業內前 3 名（+5 分）。

### 收縮與突破（依回測調整）
- **收縮逐次變小**：只看最近 4 次回檔（例如 22% → 12% → 7%），每次不大於前一次（容許 10%），第一次 ≤ 35%，最後一次 ≤ 12%。原本檢查 120 天內所有回檔，10 年回測只有 8 筆符合，幾乎選不到股票。
- **放量突破**：當日量為 50 日均量的 **1.5–3 倍**。回測中 3 倍以上的爆量突破平均報酬最差。

### A 正在形成 VCP
- 收縮逐次變小（見上）
- 量縮：10 日均量小於 50 日均量（剛放量突破的不受此限）
- 位置：收盤在突破點下方 8% 內（「接近突破點」），或剛突破 5% 內且當日放量 1.5–3 倍（「剛突破（放量）」）
- 最近 3 到 20 天已經突破過的，改歸 C 名單判斷，不會重複出現
- 技術分：RS＋產業加分＋量縮 10＋近 10 日振幅 ≤ 8% 10＋距突破點 3% 內 10＋每次收縮 5（最多 4 次）

### C 突破後回踩（J Law 的買點）
- 最近 3 到 20 個交易日內，曾從「收縮逐次變小」的基底**放量突破**（1.5–3 倍）
- 突破後從高點**拉回至少 3%**，但收盤仍守在突破位 -3% 以上（沒有跌回基底）
- 收盤回到**突破位 ±3%** 或 **10 日、20 日均線 ±2%** 附近（`support` 欄位會寫是哪一個）
- **回踩縮量**：近 3 日均量 < 50 日均量
- 附 `stop_ref` 停損參考價（突破位與 MA20 較低者再下 3%）和 `risk_pct` 風險 %，方便依 J Law 的小風險原則算部位
- 技術分：RS＋產業加分＋縮量回踩 10＋守住支撐 10＋近 10 日振幅 ≤ 8% 10（已移除「突破量 ≥ 2 倍」加分）

### E 量縮回檔加碼（基底內分批買進）
- 趨勢模板（已含站上 MA50）
- 收盤從 20 日高點回檔 **3–8%**，且**比前一次收縮淺**（`prev_contraction`）
- **量縮**：近 3 日均量 < 50 日均量 0.7 倍
- **指標排除**（任一成立就不列入，`addon_veto` 欄位）：K < 30、RSI(14) < 45、MACD DIF ≤ 0
- 附 `addon_stop` 停損參考（收盤 -8%）
- 分數（`addon_score`）：RS＋產業加分＋RSI ≥ 50 且 MACD 柱狀體 > 0 10＋回檔 ≤ 6% 10＋營收分
- 加碼方式：每次回檔比前一次淺且量縮時分批加碼，站上前高（突破）再加最後一筆；回檔比前一次深、跌破 MA50 或觸及停損參考就停止加碼。

### 月營收（讀 `revenue_monthly/`）
- `rev_3m_yoy`：近 3 個月合計營收年增率（只用計算日當天已公布、且 6 個月內的月份）
- 營收分：`rev_3m_yoy` ≥ 40% +20、≥ 20% +15，其餘 0
- `rev_growth`（參考）：近 3 個月年增率都 > 0＝成長；成長且最新月年增率高於前 3 個月平均＝加速；最新月年增率 < 0＝衰退
- **B、D**＝A、C 中 `rev_3m_yoy` ≥ 20%；**F**＝E 中 `rev_3m_yoy` ≥ 20% 且營收加速。依 `total_score`（E、F 為 `addon_score`）排序。
- 沒有 `revenue_monthly/` 資料時只輸出 A、C、E。

### 停利參考
- `tp10`、`tp20`：進場參考價（`entry_ref`；A「接近突破點」用突破點，其餘用收盤）的 +10%、+20%
- 建議分批停利：回測中約一半的突破股在碰到 -8% 停損前能漲到 +10%；剩餘部位以 MA50 或移動停損續抱。

### 可調參數
`--max-from-high`（預設 15）、`--min-value`（預設 5e7）、`--base-days`（預設 120）、`--near-pivot`（預設 8）、`--date`。

KD、RSI、MACD 都在 indicators 檔裡，可以拿來再加條件。名單是規則初篩，進場前請看線圖確認。

## 股利與估值（`compute_dividend.py`）

股價用未還原收盤（殖利率是實際拿到的錢 ÷ 實際價格）。

- 現金股利：上櫃直接取自除權息資料；上市「息」＝權值＋息值，「權息」查 TWT49UDetail 明細。上市「權」的 `stock_per_1000` 是由價格推算的估計值（現金增資也會造成除權），現金股利為 0 是準確的。
- 股數換算：過去的每股股利乘上其後所有股數變動比例，換成「今天一股」的股利，才能和今天股價相比。股數變動包括配股（1 ÷ (1＋每千股配股/1000)），以及減資、面額變更（停止交易 ≥ 3 個交易日後恢復、開盤價和停牌前收盤差超過 15%、且不是除權息日，以這個價格比例推算）。`dividend_history.csv` 的 `cash` 是當年公告值，`cash_adj` 是換算後的值。
- `yield_pct`（官方殖利率）＝最近一個股利年度的現金股利 ÷ 收盤（例：2330 在 20261123 為 1.08%，即 114 年度 22 元 ÷ 2,030）。約 2017 年以前的上市估值沒有股利年度、財報年/季欄位，留空。
- `eps_ttm`＝收盤 ÷ 本益比（近四季 EPS）、`bvps`＝收盤 ÷ 淨值比（每股淨值）；虧損時本益比為空。
- `yield_ttm`＝近 365 天除息現金合計 ÷ 收盤；`yield_avg5`＝近 5 個完整年度（今年以前，缺年算 0）平均現金股利 ÷ 收盤。
- `paid_years`：從去年往回連續有現金股利的年數；`cash_cv5`：近 5 年股利的變異係數（越小越穩）；`up_years5`：近 5 年股利不低於前一年的年數。
- 河流圖不預先計算：直接用 `valuation/` 每日的 pe、pb 搭配日線收盤自行運算（EPS＝收盤 ÷ pe、每股淨值＝收盤 ÷ pb）。
- `yield_pctile`／`pe_pctile`／`pb_pctile`：今天的值在自己近 10 年每日值中的百分位（殖利率越高、本益比與淨值比越低＝相對便宜）。估值歷史不足 250 個交易日（`--min-history`）時，百分位留空，H 名單也就不會選到（新上市股、或估值回補尚未完成時）。
- G 長期配息：連續配息 ≥ 5 年、5 年平均殖利率 ≥ 4%、變異係數 ≤ 0.5、20 日均成交金額 ≥ 2,000 萬。
- H 長期配息＋相對低估：G 且殖利率百分位 ≥ 70、淨值比百分位 ≤ 50。
- 可調參數：`--min-yield`、`--min-years`、`--max-cv`、`--min-value`、`--min-yield-pctile`、`--max-pb-pctile`、`--date`。

回補指令：

```sh
# 除權息重抓（含股利金額，約 1.6 小時）
uv run --project TWSEMCPServer python collect_vcp.py --repo TWSEMCPServer --out vcp-data --exright-refresh >> vcp-data/exright.log 2>&1
# 每日估值回補 10 年（約 11 小時，中斷可重跑續抓）
uv run --project TWSEMCPServer python collect_vcp.py --repo TWSEMCPServer --out vcp-data --valuation-backfill 20161122 >> vcp-data/valuation.log 2>&1
```

## 回測重點（2017-10 ～ 2026-11，`backtest_vcp.py`）

報酬看兩種：持有到出場（停損 -8%、跌破 MA50 出場）的交易報酬，以及突破後 3 個月內最高價的漲幅（理論上限；另算「碰到停損前」的最高漲幅較實際）。

- 勝率約 30%，靠少數大賺撐起報酬（平均賺 +25%、平均賠 -7%），中位數多為負，停損紀律很重要。
- 新版規則（最近 4 次收縮遞減、突破量 1.5–3 倍）9 年 97 筆：交易報酬平均 +13.6%，3 個月內最高漲幅中位數 +20.3%（舊規則只有 8 筆）。
- 距 52 週高點 15–25% 的突破平均虧損；RS ≥ 90 的漲幅最大；突破量 1.5–2 倍最好，3 倍以上最差；大盤偏空時明顯較差。
- **月營收**：不會提高勝率，但會提高漲幅。突破時近 3 個月合計年增率 ≥ 20%：3 個月內最高漲幅中位數 +18.6%（< 20% 為 +15.2%），停損前漲到 +20% 的比例 34%（30%），10 年中 8 年較好。年增 0–20% 的「溫和成長」表現最差，衰退反而不差。
- 量縮回檔進場：回檔越淺、比前一次收縮淺，之後成功突破的比例越高（回檔 3–6% 約 56%，10–15% 約 29%）；營收年增 ≥ 20% 且加速時，3 個月內最高漲幅中位數 +20.3%（E 全部 +16.6%）。
- KD、RSI、MACD：超賣（K < 20、RSI < 40）或 DIF < 0 的回檔成功率最低；KD 黃金交叉、K 上彎、OSC 翻揚幾乎沒有幫助，所以指標只拿來「排除」。
- 結果有過度擬合風險，看方向不要看單一數字。

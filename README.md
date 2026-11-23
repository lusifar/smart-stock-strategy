# VCP 資料收集與計算

兩支腳本：
- `collect_vcp.py`：每天收集上市、上櫃全市場日線（開高低收量）、加權指數、除權息結果，以及產業別（每 30 天更新）。
- `compute_vcp.py`：計算均線、52 週高低點、RS、KD、MACD、RSI、量縮、VCP 收縮、產業強度與大盤狀態，產出四份候選名單。

KD、MACD、RSI 都是從日線算出來的，**不需要另外收集**。

## 專案結構

所有安裝與資料都放在本專案資料夾內（以下指令都在專案根目錄執行）：

```
smart-stock-strategy/
  collect_vcp.py                    資料收集
  compute_vcp.py                    指標計算與名單
  launchd/com.vcp.collect.plist     每日排程（macOS launchd）
  TWSEMCPServer/                    資料來源模組（git clone，已列入 .gitignore）
  vcp-data/                         收集到的資料與輸出（已列入 .gitignore）
```

腳本不需要複製進 `TWSEMCPServer/`，用 `uv run --project TWSEMCPServer` 借用它的環境，並以 `--repo TWSEMCPServer` 指定模組位置。

## 安裝步驟（可直接貼給 Claude Code）

> 請幫我在這台 Mac 上設定 VCP 資料收集，所有東西放在目前專案資料夾內：
> 1. 把 https://github.com/twjackysu/TWSEMCPServer clone 到 `./TWSEMCPServer`，執行 `uv sync --project TWSEMCPServer`（沒有 uv 就先用 `brew install uv` 安裝）。
> 2. 先試抓一天：`uv run --project TWSEMCPServer python collect_vcp.py --repo TWSEMCPServer --out vcp-data --date 20261120`，確認 `vcp-data/twse/20261120.csv` 有約 1,390 行、`vcp-data/tpex/20261120.csv` 有約 1,020 行、`vcp-data/industry.csv` 有約 2,000 行。
> 3. 回補 10 年：`uv run --project TWSEMCPServer python collect_vcp.py --repo TWSEMCPServer --out vcp-data --backfill 20161122`，在背景執行並記錄到 `vcp-data/backfill.log`。如果中途因為被擋而停止，等 30 分鐘再執行同一個指令即可續抓（已抓的日期會跳過）。
> 4. 設定每個交易日 17:30 自動收集：使用 `launchd/com.vcp.collect.plist`（輸出附加到 `vcp-data/daily.log`），見下方「每日排程」。
> 5. 回補完成後執行 `uv run --project TWSEMCPServer python compute_vcp.py --data vcp-data --repo TWSEMCPServer`，把 `vcp-data/output/summary_*.md` 和四份名單給我看。

## 每日排程

`launchd/com.vcp.collect.plist` 在週一到週五 17:30 執行收集，plist 內的路徑是本專案的絕對路徑（搬移專案後要修改 `WorkingDirectory` 與 log 路徑）。啟用：

```sh
cp launchd/com.vcp.collect.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vcp.collect.plist
```

停用：

```sh
launchctl bootout gui/$(id -u)/com.vcp.collect
rm ~/Library/LaunchAgents/com.vcp.collect.plist
```

注意：macOS 可能需要在「系統設定 → 隱私權與安全性 → 完整磁碟取用權限」允許 `uv`，launchd 才能讀寫 `~/Desktop` 底下的專案。

## 預估時間與注意事項

- 回補 10 年約 2,610 個平日，每天上市、上櫃各 1 次請求，間隔 8 秒，**約 12 小時**，建議分段或過夜執行（可用 `--until YYYYMMDD` 分段，例如先抓近 1.5 年再往前補）。資料量約 350 MB。
- 越早期的掛牌家數越少（2016 年上市約 980 檔、上櫃約 720 檔），屬正常。
- 被擋時腳本會自動退避（1 到 8 分鐘），連續失敗就停止，重跑同一個指令即可續抓。
- 一直被擋的話，把 `--sleep` 調大（例如 `--sleep 15`）。
- 每天的收集只要 4 到 8 次請求，很快就跑完。
- TWSE 大約下午 2 點半後才會公布當天的收盤資料，所以排程定在 17:30。

## 資料夾結構

```
vcp-data/
  twse/YYYYMMDD.csv        上市日線：date,code,name,open,high,low,close,volume,value,transactions
  tpex/YYYYMMDD.csv        上櫃日線（同欄位）
  index/taiex_YYYYMM.csv   加權指數
  exright/twse_YYYY.csv    上市除權息（還原價用）
  exright/tpex_YYYY.csv    上櫃除權息
  industry.csv             產業別（上市＋上櫃）
  holidays.txt             已確認休市的日期
  revenue/                 候選股月營收快取
  output/indicators_YYYYMMDD.csv            全市場指標
  output/vcp_forming_YYYYMMDD.csv           A 正在形成 VCP（技術面）
  output/vcp_forming_revenue_YYYYMMDD.csv   B＝A＋月營收成長（需 --repo）
  output/vcp_pullback_YYYYMMDD.csv          C 突破後回踩（技術面）
  output/vcp_pullback_revenue_YYYYMMDD.csv  D＝C＋月營收成長（需 --repo）
  output/summary_YYYYMMDD.md                大盤狀態、強勢產業、各名單前 20 名（手機好讀）
```

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

### A 正在形成 VCP
- 最近 120 個交易日內至少 2 次回檔，每次比前一次小（例如 22% → 12% → 7%），第一次 ≤ 35%，最後一次 ≤ 12%
- 量縮：10 日均量小於 50 日均量（剛放量突破的不受此限）
- 位置：收盤在突破點下方 8% 內（「接近突破點」），或剛突破 5% 內且當日量 ≥ 50 日均量 1.5 倍（「剛突破（放量）」）
- 最近 3 到 20 天已經突破過的，改歸 C 名單判斷，不會重複出現
- 技術分：RS＋產業加分＋量縮 10＋近 10 日振幅 ≤ 8% 10＋距突破點 3% 內 10＋每次收縮 5（最多 4 次）

### C 突破後回踩（J Law 的買點）
- 最近 3 到 20 個交易日內，曾從「收縮逐次變小」的基底**放量突破**（當日量 ≥ 50 日均量 1.5 倍）
- 突破後從高點**拉回至少 3%**，但收盤仍守在突破位 -3% 以上（沒有跌回基底）
- 收盤回到**突破位 ±3%** 或 **10 日、20 日均線 ±2%** 附近（`support` 欄位會寫是哪一個）
- **回踩縮量**：近 3 日均量 < 50 日均量
- 附 `stop_ref` 停損參考價（突破位與 MA20 較低者再下 3%）和 `risk_pct` 風險 %，方便依 J Law 的小風險原則算部位
- 技術分：RS＋產業加分＋縮量回踩 10＋守住支撐 10＋近 10 日振幅 ≤ 8% 10＋突破量 ≥ 2 倍 5

### 月營收（加 `--repo` 才會查，只查 A、C 名單）
- 每檔查最近 4 個月營收年增率（MOPS，請求間隔 3 秒，過去月份快取在 `revenue/`）
- 成長：近 3 個月年增率都 > 0（+8 分）
- 加速：成長，且最新月年增率高於前 3 個月平均（+15 分）
- 衰退：最新月年增率 < 0（-10 分）
- B、D 名單＝A、C 中營收「成長」或「加速」者，依 `total_score`（技術分＋營收分）排序。A、C 也會附營收欄位方便對照。
- 沒加 `--repo` 時只輸出 A、C。

### 可調參數
`--max-from-high`（預設 15）、`--min-value`（預設 5e7）、`--base-days`（預設 120）、`--near-pivot`（預設 8）、`--date`。

KD、RSI、MACD 都在 indicators 檔裡，可以拿來再加條件。名單是規則初篩，進場前請看線圖確認。

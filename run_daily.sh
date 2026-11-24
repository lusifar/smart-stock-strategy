#!/bin/bash
# 每日流程：收集日線與月營收 → 計算名單 → commit vcp-data/ 並 push 到 dev。
# 由 launchd/com.vcp.collect.plist 在交易日 17:30 執行，輸出附加到 vcp-data/daily.log。
#
# 安全措施：
#   - 只 commit vcp-data/ 底下的變更，不會把你正在改的程式一起 commit。
#   - 目前分支不是 dev 就不 commit、不 push。
#   - 收集或計算失敗就停止，不 commit。
#   - 同時只會有一個在跑（用 lock 目錄）。
# 手動測試（只收集與計算，不 commit、不 push）：DRY_RUN=1 ./run_daily.sh
set -uo pipefail

cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
BRANCH=dev
LOCK=vcp-data/.daily.lock

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

if ! mkdir "$LOCK" 2>/dev/null; then
  log "上一次的每日流程還在執行（$LOCK 存在），略過。"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

log "=== 每日流程開始 ==="

if ! uv run --project TWSEMCPServer python collect_vcp.py --repo TWSEMCPServer --out vcp-data; then
  log "收集失敗，停止（不 commit）。"
  exit 1
fi

if ! python3 compute_vcp.py --data vcp-data; then
  log "計算失敗，停止（不 commit）。"
  exit 1
fi

python3 compute_dividend.py --data vcp-data || log "股利計算失敗（仍 commit 其他資料）。"

if [ "${DRY_RUN:-0}" = "1" ]; then
  log "DRY_RUN：略過 commit 與 push。待 commit 的變更："
  git status --short -- vcp-data | head -20
  exit 0
fi

current=$(git rev-parse --abbrev-ref HEAD)
if [ "$current" != "$BRANCH" ]; then
  log "目前分支是 $current，不是 $BRANCH，略過 commit 與 push。"
  exit 0
fi

git add -A -- vcp-data
if git diff --cached --quiet -- vcp-data; then
  log "資料沒有變更，不 commit。"
  exit 0
fi

latest=$(ls vcp-data/twse | sort | tail -1 | cut -c1-8)
if ! git commit -q -m "data: daily update ${latest}" -- vcp-data; then
  log "commit 失敗。"
  exit 1
fi
log "已 commit：$(git log --oneline -1)"

if git push -q origin "$BRANCH"; then
  log "已 push 到 origin/$BRANCH。"
else
  log "push 失敗（網路或認證問題），commit 已保留在本機，下次執行會一起 push。"
  exit 1
fi
log "=== 每日流程完成 ==="

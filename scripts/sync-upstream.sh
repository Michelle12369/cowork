#!/usr/bin/env bash
# 單向同步：把上游（GitHub 經 internal GitLab 鏡像）整棵樹取代進來，再還原 internal 獨佔路徑。
# 產出一條 sync/upstream-<sha> branch 供人工適配後發 PR，NEVER 直接推 develop。
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# internal 主線名稱由第一個位置參數帶入(例如 bash scripts/sync-upstream.sh feature/main);
# 未帶則預設 develop。NEVER 直接改本腳本字面值——本腳本是上游檔,同步會把修改蓋回
# 預設,下一次執行就拒跑。
MAIN_BRANCH="${1:-develop}"

# 第二參數：要同步的上游 ref（例如 gl/feat/foo）；預設 gl/master＝正式同步。
# 非 gl/master 一律進測試模式：前綴隔離讓測試 commit 不產生 upstream-sync: 開頭的
# 行；真正的硬保證是 trailer 換名（Test-Upstream-Commit）——就算被誤選，
# sed -n 's/^Upstream-Commit: //p' 也解不出 sha，成不了錨點。
UPSTREAM_REF="${2:-gl/master}"
TEST_MODE=0
[ "$UPSTREAM_REF" != "gl/master" ] && TEST_MODE=1

# 清單是唯一事實來源：還原與守門排除範圍共用它，避免兩者失同步而漏守或誤報。
OWNED=(); EXCLUDES=()
while read -r ownedPath; do
  [ -n "$ownedPath" ] || continue
  OWNED+=("$ownedPath"); EXCLUDES+=(":(exclude)$ownedPath")
done < scripts/internal-owned-paths.txt

# --multiple 讓每個引數各自當一個 remote 抓；沒有它 `gl origin` 會被解成 gl 底下的 refspec。
git fetch -q --multiple gl origin

if ! git rev-parse --verify -q "${UPSTREAM_REF}^{commit}" >/dev/null; then
  echo "找不到 ${UPSTREAM_REF}——GitLab 鏡像可能未帶 feature branches，檢查鏡像設定或手動推入。" >&2
  exit 1
fi

# 基準點＝origin/develop 上最後一顆已落地的同步 commit；用 commit 而非 tag，因為 tag 可能
# 隨分支移動，指向從未真正落地的狀態。
LAST_SYNC=$(git log "origin/${MAIN_BRANCH}" --grep='^upstream-sync: ' -1 --format=%H || true)
if [ -z "$LAST_SYNC" ]; then
  echo "找不到基準同步 commit。首次同步 MUST 先人工 bootstrap（見 docs/internal-sync.md）。" >&2
  exit 1
fi
LAST_UPSTREAM=$(git log -1 --format=%B "$LAST_SYNC" | sed -n 's/^Upstream-Commit: //p')
if [ -z "$LAST_UPSTREAM" ]; then
  echo "基準 commit $LAST_SYNC 缺少 Upstream-Commit trailer——同步 PR 被 squash 了？" >&2
  exit 1
fi

# 正式同步的錨點鏈 MUST 單調前進：上一個錨點必須是本次同步目標的祖先。
# 不是＝錨點被污染（測試/feature 同步誤入主線）或上游 force-push，先人工修錨再同步。
if [ "$TEST_MODE" = "0" ] && ! git merge-base --is-ancestor "$LAST_UPSTREAM" "$UPSTREAM_REF"; then
  echo "基準錨點 ${LAST_UPSTREAM} 不是 ${UPSTREAM_REF} 的祖先——錨點鏈回退或被污染，MUST 人工修復。" >&2
  exit 1
fi

# 前置守門——全部 MUST 通過，NEVER 為了讓同步跑完而跳過。
if [ "$(git rev-parse --abbrev-ref HEAD)" != "$MAIN_BRANCH" ]; then
  echo "MUST 在 ${MAIN_BRANCH} 上執行（腳本結束時會留在同步 branch）。" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "worktree 不乾淨；read-tree --reset 會吃掉未提交的修改。" >&2
  exit 1
fi
if [ -n "$(git diff --name-only "$LAST_SYNC" "$MAIN_BRANCH" -- . "${EXCLUDES[@]}")" ]; then
  echo "獨佔清單外有 internal 改動，同步會無聲抹掉它們：" >&2
  git diff --name-only "$LAST_SYNC" "$MAIN_BRANCH" -- . "${EXCLUDES[@]}" >&2
  exit 1
fi
if [ -n "$(git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}")" ]; then
  echo "有野生 untracked 檔，git add -A 會把它們永久收編成同步 commit 的一部分：" >&2
  git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}" >&2
  exit 1
fi

UPSTREAM=$(git rev-parse "$UPSTREAM_REF")
UPSTREAM_SHORT=$(git rev-parse --short "$UPSTREAM_REF")

# 雙邊擁有檔：列出上游動過的交給人工調和。錨點 MUST 是 $LAST_UPSTREAM，不是 $LAST_SYNC
# ——後者是 internal 版，拿它比上游永遠有差、每次都誤報。
MANUAL_NOTES=""
while read -r mergePath; do
  [ -n "$mergePath" ] || continue
  if ! git diff --quiet "$LAST_UPSTREAM" "$UPSTREAM_REF" -- "$mergePath"; then
    MANUAL_NOTES="${MANUAL_NOTES}需人工調和：${mergePath}"$'\n'
  fi
done < scripts/manual-merge-paths.txt

if [ "$TEST_MODE" = "1" ]; then
  # 測試模式三重隔離：test/ 前綴、test-sync: 前綴、trailer 換名——就算被違規
  # squash 進主線，基準 grep 與 trailer 解析也都讀不到它。
  SYNC_BRANCH="test/upstream-${UPSTREAM_SHORT}"
  COMMIT_PREFIX="test-sync"
  TRAILER_NAME="Test-Upstream-Commit"
else
  SYNC_BRANCH="sync/upstream-${UPSTREAM_SHORT}"
  COMMIT_PREFIX="upstream-sync"
  TRAILER_NAME="Upstream-Commit"
fi
git checkout -qb "$SYNC_BRANCH"
git read-tree -u --reset "$UPSTREAM_REF"        # 整棵樹換成指定上游 ref，含其刪除
git checkout "$MAIN_BRANCH" -- "${OWNED[@]}"    # 還原 internal 獨佔路徑（相對切出點淨變更為零）
git add -A
# --allow-empty：雙邊擁有檔還原後淨變更常是零，但此 commit MUST 落地——它是下次同步的
# 基準點，也是 MANUAL_NOTES 待辦的唯一落地處。
git commit -q --allow-empty -m "${COMMIT_PREFIX}: 同步至 ${UPSTREAM_SHORT}（${UPSTREAM_REF}）" \
  -m "${MANUAL_NOTES}" -m "${TRAILER_NAME}: ${UPSTREAM}"
git push -q -u origin "$SYNC_BRANCH"

if [ "$TEST_MODE" = "1" ]; then
  echo "已推出測試 branch ${SYNC_BRANCH}（來源 ${UPSTREAM_REF}）。"
  echo "  NEVER merge 進 ${MAIN_BRANCH}——測完即刪（本地與 origin 都刪）。"
  echo "  正式進場路徑：上游 merge master 後走正常同步。"
else
  echo "已推出 ${SYNC_BRANCH}。接著人工完成："
  echo "  1. 檢視 diff，確認上游改動"
  echo "  2. 調和 commit body 列出的雙邊擁有檔"
  echo "  3. 接縫適配（上游若改了 AgentRuntime 等介面，internal 實作要跟著改）"
  echo "  4. 發 PR 進 ${MAIN_BRANCH}，CI 綠燈後合併（MUST NOT squash）"
fi

#!/usr/bin/env bash
# 單向同步：把上游（GitHub 經 internal GitLab 鏡像）整棵樹取代進來，再還原 internal 獨佔路徑。
# 產出一條 sync/upstream-<sha> branch 供人工適配後發 PR，NEVER 直接推 feature/first-sync3。
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# 清單是唯一事實來源：還原與守門排除範圍共用它，避免兩者失同步而漏守或誤報。
OWNED=(); EXCLUDES=()
while read -r ownedPath; do
  [ -n "$ownedPath" ] || continue
  OWNED+=("$ownedPath"); EXCLUDES+=(":(exclude)$ownedPath")
done < scripts/internal-owned-paths.txt

# --multiple 讓每個引數各自當一個 remote 抓；沒有它 `gl origin` 會被解成 gl 底下的 refspec。
git fetch -q --multiple gl origin

# 基準點＝origin/feature/first-sync3 上最後一顆已落地的同步 commit；用 commit 而非 tag，因為 tag 可能
# 隨分支移動，指向從未真正落地的狀態。
LAST_SYNC=$(git log origin/feature/first-sync3 --grep='^upstream-sync: ' -1 --format=%H || true)
if [ -z "$LAST_SYNC" ]; then
  echo "找不到基準同步 commit。首次同步 MUST 先人工 bootstrap（見 docs/internal-sync.md）。" >&2
  exit 1
fi
LAST_UPSTREAM=$(git log -1 --format=%B "$LAST_SYNC" | sed -n 's/^Upstream-Commit: //p')
if [ -z "$LAST_UPSTREAM" ]; then
  echo "基準 commit $LAST_SYNC 缺少 Upstream-Commit trailer——同步 PR 被 squash 了？" >&2
  exit 1
fi

# 前置守門——全部 MUST 通過，NEVER 為了讓同步跑完而跳過。
if [ "$(git rev-parse --abbrev-ref HEAD)" != feature/first-sync3 ]; then
  echo "MUST 在 feature/first-sync3 上執行（腳本結束時會留在同步 branch）。" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "worktree 不乾淨；read-tree --reset 會吃掉未提交的修改。" >&2
  exit 1
fi
if [ -n "$(git diff --name-only "$LAST_SYNC" feature/first-sync3 -- . "${EXCLUDES[@]}")" ]; then
  echo "獨佔清單外有 internal 改動，同步會無聲抹掉它們：" >&2
  git diff --name-only "$LAST_SYNC" feature/first-sync3 -- . "${EXCLUDES[@]}" >&2
  exit 1
fi
if [ -n "$(git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}")" ]; then
  echo "有野生 untracked 檔，git add -A 會把它們永久收編成同步 commit 的一部分：" >&2
  git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}" >&2
  exit 1
fi

UPSTREAM=$(git rev-parse gl/master)
UPSTREAM_SHORT=$(git rev-parse --short gl/master)

# 雙邊擁有檔：列出上游動過的交給人工調和。錨點 MUST 是 $LAST_UPSTREAM，不是 $LAST_SYNC
# ——後者是 internal 版，拿它比上游永遠有差、每次都誤報。
MANUAL_NOTES=""
while read -r mergePath; do
  [ -n "$mergePath" ] || continue
  if ! git diff --quiet "$LAST_UPSTREAM" gl/master -- "$mergePath"; then
    MANUAL_NOTES="${MANUAL_NOTES}需人工調和：${mergePath}"$'\n'
  fi
done < scripts/manual-merge-paths.txt

SYNC_BRANCH="sync/upstream-${UPSTREAM_SHORT}"
git checkout -qb "$SYNC_BRANCH"
git read-tree -u --reset gl/master              # 整棵樹換成上游，含上游的刪除
git checkout feature/first-sync3 -- "${OWNED[@]}"           # 還原 internal 獨佔路徑（相對切出點淨變更為零）
git add -A
# --allow-empty：雙邊擁有檔還原後淨變更常是零，但此 commit MUST 落地——它是下次同步的
# 基準點，也是 MANUAL_NOTES 待辦的唯一落地處。
git commit -q --allow-empty -m "upstream-sync: 同步至 ${UPSTREAM_SHORT}" \
  -m "${MANUAL_NOTES}" -m "Upstream-Commit: ${UPSTREAM}"
git push -q -u origin "$SYNC_BRANCH"

echo "已推出 $SYNC_BRANCH。接著人工完成："
echo "  1. 檢視 diff，確認上游改動"
echo "  2. 調和 commit body 列出的雙邊擁有檔"
echo "  3. 接縫適配（上游若改了 AgentRuntime 等介面，internal 實作要跟著改）"
echo "  4. 發 PR 進 feature/first-sync3，CI 綠燈後合併（MUST NOT squash）"

#!/usr/bin/env bash
# 單向同步：把上游（GitHub 經 internal GitLab 鏡像）整棵樹取代進來，再還原 internal 獨佔路徑。
# 產出一條 sync/upstream-<sha> branch 供人工適配後發 PR，NEVER 直接推 develop。
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# 清單是唯一事實來源：還原用它，守門的排除範圍也用它——兩者 MUST 同源，
# 否則清單一改就會漏守或誤報，而誤報會訓練人跳過守門。
OWNED=(); EXCLUDES=()
while read -r ownedPath; do
  [ -n "$ownedPath" ] || continue
  OWNED+=("$ownedPath"); EXCLUDES+=(":(exclude)$ownedPath")
done < scripts/internal-owned-paths.txt

# --multiple 對每個引數分別當一個 remote 抓；`git fetch gl origin` 會把 origin 當成
# gl 這個 remote 底下的 refspec 去解析，兩者語意不同，前者才是「兩個 remote 都更新」。
git fetch -q --multiple gl origin

# 基準點＝origin/develop 上最後一顆已落地的同步 commit。用 commit 而非 tag：PR 可能
# 放棄或擱置，tag 若在推分支時就移動，基準會指向從未落地的狀態。
LAST_SYNC=$(git log origin/develop --grep='^upstream-sync: ' -1 --format=%H || true)
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
if [ "$(git rev-parse --abbrev-ref HEAD)" != develop ]; then
  echo "MUST 在 develop 上執行（腳本結束時會留在同步 branch）。" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "worktree 不乾淨；read-tree --reset 會吃掉未提交的修改。" >&2
  exit 1
fi
if [ -n "$(git diff --name-only "$LAST_SYNC" develop -- . "${EXCLUDES[@]}")" ]; then
  echo "獨佔清單外有 internal 改動，同步會無聲抹掉它們：" >&2
  git diff --name-only "$LAST_SYNC" develop -- . "${EXCLUDES[@]}" >&2
  exit 1
fi
if [ -n "$(git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}")" ]; then
  echo "有野生 untracked 檔，git add -A 會把它們永久收編成同步 commit 的一部分：" >&2
  git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}" >&2
  exit 1
fi

UPSTREAM=$(git rev-parse gl/master)
UPSTREAM_SHORT=$(git rev-parse --short gl/master)

# 雙邊擁有檔：列出上游這次動過的，交給人工調和。錨點 MUST 是 $LAST_UPSTREAM；用 $LAST_SYNC
# 會拿 internal 版 pom 去比上游，永遠有差、每次都報。
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
git checkout develop -- "${OWNED[@]}"           # 還原 internal 獨佔路徑（相對切出點淨變更為零）
git add -A
# --allow-empty：雙邊擁有檔（如 pom.xml）被還原後淨變更常常是零，但這顆 commit 仍
# MUST 落地——它同時是下次同步的基準點，也是待辦（MANUAL_NOTES）唯一的落地處。
git commit -q --allow-empty -m "upstream-sync: 同步至 ${UPSTREAM_SHORT}" \
  -m "${MANUAL_NOTES}" -m "Upstream-Commit: ${UPSTREAM}"
git push -q -u origin "$SYNC_BRANCH"

echo "已推出 $SYNC_BRANCH。接著人工完成："
echo "  1. 檢視 diff，確認上游改動"
echo "  2. 調和 commit body 列出的雙邊擁有檔"
echo "  3. 接縫適配（上游若改了 AgentRuntime 等介面，internal 實作要跟著改）"
echo "  4. 發 PR 進 develop，CI 綠燈後合併（MUST NOT squash）"

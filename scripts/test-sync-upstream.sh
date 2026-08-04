#!/usr/bin/env bash
# sync-upstream.sh 的守門驗證：每個情境 MUST 讓腳本以非零碼中止，守門絕不可跳過。
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK_ROOT=$(mktemp -d)
trap 'rm -rf "$WORK_ROOT"' EXIT
FAILURES=0

expect_abort() {
  local caseName=$1
  shift
  if (cd "$WORK_ROOT/clone" && "$@" >/dev/null 2>&1); then
    echo "FAIL: $caseName —— 腳本應中止卻成功了"
    FAILURES=$((FAILURES + 1))
  else
    echo "ok: $caseName"
  fi
}

setup() {
  rm -rf "$WORK_ROOT"/upstream "$WORK_ROOT"/origin "$WORK_ROOT"/clone "$WORK_ROOT"/seed
  git init -q --bare "$WORK_ROOT/upstream"
  git init -q --bare "$WORK_ROOT/origin"

  git clone -q "$WORK_ROOT/upstream" "$WORK_ROOT/seed"
  (
    cd "$WORK_ROOT/seed"
    git config user.email t@t; git config user.name t
    mkdir -p scripts backend
    cp "$SCRIPT_DIR/sync-upstream.sh" scripts/
    cp "$SCRIPT_DIR/internal-owned-paths.txt" scripts/
    cp "$SCRIPT_DIR/manual-merge-paths.txt" scripts/
    echo "<project/>" > backend/pom.xml
    echo shared > shared.txt
    git add -A && git commit -qm "init"
    git push -q origin HEAD:master
  )

  git clone -q "$WORK_ROOT/origin" "$WORK_ROOT/clone"
  (
    cd "$WORK_ROOT/clone"
    git config user.email t@t; git config user.name t
    git remote add gl "$WORK_ROOT/upstream"
    git fetch -q gl
    git checkout -qb develop gl/master
    # 首次同步前置條件：internal 獨佔檔 MUST 先 commit 到 develop 才有東西可還原（見 docs/internal-sync.md）。
    mkdir -p internal backend/src/internal backend/src/main/resources \
      frontend/src/bootstrap deepagent-service/app/agent/runtime
    echo "internal owned" > internal/README.md
    echo "# internal owned" > .env.internal.example
    echo "internal owned" > backend/src/internal/Marker.java
    echo "internal.owned=true" > backend/src/main/resources/application.properties
    echo "<html>internal owned</html>" > frontend/index.html
    echo "export {};" > frontend/src/bootstrap/internal.impl.ts
    echo "# internal owned" > deepagent-service/app/agent/runtime/internal_runtime.py
    git add -A && git commit -qm "internal 獨佔檔 bootstrap"
    # bootstrap：第一顆同步 commit，之後的基準點由它提供。
    git commit -q --allow-empty -m "upstream-sync: bootstrap" \
      -m "Upstream-Commit: $(git rev-parse gl/master)"
    git push -q -u origin develop
  )
}

# 情境 ①：獨佔清單外有 internal 改動
setup
(cd "$WORK_ROOT/clone" && echo tampered > shared.txt && git commit -qam "越界" && git push -q origin develop)
expect_abort "① 獨佔清單外有 internal 改動" bash scripts/sync-upstream.sh

# 情境 ②：有野生 untracked 檔
setup
(cd "$WORK_ROOT/clone" && echo stray > stray.txt)
expect_abort "② 有野生 untracked 檔" bash scripts/sync-upstream.sh

# 情境 ③：不在 develop 上
setup
(cd "$WORK_ROOT/clone" && git checkout -qb feature/x)
expect_abort "③ 不在 develop 上" bash scripts/sync-upstream.sh

# 情境 ④：找不到基準同步 commit（未 bootstrap）
setup
(cd "$WORK_ROOT/clone" && git checkout -qB develop gl/master && git push -qf origin develop)
expect_abort "④ 未 bootstrap（無基準同步 commit）" bash scripts/sync-upstream.sh

expect_note() {
  local caseName=$1
  local shouldContain=$2
  local body
  body=$(cd "$WORK_ROOT/clone" && git log -1 --format=%B)
  if [ "$shouldContain" = yes ] && ! grep -q "需人工調和：backend/pom.xml" <<<"$body"; then
    echo "FAIL: $caseName —— commit body 少了待辦行"; FAILURES=$((FAILURES + 1)); return
  fi
  if [ "$shouldContain" = no ] && grep -q "需人工調和" <<<"$body"; then
    echo "FAIL: $caseName —— commit body 不該有待辦行"; FAILURES=$((FAILURES + 1)); return
  fi
  echo "ok: $caseName"
}

# 情境 ⑤：上游動過 pom.xml → commit body MUST 列出待辦
setup
(cd "$WORK_ROOT/seed" && echo "<project><!--changed--></project>" > backend/pom.xml \
  && git commit -qam "上游改 pom" && git push -q origin HEAD:master)
(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh >/dev/null 2>&1)
expect_note "⑤ 上游動過 pom.xml → 列出待辦" yes

# 情境 ⑥：錨點回歸——上游未動 pom 時不得列出待辦（用 $LAST_SYNC 當錨點會誤報）
setup
(cd "$WORK_ROOT/seed" && echo other > other.txt && git add -A && git commit -qm "上游改別的" \
  && git push -q origin HEAD:master)
(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh >/dev/null 2>&1)
expect_note "⑥ 上游未動 pom.xml → 不列待辦" no

# 情境 ⑦：基準點取自 origin/develop——同步 branch 已推但 PR 未合併時，基準不得前移
setup
BASE_BEFORE=$(cd "$WORK_ROOT/clone" && git log origin/develop --grep='^upstream-sync: ' -1 --format=%H)
(cd "$WORK_ROOT/seed" && echo more > more.txt && git add -A && git commit -qm "上游再改" \
  && git push -q origin HEAD:master)
(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh >/dev/null 2>&1)
BASE_AFTER=$(cd "$WORK_ROOT/clone" && git fetch -q origin \
  && git log origin/develop --grep='^upstream-sync: ' -1 --format=%H)
if [ "$BASE_BEFORE" = "$BASE_AFTER" ]; then
  echo "ok: ⑦ PR 未合併時基準不前移"
else
  echo "FAIL: ⑦ 基準前移了——基準必須取自 origin/develop 而非分支或 tag"
  FAILURES=$((FAILURES + 1))
fi

echo "---"
if [ "$FAILURES" -gt 0 ]; then echo "$FAILURES 項失敗"; exit 1; fi
echo "全部通過"

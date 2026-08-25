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
      frontend/src/bootstrap deepagent-service/app/agent/runtime deepagent-service/app/engine
    echo "internal owned" > internal/README.md
    echo "# internal owned" > .env.internal.example
    echo "internal owned" > backend/src/internal/Marker.java
    echo "internal.owned=true" > backend/src/main/resources/application.properties
    echo "<html>internal owned</html>" > frontend/index.html
    echo "export {};" > frontend/src/bootstrap/internal.impl.ts
    echo "# internal owned" > deepagent-service/app/agent/runtime/internal_runtime.py
    echo "# internal owned" > deepagent-service/app/engine/upload_decrypt.py
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

# 情境 ⑧：位置參數覆寫主線名稱——internal 主線非 develop 時腳本仍可跑
# 獨立一組 throwaway repo（override-*），避免污染上面共用 setup() 的 develop 情境。
OVERRIDE_BRANCH="feature/main"
rm -rf "$WORK_ROOT"/override-upstream "$WORK_ROOT"/override-origin \
  "$WORK_ROOT"/override-clone "$WORK_ROOT"/override-seed
git init -q --bare "$WORK_ROOT/override-upstream"
git init -q --bare "$WORK_ROOT/override-origin"

git clone -q "$WORK_ROOT/override-upstream" "$WORK_ROOT/override-seed"
(
  cd "$WORK_ROOT/override-seed"
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

git clone -q "$WORK_ROOT/override-origin" "$WORK_ROOT/override-clone"
(
  cd "$WORK_ROOT/override-clone"
  git config user.email t@t; git config user.name t
  git remote add gl "$WORK_ROOT/override-upstream"
  git fetch -q gl
  git checkout -qb "$OVERRIDE_BRANCH" gl/master
  mkdir -p internal backend/src/internal backend/src/main/resources \
    frontend/src/bootstrap deepagent-service/app/agent/runtime deepagent-service/app/engine
  echo "internal owned" > internal/README.md
  echo "# internal owned" > .env.internal.example
  echo "internal owned" > backend/src/internal/Marker.java
  echo "internal.owned=true" > backend/src/main/resources/application.properties
  echo "<html>internal owned</html>" > frontend/index.html
  echo "export {};" > frontend/src/bootstrap/internal.impl.ts
  echo "# internal owned" > deepagent-service/app/agent/runtime/internal_runtime.py
  echo "# internal owned" > deepagent-service/app/engine/upload_decrypt.py
  git add -A && git commit -qm "internal 獨佔檔 bootstrap"
  git commit -q --allow-empty -m "upstream-sync: bootstrap" \
    -m "Upstream-Commit: $(git rev-parse gl/master)"
  git push -q -u origin "$OVERRIDE_BRANCH"
)

# 比照既有成功情境（⑤⑥⑦）的斷言方式：看落地的側面效果，而非腳本自身 exit code。
(cd "$WORK_ROOT/override-clone" && bash scripts/sync-upstream.sh "$OVERRIDE_BRANCH" >/dev/null 2>&1)
UPSTREAM_SHORT=$(cd "$WORK_ROOT/override-clone" && git rev-parse --short gl/master)
if (cd "$WORK_ROOT/override-clone" && git ls-remote --exit-code origin "sync/upstream-${UPSTREAM_SHORT}" >/dev/null 2>&1); then
  echo "ok: ⑧ 位置參數覆寫主線名稱——同步在非 develop 主線（${OVERRIDE_BRANCH}）上成功產出 sync branch"
else
  echo "FAIL: ⑧ 位置參數覆寫主線名稱——未推出 sync branch"
  FAILURES=$((FAILURES + 1))
fi

# 情境 ⑨：測試模式產物形狀——同步 feature branch（非 gl/master）落地的三重隔離標記
setup
(
  cd "$WORK_ROOT/seed"
  git checkout -qb feat/x
  echo "feature marker" > feature-marker.txt
  git add -A && git commit -qm "上游 feature 分支新檔"
  git push -q origin HEAD:feat/x
)
(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh develop gl/feat/x >/dev/null 2>&1)
FEATURE_SHORT=$(cd "$WORK_ROOT/clone" && git rev-parse --short gl/feat/x)
FEATURE_FULL=$(cd "$WORK_ROOT/clone" && git rev-parse gl/feat/x)
TEST_BRANCH="test/upstream-${FEATURE_SHORT}"
SUBJECT=$(cd "$WORK_ROOT/clone" && git log -1 --format=%s "origin/${TEST_BRANCH}" 2>/dev/null || true)
BODY=$(cd "$WORK_ROOT/clone" && git log -1 --format=%B "origin/${TEST_BRANCH}" 2>/dev/null || true)
HAS_MARKER=$(cd "$WORK_ROOT/clone" && git ls-tree -r --name-only "origin/${TEST_BRANCH}" 2>/dev/null \
  | grep -c '^feature-marker.txt$' || true)
OWNED_CONTENT=$(cd "$WORK_ROOT/clone" && git show "origin/${TEST_BRANCH}:internal/README.md" 2>/dev/null || true)
if [[ "$SUBJECT" == test-sync:\ * ]] && grep -q "Test-Upstream-Commit: ${FEATURE_FULL}" <<<"$BODY" \
  && [ "$HAS_MARKER" = "1" ] && [ "$OWNED_CONTENT" = "internal owned" ]; then
  echo "ok: ⑨ 測試模式產物形狀"
else
  echo "FAIL: ⑨ 測試模式產物形狀 —— subject=[$SUBJECT] marker=[$HAS_MARKER] owned=[$OWNED_CONTENT]"
  FAILURES=$((FAILURES + 1))
fi

# 情境 ⑩：測試 commit 永不成錨（對抗性）——接續⑨，模擬違規把測試 branch merge 進 develop，
# 驗證基準查找仍選到 bootstrap（不是 test-sync），且後續正式同步不受污染、照常成功。
# 用 -s ours 是為了單純模擬「test-sync commit 混進 develop 歷史」這個違規本身，
# 不夾帶內容變動——內容變動會觸發另一道「獨佔清單外有 internal 改動」守門，混淆此情境要驗的重點。
BOOTSTRAP_SHA=$(cd "$WORK_ROOT/clone" && git log origin/develop --grep='^upstream-sync: ' -1 --format=%H)
(
  cd "$WORK_ROOT/clone"
  git checkout -q develop
  git merge -q -s ours "$TEST_BRANCH" -m "違規：把測試同步 merge 進 develop（模擬）"
  git push -q origin develop
)
(
  cd "$WORK_ROOT/seed"
  git checkout -q master
  echo poststate > poststate.txt
  git add -A && git commit -qm "上游再改一次"
  git push -q origin HEAD:master
)
(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh >/dev/null 2>&1)
SELECTED_SHA=$(cd "$WORK_ROOT/clone" && git fetch -q origin \
  && git log origin/develop --grep='^upstream-sync: ' -1 --format=%H)
NEW_UPSTREAM_SHORT=$(cd "$WORK_ROOT/clone" && git rev-parse --short gl/master)
if [ "$SELECTED_SHA" = "$BOOTSTRAP_SHA" ] \
  && (cd "$WORK_ROOT/clone" && git ls-remote --exit-code origin "sync/upstream-${NEW_UPSTREAM_SHORT}" >/dev/null 2>&1); then
  echo "ok: ⑩ 測試 commit 永不成錨（對抗性）"
else
  echo "FAIL: ⑩ 測試 commit 永不成錨 —— selected=$SELECTED_SHA bootstrap=$BOOTSTRAP_SHA"
  FAILURES=$((FAILURES + 1))
fi

# 情境 ⑪：未知的上游 ref
setup
expect_abort "⑪ 未知的上游 ref" bash scripts/sync-upstream.sh develop gl/no-such-branch

# 情境 ⑫：錨點鏈污染守門——偽造一顆錨點 commit，Upstream-Commit trailer 指到不在
# gl/master 祖先鏈上的 sha（另一條 feature branch 的 commit），正式同步 MUST 中止。
setup
(
  cd "$WORK_ROOT/seed"
  git checkout -qb feat/poison
  echo poison > poison.txt
  git add -A && git commit -qm "不在祖先鏈上的 commit"
  git push -q origin HEAD:feat/poison
)
POISON_SHA=$(cd "$WORK_ROOT/clone" && git fetch -q gl && git rev-parse gl/feat/poison)
(
  cd "$WORK_ROOT/clone"
  git commit -q --allow-empty -m "upstream-sync: 污染" -m "Upstream-Commit: ${POISON_SHA}"
  git push -q origin develop
)
expect_abort "⑫ 錨點鏈污染守門" bash scripts/sync-upstream.sh

echo "---"
if [ "$FAILURES" -gt 0 ]; then echo "$FAILURES 項失敗"; exit 1; fi
echo "全部通過"

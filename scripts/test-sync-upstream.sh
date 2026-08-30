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
      frontend/src/bootstrap deepagent-service/app/agent/runtime deepagent-service/app/engine \
      deepagent-service/app/agent/connectors
    echo "internal owned" > internal/README.md
    echo "# internal owned" > .env.internal.example
    echo "internal owned" > backend/src/internal/Marker.java
    echo "internal.owned=true" > backend/src/main/resources/application.properties
    echo "<html>internal owned</html>" > frontend/index.html
    echo "export {};" > frontend/src/bootstrap/internal.impl.ts
    echo "# internal owned" > deepagent-service/app/agent/runtime/internal_runtime.py
    echo "# internal owned" > deepagent-service/app/agent/connectors/catalog.py
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

# 附加斷言（同一情境的落地產物）：官方模式 commit subject MUST 與原版 byte-identical
# ——不帶 UPSTREAM_REF 後綴。測試模式為了在 in-place 反覆疊快照時方便辨識來源
# feature branch，subject 會附加（${UPSTREAM_REF}），但官方同步永遠是 gl/master，
# 加了只是雜訊且破壞既有 commit subject 格式相容性；過去沒有測試釘住這個格式，才
# 讓官方模式一度意外沾上這個後綴，這裡補上，往後再漂移會被抓到。
UPSTREAM_SHORT_5=$(cd "$WORK_ROOT/clone" && git rev-parse --short gl/master)
SUBJECT_5=$(cd "$WORK_ROOT/clone" && git log -1 --format=%s)
if [ "$SUBJECT_5" = "upstream-sync: 同步至 ${UPSTREAM_SHORT_5}" ]; then
  echo "ok: ⑤ 官方 commit subject 與原版 byte-identical（不帶 ref 後綴）"
else
  echo "FAIL: ⑤ 官方 commit subject 漂移 —— got=[$SUBJECT_5] want=[upstream-sync: 同步至 ${UPSTREAM_SHORT_5}]"
  FAILURES=$((FAILURES + 1))
fi

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
    frontend/src/bootstrap deepagent-service/app/agent/runtime deepagent-service/app/engine \
    deepagent-service/app/agent/connectors
  echo "internal owned" > internal/README.md
  echo "# internal owned" > .env.internal.example
  echo "internal owned" > backend/src/internal/Marker.java
  echo "internal.owned=true" > backend/src/main/resources/application.properties
  echo "<html>internal owned</html>" > frontend/index.html
  echo "export {};" > frontend/src/bootstrap/internal.impl.ts
  echo "# internal owned" > deepagent-service/app/agent/runtime/internal_runtime.py
  echo "# internal owned" > deepagent-service/app/agent/connectors/catalog.py
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

# 情境 ⑨：測試模式產物形狀（in-place，單參數 gl/ 語法糖）——disposable 的
# test/upstream-<sha> 一次性 branch 已移除，測試模式只剩 in-place：使用者自建
# test/mine，單參數 `gl/feat/x` 形式下 MAIN_BRANCH 自動預設 develop（涵蓋語法糖
# 分支），就地疊一顆快照 commit，斷言雙重隔離標記（test-sync: 前綴、trailer 換名）
# 與擁有路徑正確還原。
setup
(
  cd "$WORK_ROOT/seed"
  git checkout -qb feat/x
  echo "feature marker" > feature-marker.txt
  git add -A && git commit -qm "上游 feature 分支新檔"
  git push -q origin HEAD:feat/x
)
(cd "$WORK_ROOT/clone" && git checkout -qb test/mine)
(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh gl/feat/x >/dev/null 2>&1)
FEATURE_FULL=$(cd "$WORK_ROOT/clone" && git rev-parse gl/feat/x)
CURRENT_BRANCH_9=$(cd "$WORK_ROOT/clone" && git rev-parse --abbrev-ref HEAD)
SUBJECT_9=$(cd "$WORK_ROOT/clone" && git log -1 --format=%s)
BODY_9=$(cd "$WORK_ROOT/clone" && git log -1 --format=%B)
HAS_MARKER_9=$(cd "$WORK_ROOT/clone" && git ls-files | grep -c '^feature-marker.txt$' || true)
OWNED_CONTENT_9=$(cd "$WORK_ROOT/clone" && cat internal/README.md 2>/dev/null || true)
REMOTE_HAS_MINE_9=0
(cd "$WORK_ROOT/clone" && git ls-remote --exit-code origin test/mine >/dev/null 2>&1) && REMOTE_HAS_MINE_9=1
if [ "$CURRENT_BRANCH_9" = "test/mine" ] && [[ "$SUBJECT_9" == test-sync:\ * ]] \
  && grep -q "Test-Upstream-Commit: ${FEATURE_FULL}" <<<"$BODY_9" \
  && [ "$HAS_MARKER_9" = "1" ] && [ "$OWNED_CONTENT_9" = "internal owned" ] \
  && [ "$REMOTE_HAS_MINE_9" = "1" ]; then
  echo "ok: ⑨ 測試模式產物形狀（in-place，單參數 gl/ 語法糖）"
else
  echo "FAIL: ⑨ 測試模式產物形狀 —— current=[$CURRENT_BRANCH_9] subject=[$SUBJECT_9] marker=[$HAS_MARKER_9] owned=[$OWNED_CONTENT_9] remote=[$REMOTE_HAS_MINE_9]"
  FAILURES=$((FAILURES + 1))
fi

# 情境 ⑩：測試 commit 永不成錨（對抗性，in-place 版本）——接續⑨，模擬違規把 in-place
# 測試 branch（test/mine）merge 進 develop，驗證基準查找仍選到 bootstrap（不是
# test-sync），且後續正式同步不受污染、照常成功。用 -s ours 是為了單純模擬
# 「test-sync commit 混進 develop 歷史」這個違規本身，不夾帶內容變動——內容變動會
# 觸發另一道「獨佔清單外有 internal 改動」守門，混淆此情境要驗的重點。
BOOTSTRAP_SHA=$(cd "$WORK_ROOT/clone" && git log origin/develop --grep='^upstream-sync: ' -1 --format=%H)
(
  cd "$WORK_ROOT/clone"
  git checkout -q develop
  git merge -q -s ours test/mine -m "違規：把 in-place 測試同步 merge 進 develop（模擬）"
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
  echo "ok: ⑩ 測試 commit 永不成錨（對抗性，in-place 版本）"
else
  echo "FAIL: ⑩ 測試 commit 永不成錨 —— selected=$SELECTED_SHA bootstrap=$BOOTSTRAP_SHA"
  FAILURES=$((FAILURES + 1))
fi

# 情境 ⑪：未知的上游 ref（單參數形式，站在使用者自建的 test/* branch 上）——mode
# 判定在 fetch／驗證 ref 存在之前就會放行 test/* branch，中止原因只會是 ref 不存在。
setup
(cd "$WORK_ROOT/clone" && git checkout -qb test/mine)
expect_abort "⑪ 未知的上游 ref" bash scripts/sync-upstream.sh gl/no-such-branch

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

# 情境 ⑬：IN-PLACE 首次同步——使用者站在自建的 test/* branch 上（而非 $MAIN_BRANCH）跑
# 測試模式，MUST 就地疊一顆快照 commit，不新切 test/upstream-<sha> branch。
setup
(
  cd "$WORK_ROOT/seed"
  git checkout -qb feat/x
  echo "in-place marker 1" > feature-marker-1.txt
  git add -A && git commit -qm "上游 feature 分支新檔 #1"
  git push -q origin HEAD:feat/x
)
(cd "$WORK_ROOT/clone" && git checkout -qb test/mine)
(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh develop gl/feat/x >/dev/null 2>&1)
CURRENT_BRANCH_13=$(cd "$WORK_ROOT/clone" && git rev-parse --abbrev-ref HEAD)
SUBJECT_13=$(cd "$WORK_ROOT/clone" && git log -1 --format=%s)
HAS_MARKER_13=$(cd "$WORK_ROOT/clone" && git ls-files | grep -c '^feature-marker-1.txt$' || true)
OWNED_CONTENT_13=$(cd "$WORK_ROOT/clone" && cat internal/README.md 2>/dev/null || true)
NO_NEW_TEST_BRANCH_13=1
if (cd "$WORK_ROOT/clone" && git branch --list 'test/upstream-*') | grep -q .; then
  NO_NEW_TEST_BRANCH_13=0
fi
REMOTE_HAS_MINE_13=0
(cd "$WORK_ROOT/clone" && git ls-remote --exit-code origin test/mine >/dev/null 2>&1) && REMOTE_HAS_MINE_13=1
if [ "$CURRENT_BRANCH_13" = "test/mine" ] && [[ "$SUBJECT_13" == test-sync:\ * ]] \
  && [ "$HAS_MARKER_13" = "1" ] && [ "$OWNED_CONTENT_13" = "internal owned" ] \
  && [ "$NO_NEW_TEST_BRANCH_13" = "1" ] && [ "$REMOTE_HAS_MINE_13" = "1" ]; then
  echo "ok: ⑬ in-place 首次同步"
else
  echo "FAIL: ⑬ in-place 首次同步 —— current=[$CURRENT_BRANCH_13] subject=[$SUBJECT_13] marker=[$HAS_MARKER_13] owned=[$OWNED_CONTENT_13] no_new_test_branch=[$NO_NEW_TEST_BRANCH_13] remote=[$REMOTE_HAS_MINE_13]"
  FAILURES=$((FAILURES + 1))
fi

# 情境 ⑭：重複 in-place 同步疊 commit＋develop 錨點不受影響——接續⑬，上游 feature branch
# 再推一顆 commit，同一條 test/mine 上重跑，確認疊出第二顆 test-sync commit，且 develop
# 基準仍是 bootstrap（比照⑩的對抗性檢查手法：in-place 快照 commit 不該擾動主線錨點）。
BOOTSTRAP_SHA_14=$(cd "$WORK_ROOT/clone" && git log origin/develop --grep='^upstream-sync: ' -1 --format=%H)
(
  cd "$WORK_ROOT/seed"
  git checkout -q feat/x
  echo "in-place marker 2" > feature-marker-2.txt
  git add -A && git commit -qm "上游 feature 分支新檔 #2"
  git push -q origin HEAD:feat/x
)
(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh develop gl/feat/x >/dev/null 2>&1)
TEST_SYNC_COUNT_14=$(cd "$WORK_ROOT/clone" && git log --oneline | grep -c '^[a-f0-9]* test-sync:')
HAS_MARKER2_14=$(cd "$WORK_ROOT/clone" && git ls-files | grep -c '^feature-marker-2.txt$' || true)
ANCHOR_AFTER_14=$(cd "$WORK_ROOT/clone" && git fetch -q origin \
  && git log origin/develop --grep='^upstream-sync: ' -1 --format=%H)
if [ "$TEST_SYNC_COUNT_14" = "2" ] && [ "$HAS_MARKER2_14" = "1" ] \
  && [ "$ANCHOR_AFTER_14" = "$BOOTSTRAP_SHA_14" ]; then
  echo "ok: ⑭ 重複 in-place 同步疊 commit＋錨點不受影響"
else
  echo "FAIL: ⑭ 重複 in-place 同步疊 commit＋錨點不受影響 —— count=[$TEST_SYNC_COUNT_14] marker2=[$HAS_MARKER2_14] anchor=[$ANCHOR_AFTER_14] bootstrap=[$BOOTSTRAP_SHA_14]"
  FAILURES=$((FAILURES + 1))
fi

# 情境 ⑮：非 test/* branch 拒跑——測試模式下站在既非 $MAIN_BRANCH 也非 test/* 的其他
# branch 上，一律中止。用有效的上游 feature ref（比照⑨用 gl/feat/x）而非不存在的
# ref：若用不存在的 ref，就算把分支守門那個 `*)` arm 拿掉，腳本仍會在稍後的
# 「找不到 ${UPSTREAM_REF}」步驟中止，看起來像是守門生效、實則沒測到它。改用有效
# ref 後才會真的測到這道守門：拿掉 `*)` arm，guard-skip 現在直接讀 TEST_MODE（不再
# 經一層獨立的 IN_PLACE 判定），會讓腳本把 feature/other 當成合法測試路線繼續跑到
# push——變成「不該中止卻成功了」，比誤判成別道守門更隱蔽。因此不用 expect_abort
# （它只驗 exit code），改自行捕捉 stderr，同時斷言 non-zero exit 與分支守門訊息
# 本文，才能真正鎖定這道守門。
setup
(
  cd "$WORK_ROOT/seed"
  git checkout -qb feat/x
  echo "feature marker" > feature-marker.txt
  git add -A && git commit -qm "上游 feature 分支新檔"
  git push -q origin HEAD:feat/x
)
(cd "$WORK_ROOT/clone" && git checkout -qb feature/other)
STDERR_15=$(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh gl/feat/x 2>&1 >/dev/null)
EXIT_15=$?
if [ "$EXIT_15" -ne 0 ] && grep -q "防止把其他 branch 整棵樹替換掉" <<<"$STDERR_15"; then
  echo "ok: ⑮ 非 test/* branch 拒跑（斷言分支守門訊息）"
else
  echo "FAIL: ⑮ 非 test/* branch 拒跑 —— exit=[$EXIT_15] stderr=[$STDERR_15]"
  FAILURES=$((FAILURES + 1))
fi

# 情境 ⑯：站在 $MAIN_BRANCH 上帶測試 ref → 拒跑並指路 in-place——disposable 的
# test/upstream-<sha> 一次性 branch 路徑已移除，這個組合不再產生新 branch，而是
# 直接中止並指路正確用法。ref 不需要真的存在——mode 判定在 fetch／驗證 ref 之前
# 就會中止，用 gl/feat/x 純粹比照其他情境的命名習慣。比照⑮的技巧：自行捕捉
# stderr，同時斷言 non-zero exit 與新指路訊息本文。
setup
STDERR_16=$(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh gl/feat/x 2>&1 >/dev/null)
EXIT_16=$?
if [ "$EXIT_16" -ne 0 ] && grep -q "測試同步只支援 in-place" <<<"$STDERR_16"; then
  echo "ok: ⑯ 站在 \$MAIN_BRANCH 上帶測試 ref → 拒跑並指路 in-place"
else
  echo "FAIL: ⑯ 站在 \$MAIN_BRANCH 上帶測試 ref → 拒跑並指路 in-place —— exit=[$EXIT_16] stderr=[$STDERR_16]"
  FAILURES=$((FAILURES + 1))
fi

echo "---"
if [ "$FAILURES" -gt 0 ]; then echo "$FAILURES 項失敗"; exit 1; fi
echo "全部通過"

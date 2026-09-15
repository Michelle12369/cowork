#!/usr/bin/env bash
# 單向同步：把上游（GitHub 經 internal GitLab 鏡像）整棵樹取代進來，再還原 internal 獨佔路徑。
# 正式模式產出一條 sync/upstream-<sha> branch 供人工適配後發 PR，NEVER 直接推主線。
# 測試模式站在使用者自建的 test/* branch 上就地疊快照，那條 branch 就是它的主線，NEVER merge 回正式主線。
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# 用法（主線＝目前所在 branch）：
#   --official <gl/上游ref> <GitHub sha>   正式同步；站在正式主線（develop、9E）上執行。兩個
#                                          引數順序固定，第一個 MUST 以 gl/ 開頭、第二個
#                                          MUST 是 7-40 位 hex（GitHub 上該 commit 的 sha）
#   --test <gl/上游ref>                    測試同步；先自己從正式主線 checkout 一條 test/*
#                                          再站在上面執行，那條 test/* 就是這次的主線：
#                                          清單、還原、基準點全部從它讀，快照就地疊上去
# 其他寫法（不帶旗標、旗標打錯、參數數量不對）一律印用法並以非零碼結束。
# NEVER 直接改本腳本字面值——本腳本是上游檔,同步會把修改蓋回預設,下一次執行就拒跑。
usage() {
  cat >&2 <<'USAGE'
用法：
  sync-upstream.sh --official <gl/上游ref> <GitHub sha>   # 站在正式主線上
  sync-upstream.sh --test <gl/上游ref>                    # 站在自建的 test/* 上
USAGE
}

if [ "$#" -lt 1 ]; then
  usage
  exit 1
fi

MODE="$1"
shift

case "$MODE" in
  --official)
    if [ "$#" -ne 2 ]; then
      usage
      exit 1
    fi
    UPSTREAM_REF="$1"
    UPSTREAM_SHA_INPUT="$2"
    case "$UPSTREAM_REF" in
      gl/*) ;;
      *)
        usage
        exit 1
        ;;
    esac
    if ! [[ "$UPSTREAM_SHA_INPUT" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
      usage
      exit 1
    fi
    TEST_MODE=0
    ;;
  --test)
    if [ "$#" -ne 1 ]; then
      usage
      exit 1
    fi
    case "$1" in
      gl/*) ;;
      *)
        usage
        exit 1
        ;;
    esac
    UPSTREAM_REF="$1"
    TEST_MODE=1
    ;;
  *)
    usage
    exit 1
    ;;
esac

# 兩種模式的主線都是目前所在 branch。
MAIN_BRANCH=$(git rev-parse --abbrev-ref HEAD)

# --multiple 讓每個引數各自當一個 remote 抓；沒有它 `gl origin` 會被解成 gl 底下的 refspec。
git fetch -q --multiple gl origin

# 主線 branch 本身的檢查。
if [ "$MAIN_BRANCH" = "HEAD" ]; then
  echo "目前是 detached HEAD，同步 MUST 站在具名 branch 上執行。" >&2
  exit 1
fi
if [ "$TEST_MODE" = "1" ]; then
  # 測試模式的主線就是目前站的 test/* branch；清單、還原、基準點都從本機這條 branch 讀，
  # 不要求它已經推上 origin（第一次跑完腳本會推）。
  case "$MAIN_BRANCH" in
    test/*) ;;
    *)
      echo "測試模式 MUST 站在自建的 test/* branch 上執行（先從正式主線 checkout 一條），防止把其他 branch 整棵樹替換掉。" >&2
      exit 1
      ;;
  esac
  MAIN_SOURCE="$MAIN_BRANCH"
else
  # 正式模式站在正式主線上；清單與基準點從 origin/<主線> 讀，還原從本機讀。
  case "$MAIN_BRANCH" in
    test/*|sync/*)
      echo "目前站在 ${MAIN_BRANCH} 上，正式同步不能站在 test/* 或 sync/* branch 上執行。" >&2
      exit 1
      ;;
  esac
  if ! git rev-parse --verify -q "origin/${MAIN_BRANCH}" >/dev/null; then
    echo "origin/${MAIN_BRANCH} 不存在，確認目前 branch 已推上 origin。" >&2
    exit 1
  fi
  MAIN_SOURCE="origin/${MAIN_BRANCH}"
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "worktree 不乾淨；read-tree --reset 會吃掉未提交的修改，先 commit 或 stash。" >&2
  exit 1
fi

if ! git rev-parse --verify -q "${UPSTREAM_REF}^{commit}" >/dev/null; then
  echo "找不到 ${UPSTREAM_REF}——GitLab 鏡像可能未帶 feature branches，檢查鏡像設定或手動推入。" >&2
  exit 1
fi

# 官方模式三道 sha 守門：GitLab 鏡像不是純鏡像（每條 gl/* branch 上會多出 GitHub 沒有的
# commit），錨點與快照樹改從人工帶入的 GitHub sha 拿，GitLab 多出來的東西完全不進
# internal。三道都通過才把 UPSTREAM/UPSTREAM_SHORT 定案。
if [ "$TEST_MODE" = "0" ]; then
  if ! git rev-parse --verify -q "${UPSTREAM_SHA_INPUT}^{commit}" >/dev/null; then
    echo "找不到 ${UPSTREAM_SHA_INPUT} 這個 commit：可能 ${UPSTREAM_REF} 尚未包含它，或短碼有歧義（改帶完整 sha）。" >&2
    exit 1
  fi
  if ! git merge-base --is-ancestor "$UPSTREAM_SHA_INPUT" "$UPSTREAM_REF"; then
    echo "${UPSTREAM_REF} 不包含這個 GitHub commit（${UPSTREAM_SHA_INPUT}）。" >&2
    exit 1
  fi
  # --no-renames: 改名要拆成刪除加新增, 不然會被當成純新增放行.
  if ! git diff --no-renames --diff-filter=MD --quiet "$UPSTREAM_SHA_INPUT" "$UPSTREAM_REF"; then
    echo "GitLab 端相對這個 GitHub commit 修改或刪除了檔案，或帶的 sha 太舊：" >&2
    git diff --no-renames --name-status --diff-filter=MD "$UPSTREAM_SHA_INPUT" "$UPSTREAM_REF" >&2
    exit 1
  fi
  UPSTREAM=$(git rev-parse "$UPSTREAM_SHA_INPUT")
  UPSTREAM_SHORT=$(git rev-parse --short "$UPSTREAM_SHA_INPUT")
else
  # 測試模式直接拿 gl/ref 整包（含 GitLab 多出來的 commit），不留正式錨點。
  UPSTREAM=$(git rev-parse "$UPSTREAM_REF")
  UPSTREAM_SHORT=$(git rev-parse --short "$UPSTREAM_REF")
fi

# 兩份清單共用的行清理：去尾端 \r、去頭尾空白、跳過空行與 # 開頭註解——CRLF 或
# 人工加的說明行不該讓路徑對不到，owned／manual-merge 清單共用同一段邏輯。
clean_list() {
  sed -e 's/\r$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^$/d' -e '/^#/d'
}

# 清單權威來源＝主線 branch ref（正式：origin/<主線>；測試：本機 test/*），NEVER 讀工作樹。
# 清單檔本身也在清單裡（internal 側可以直接在主線上改清單，見 docs/internal-sync.md
# 第 6 節），還原與守門排除範圍共用同一份清單，避免兩者失同步而漏守或誤報。
OWNED_LIST_CONTENT=$(git show "${MAIN_SOURCE}:scripts/internal-owned-paths.txt" 2>/dev/null) || {
  echo "找不到 ${MAIN_SOURCE}:scripts/internal-owned-paths.txt——主線缺少獨佔清單。" >&2
  exit 1
}
OWNED=(); EXCLUDES=()
while read -r ownedPath; do
  OWNED+=("$ownedPath"); EXCLUDES+=(":(exclude)$ownedPath")
done < <(clean_list <<< "$OWNED_LIST_CONTENT")

# pre-flight：owned 路徑在還原來源（$MAIN_SOURCE）上不存在，會讓還原在 read-tree
# 已經把樹換掉一半之後才失敗；這裡先逐條驗過，一個不通全部列出並拒跑，樹不動。
MISSING_OWNED=()
for ownedPath in "${OWNED[@]}"; do
  if ! git cat-file -e "${MAIN_SOURCE}:${ownedPath%/}" 2>/dev/null; then
    MISSING_OWNED+=("$ownedPath")
  fi
done
if [ "${#MISSING_OWNED[@]}" -gt 0 ]; then
  echo "清單裡的路徑在主線上不存在，樹一個檔都沒動：" >&2
  printf '%s\n' "${MISSING_OWNED[@]}" >&2
  exit 1
fi

# 錨點＝主線上最後一顆已落地的正式同步 commit（upstream-sync:）；用 commit 而非 tag，因為
# tag 可能隨分支移動，指向從未真正落地的狀態。測試模式的 test/* 從正式主線切出來，會繼承
# 這顆錨點，雙邊擁有檔比對就用它。
LAST_SYNC=$(git log "$MAIN_SOURCE" --grep='^upstream-sync: ' -1 --format=%H || true)
if [ -z "$LAST_SYNC" ]; then
  echo "找不到基準同步 commit。首次同步 MUST 先人工 bootstrap（見 docs/internal-sync.md）。" >&2
  exit 1
fi
# 守門基準：正式模式就是正式錨點（test-sync: 誤入主線時，拿它當基準會把污染遮掉）；
# 測試模式的 test/* 疊過快照後樹本來就含上游內容，拿正式錨點比會把上游改動誤判成
# internal 改動，所以改以最近一顆 test-sync: 或 upstream-sync: 為準。
if [ "$TEST_MODE" = "1" ]; then
  GATE_BASE=$(git log "$MAIN_SOURCE" --grep='^upstream-sync: ' --grep='^test-sync: ' -1 --format=%H)
else
  GATE_BASE="$LAST_SYNC"
fi
LAST_UPSTREAM=$(git log -1 --format=%B "$LAST_SYNC" | sed -n 's/^Upstream-Commit: //p')
if [ -z "$LAST_UPSTREAM" ]; then
  echo "基準 commit $LAST_SYNC 缺少 Upstream-Commit trailer——同步 PR 被 squash 了？" >&2
  exit 1
fi

# 正式同步的錨點鏈 MUST 單調前進：上一個錨點必須是本次同步目標（GitHub sha）的祖先。
# 不是＝錨點被污染（測試/feature 同步誤入主線）或上游 force-push，先人工修錨再同步。
if [ "$TEST_MODE" = "0" ] && ! git merge-base --is-ancestor "$LAST_UPSTREAM" "$UPSTREAM"; then
  echo "基準錨點 ${LAST_UPSTREAM} 不是 ${UPSTREAM} 的祖先——錨點鏈回退或被污染，MUST 人工修復。" >&2
  exit 1
fi

# 前置守門——全部 MUST 通過，NEVER 為了讓同步跑完而跳過。兩種模式共用。
# 獨佔清單外有 internal 改動＝同步後會無聲抹掉；測試模式一樣要擋：test/* 上手工改的
# 非 owned 檔會被下一次快照蓋掉，清單有問題（例如換行格式）也在這裡先被抓到，樹一個檔都不動。
if [ -n "$(git diff --name-only "$GATE_BASE" "$MAIN_BRANCH" -- . "${EXCLUDES[@]}")" ]; then
  echo "獨佔清單外有 internal 改動，同步會無聲抹掉它們：" >&2
  git diff --name-only "$GATE_BASE" "$MAIN_BRANCH" -- . "${EXCLUDES[@]}" >&2
  exit 1
fi
if [ -n "$(git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}")" ]; then
  echo "有野生 untracked 檔，git add -A 會把它們永久收編成同步 commit 的一部分：" >&2
  git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}" >&2
  exit 1
fi

# 雙邊擁有檔：列出上游動過的交給人工調和。錨點 MUST 是 $LAST_UPSTREAM，不是 $LAST_SYNC
# ——後者是 internal 版，拿它比上游永遠有差、每次都誤報。
MANUAL_NOTES=""
while read -r mergePath; do
  if ! git diff --quiet "$LAST_UPSTREAM" "$UPSTREAM" -- "$mergePath"; then
    MANUAL_NOTES="${MANUAL_NOTES}需人工調和：${mergePath}"$'\n'
  fi
done < <(clean_list < scripts/manual-merge-paths.txt)

if [ "$TEST_MODE" = "1" ]; then
  # 測試模式雙重隔離：test-sync: 前綴、trailer 換名——就算被違規 squash 進主線，
  # 基準 grep 與 trailer 解析也都讀不到它。
  COMMIT_PREFIX="test-sync"
  TRAILER_NAME="Test-Upstream-Commit"
  COMMIT_SUBJECT="${COMMIT_PREFIX}: 同步至 ${UPSTREAM_SHORT}（${UPSTREAM_REF}）"
else
  SYNC_BRANCH="sync/upstream-${UPSTREAM_SHORT}"
  COMMIT_PREFIX="upstream-sync"
  TRAILER_NAME="Upstream-Commit"
  # sha 是人工輸入、認不出是哪條上游 ref，subject 附加 gl/ref 方便辨認。
  COMMIT_SUBJECT="${COMMIT_PREFIX}: 同步至 ${UPSTREAM_SHORT}（經 ${UPSTREAM_REF}）"
fi

if [ "$TEST_MODE" = "1" ]; then
  # in-place：站在使用者自建的 test/* 上疊一顆快照 commit，branch 不變。
  git read-tree -u --reset "$UPSTREAM_REF"                    # 整棵樹換成指定上游 ref，含其刪除
  # 還原＝先刪後取，owned 路徑嚴格等於這條 branch 疊快照前的版本——單純 checkout 是聯集，
  # 上游新增檔會殘留。來源＝本機這條 test/*（read-tree 前的 HEAD），跟清單同源。
  for ownedPath in "${OWNED[@]}"; do
    git rm -rfq --ignore-unmatch -- "$ownedPath"
    git checkout "$MAIN_BRANCH" -- "$ownedPath"
  done
  git add -A
  # --allow-empty：擁有路徑還原後淨變更常是零，但此 commit MUST 落地。
  git commit -q --allow-empty -m "${COMMIT_SUBJECT}" \
    -m "${MANUAL_NOTES}" -m "${TRAILER_NAME}: ${UPSTREAM}"
  git push -q -u origin "$MAIN_BRANCH"
else
  git checkout -qb "$SYNC_BRANCH"
  git read-tree -u --reset "$UPSTREAM"            # 整棵樹換成 GitHub sha（不是 gl/ref），含其刪除
  # 還原＝先刪後取，owned 路徑嚴格等於主線版本——單純 checkout 是聯集，上游新增檔會殘留。
  # 相對切出點淨變更為零。
  for ownedPath in "${OWNED[@]}"; do
    git rm -rfq --ignore-unmatch -- "$ownedPath"
    git checkout "$MAIN_BRANCH" -- "$ownedPath"   # 官方模式清單讀 origin、還原讀本機——分歧方向由守門（EXCLUDES 同源 origin）fail-close 擋下
  done
  git add -A
  # --allow-empty：雙邊擁有檔還原後淨變更常是零，但此 commit MUST 落地——它是下次同步的
  # 基準點，也是 MANUAL_NOTES 待辦的唯一落地處。
  git commit -q --allow-empty -m "${COMMIT_SUBJECT}" \
    -m "${MANUAL_NOTES}" -m "${TRAILER_NAME}: ${UPSTREAM}"
  git push -q -u origin "$SYNC_BRANCH"
fi

if [ "$TEST_MODE" = "1" ]; then
  echo "已在 ${MAIN_BRANCH} 上就地疊一顆快照 commit（來源 ${UPSTREAM_REF}），branch 未變。"
  echo "  本模式整棵樹替換——此 branch 上非 owned 路徑的手工改動下次會被守門擋下；internal 接縫改動請進正式主線的獨佔路徑再重切 test/*。"
  echo "  NEVER merge 進正式主線——測完即刪（本地與 origin 都刪）。"
else
  echo "已推出 ${SYNC_BRANCH}。接著人工完成："
  echo "  1. 檢視 diff，確認上游改動"
  echo "  2. 調和 commit body 列出的雙邊擁有檔"
  echo "  3. 接縫適配（上游若改了 AgentRuntime 等介面，internal 實作要跟著改）"
  echo "  4. 發 PR 進 ${MAIN_BRANCH}，CI 綠燈後合併（MUST NOT squash）"
fi

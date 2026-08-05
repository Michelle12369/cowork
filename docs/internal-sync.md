# Internal 環境同步流程

同一份程式碼同時活在 GitHub（家裡，權威）與 internal 環境的內網。兩邊的連通是**單向**的：
GitHub 經 internal GitLab 鏡像流入，internal 側的 commit 永遠推不回 GitHub。策略不是「把
衝突解好」，而是讓重疊面積為零——每個檔案只有一側能寫，任何「兩邊都想改」的地方
都先在家裡開一個接縫。本文件是這條同步流程的權威說明；設計脈絡見
`docs/superpowers/specs/2026-08-03-internal-env-seams-design.md`。

---

## 1. 拓撲

```
① 家裡 push ─────────▶ GitHub master              （家裡，權威）
                              │ 自動鏡像
② 　　　　　　　　　　  ▼
                       internal GitLab  gl/master     （唯讀上游）
                              │
③ 有人跑 sync-upstream.sh ────┤  ← 唯一的人工動作
                              ▼
④                      Azure 工作 repo  develop     （internal 主線，可推）
```

四步裡只有第三步需要人；`scripts/sync-upstream.sh` 在 **Azure 工作 repo 的 clone**
裡執行，remote 設定：

```bash
origin  https://dev.azure.com/.../cowork      # internal 工作 repo，可推
gl      https://gitlab.<internal>/.../cowork      # GitHub 鏡像，只讀
```

「上游（upstream）」指 GitHub 那份，經 GitLab 鏡像被 internal 消費——唯讀、不可修改、
只能整批接收。文件、腳本、commit 訊息一律用 upstream 一詞，不用 vendor（vendor
branch 是同一套做法的業界術語，但這裡的上游是自家程式碼，用 vendor 易誤導）。

---

## 2. 首次 bootstrap

`scripts/internal-owned-paths.txt` 列出的 internal 獨佔路徑在剛建立時，`develop` 上還
不存在——同步腳本靠 `git checkout develop -- <path>` 還原這些檔案，若它們從未被
commit 過，這一步會直接失敗。因此**首次同步 MUST 先由 internal 側把各獨佔檔案 commit 到
`develop`**，之後才可能有東西可還原。

獨佔檔就緒後，人工建立第一顆同步 commit 作為之後所有同步的基準點：

```bash
git commit --allow-empty -m "upstream-sync: bootstrap" \
  -m "Upstream-Commit: $(git rev-parse gl/master)"
git push -u origin develop
```

`Upstream-Commit:` trailer 是腳本找基準點的唯一依據——之後每次同步都從
`origin/develop` 的歷史找最後一顆 `^upstream-sync: ` commit，讀出它的
`Upstream-Commit:` 作為「上次同步到哪」。

---

## 3. 每次同步

在**專用 clone 或 worktree**（不與任何人的工作區共用）的 `develop` 上執行：

```bash
bash scripts/sync-upstream.sh
```

腳本會做 replace-then-restore：`git read-tree -u --reset gl/master` 把整棵樹換成
上游（含上游的刪除），再用獨佔路徑清單把 internal 檔案撈回來，最後在一條新切出的
`sync/upstream-<shorthash>` branch 上落一顆 commit 並推上 `origin`。**腳本
NEVER 直接推 `develop`**——落地永遠是 feature branch → 人工確認與適配 → PR。

腳本結束後，人在 `sync/upstream-<shorthash>` branch 上完成：

1. **檢視 diff**，確認上游改動的範圍與內容
2. **調和雙邊擁有檔**——commit body 裡標了「需人工調和」的路徑（目前是
   `backend/pom.xml`、`backend/src/main/resources/application.properties`、
   `frontend/index.html`）需要人工比對上游版本與 internal 版本，決定怎麼合併
3. **接縫適配**——上游若改動了接縫介面（例如 `AgentRuntime` 增加方法），
   `internal_runtime.py` 等 internal 獨佔實作 MUST 在**同一個 PR**裡跟著改，否則
   `develop` 會從同步落地那刻起壞掉，直到有人補救為止
4. **發 PR 進 `develop`**，internal CI 綠燈後合併

腳本執行前會做四道前置守門（在 `develop` 上、worktree 乾淨、獨佔清單外沒有 internal
改動、沒有野生 untracked 檔），任何一道不過就中止，不會往下跑。這是整個流程唯一
的安全裝置，**NEVER 為了讓同步跑完而跳過它們**——若真的擋到你，先解決守門指出的
問題（多半是清單漏列了新的 internal 獨佔檔），而不是繞過檢查。

---

## 4. 硬規則

- 同步 PR **MUST NOT squash 合併**：squash 會丟掉 commit 上的 `Upstream-Commit:`
  trailer，下一次同步就找不到基準點。這條規則 MUST 寫進 internal 側的 PR 流程說明。
- `git remote set-url --push gl no_push`，從物理上擋掉誤推鏡像——`gl` 是唯讀
  上游，不該有人往它推東西。
- **GitLab MUST 是真鏡像（`--mirror`），不是重新匯入**。同步 commit 訊息記的是
  `gl/master` 的 short hash，整個流程的稽核能力全靠它——若鏡像是真 mirror，
  SHA 與 GitHub 完全相同，可直接拿去 GitHub 對照；若改成重新匯入、squash 或
  重打包，SHA 會全部變成 GitLab 自己的，對照能力當場歸零，而且不會有任何錯誤
  訊息。鏡像設定變更 MUST 視為破壞性變更。
- 基準點用 **commit 而非 tag**：PR 可能被放棄或擱置，推分支時就移動的 tag 會
  指向從未落地的狀態；改從 `origin/develop` 的歷史找最後一顆同步 commit，基準
  因此只反映真正合併進 `develop` 的同步。

---

## 5. 四類檔案與兩份清單

| 類別 | 誰能寫 | 同步時 | 例子 |
|---|---|---|---|
| **共用權威檔** | 只有家裡 | 整檔取代 | `pyproject.toml`、`uv.lock`、`requirements.txt`、`main.tsx`、`app/agent/**`（`runtime/internal_runtime.py` 除外）、`.env.example` |
| **internal 獨佔檔** | 只有 internal | 取代後還原 | `backend/src/internal/**`、`internal.impl.ts`、`internal_runtime.py` |
| **雙邊擁有檔** | 兩邊都寫 | 還原＋偵測上游變更後人工調和 | `backend/pom.xml`、`backend/src/main/resources/application.properties`、`frontend/index.html` |
| **不在 repo 內** | 各自 | 不受影響 | `.env`（gitignored）、`~/.m2/settings.xml` |

兩份清單都在 `scripts/`，是還原與守門共用的唯一事實來源：

- **`scripts/internal-owned-paths.txt`**——internal 獨佔路徑，同步時先被上游整棵樹
  蓋掉、再從 `develop` 撈回來；也是守門檢查「獨佔清單外有沒有 internal 改動」的排除
  範圍。**新增 internal 獨佔檔時 MUST 同步更新這份清單**，否則下次同步會把它當成
  「越界改動」擋下，或者更糟——清單沒列到但也沒被上游覆蓋的檔案不會出現在守門
  裡，但一旦上游剛好新增同名路徑，該檔案會被無聲蓋掉。
- **`scripts/manual-merge-paths.txt`**——雙邊擁有檔，內容 MUST 是上面清單的
  子集：先被還原保住 internal 版，再由上游變更偵測攔下需要人工調和的情況（同步時若
  上游也動過該路徑，commit body 會多一行「需人工調和：<path>」）。

`uv.lock` 不在清單內——internal 走 `requirements.txt`，不讀 lock；`requirements.txt`
漂移由 `deepagent-service/tests/test_requirements_sync.py` 在家裡攔截，避免忘記
重新匯出（`uv export --no-dev --no-hashes --format requirements-txt -o
requirements.txt`）而讓 internal 裝到舊依賴。`.env` 也不在清單內，它兩側皆 gitignored、
不在 index，`read-tree` 不會碰它。

家裡側的配合：這些獨佔路徑 **NEVER 加進家裡的 `.gitignore`**。internal 側必須能把獨佔檔
commit 到 `develop`，同步時 `git checkout develop -- <path>` 才有東西可還原；若
家裡把這些路徑寫進 `.gitignore`，該 `.gitignore` 會隨同步傳進 internal 側，使這些檔案變成
被忽略，internal 端得靠 `git add -f` 才追蹤得到，是一個沒必要的陷阱。

---

## 6. 守門的限制

守門的觀察範圍僅限 `develop`：若 internal 側的越界改動還躺在未合併的 feature branch 上，
本次同步看不到，要等它 merge 進 `develop` 之後才會在**下一次**同步被攔下。這不是
漏洞（最終仍會被抓到），但延遲是真的——攔下的時間點可能離犯錯的當下很遠。

因此 internal 側的 code review MUST 一併把關「共用檔不得修改」，不能只依賴同步時的
守門作為唯一防線。

---

## 相關檔案

- `scripts/sync-upstream.sh` — 同步腳本本體（internal 側執行、家裡維護）
- `scripts/internal-owned-paths.txt` / `scripts/manual-merge-paths.txt` — 兩份清單
- `scripts/test-sync-upstream.sh` — 守門行為的自動化驗證，在拋棄式 git repo 上跑
  七個情境，`bash scripts/test-sync-upstream.sh` 即可執行
- `deepagent-service/app/agent/runtime/base.py` — `AgentRuntime` 接縫（本流程要
  搬運的主體）

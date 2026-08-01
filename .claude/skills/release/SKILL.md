---
name: release
description: Push master 與目前 branch 到 GitHub、重啟 docker compose、回報 localhost 與 TryCloudflare URL。使用者輸入 /release 時執行。
---

# Release

固定流程，依序執行（任一步失敗即停止並回報）：

## 1. Push 到 GitHub

```bash
cd "$(git rev-parse --show-toplevel)"
# remote 不存在時先建立 private repo（一次性）
git remote get-url origin 2>/dev/null || gh repo create erd-cowork --private --source . --remote origin
BRANCH=$(git branch --show-current)
git push -u origin master
[ "$BRANCH" != "master" ] && git push -u origin "$BRANCH"
```

- 工作區有未 commit 變更時：先問使用者要不要一起 commit，不要自行決定
- NEVER force push

## 2. 重啟 docker compose

```bash
docker compose down
DOCKER_CONFIG=$(mktemp -d) docker compose up -d --build   # 避開 docker-credential-desktop 懸掛
```

- `.env` 會被 compose 自動讀取（ANTHROPIC_API_KEY 等）；不要把 .env 內容印出來
- 等待 backend healthy：`curl -sf localhost:${BACKEND_PORT:-8080}/actuator/health`（從 .env 讀 BACKEND_PORT；Oracle 首次啟動可能 2-4 分鐘，輪詢至多 5 分鐘）

## 3. 回報 URL

```bash
docker compose logs tunnel-frontend 2>&1 | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1
docker compose logs tunnel-backend 2>&1 | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1
```

最後用表格回報：
- Frontend local: http://localhost:3000
- Frontend tunnel: （上面撈到的 URL）
- Backend tunnel: （同上；Swagger 在 `/swagger-ui.html`）
- 提醒：TryCloudflare quick tunnel URL 每次重啟都會變

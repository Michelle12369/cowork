# eRD Cowork — Frontend

React + TypeScript + Vite 前端，提供 AI 協作式 Data Studio 介面。

## 前置需求

| 要裝 | 版本 | 說明 |
|---|---|---|
| Node.js | **22** | 對齊 `Dockerfile` 的 `node:22-alpine` |
| npm | 隨 Node 附帶（v10+） | — |

repo 沒有 `.nvmrc`／`package.json` 的 `engines` 釘選，**22 是以 Dockerfile 為準**。前端用 Vite 8 + TypeScript 6 + Vitest 4，Node 太舊會直接起不來。驗證：`node -v` → `v22.x`。

## 本機開發（localhost，建議）

    npm install     # 首次
    npm run dev

- http://localhost:3000（`vite.config.ts` 寫死 `port: 3000` + `strictPort: true`，埠被佔用時**直接失敗**而非跳號）
- `/api` 自動 proxy 至 `http://localhost:8080`（`vite.config.ts` 寫定，不吃環境變數）——**backend 需先啟動**（`cd backend && ./mvnw spring-boot:run`）

## 指令

| 指令 | 說明 |
|------|------|
| `npm run dev` | 開發伺服器（http://localhost:3000，HMR） |
| `npm test` | Vitest 單元測試（jsdom 環境） |
| `npm run lint` | oxlint |
| `npm run build` | `tsc -b` + 產生生產 bundle 至 `dist/` |
| `npm run preview` | 預覽 build 產物（同樣走 :3000） |

## Docker 版本

前端在 docker 裡是 **nginx 服務 build 好的靜態檔**，不是 vite dev server——因此**沒有 HMR，且不會反映未重新 build 的程式碼**。詳見根目錄 [README](../README.md) 第二節。

    docker compose -f ../docker-compose.app.yml up -d --build frontend

- http://localhost:3001 ← **與本機開發的 :3000 不同**
- 改了程式碼一定要 `--build`，否則跑的還是舊 bundle

## /api Proxy

- **本機開發**：`vite.config.ts` 將 `/api` proxy 至 `http://localhost:8080`
- **docker/生產**：`nginx.conf` 將 `/api` 原樣轉發至 `backend:8080`，**不可加尾斜線**，否則路徑 mapping 會錯位

## 技術棧

- React 18 + TypeScript、Ant Design + Ant Design X、Tailwind CSS
- TanStack Query（資料抓取）、React Router、Vitest + Testing Library

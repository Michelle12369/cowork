# eRD Cowork — Frontend

React + TypeScript + Vite 前端，提供 AI 協作式 Data Studio 介面。

## 指令

| 指令 | 說明 |
|------|------|
| `npm run dev` | 啟動開發伺服器（http://localhost:5173），/api 請求自動 proxy 至 localhost:8080 |
| `npm test` | 以 Vitest 執行單元測試（jsdom 環境） |
| `npm run build` | 產生生產 bundle 至 `dist/` |

## /api Proxy

- **開發**：vite.config.ts 將 `/api` proxy 至 `http://localhost:8080`（backend 需先啟動）
- **生產**：nginx.conf 將 `/api` 原樣轉發至 `backend:8080`，**不可加尾斜線**，否則路徑 mapping 會錯位

## 技術棧

- React 18 + TypeScript、Ant Design + Ant Design X、Tailwind CSS
- TanStack Query（資料抓取）、React Router、Vitest + Testing Library

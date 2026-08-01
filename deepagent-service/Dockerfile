# builder: quickjs 無 aarch64 wheel、需 gcc 源碼編譯;runtime 維持 slim 不帶編譯鏈
FROM python:3.11-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY --from=builder /app/.venv ./.venv
COPY app ./app
COPY skills ./skills
EXPOSE 8000
# --no-sync: runtime 無編譯鏈,啟動時絕不可觸發隱式重 sync(venv 已在 builder 固定)
CMD ["uv", "run", "--no-sync", "fastapi", "run"]

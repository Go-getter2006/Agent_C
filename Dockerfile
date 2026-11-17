# 一个镜像，三种用法：网页和接口（web）、常驻主循环（daemon）、沙箱（sandbox）。
FROM node:22-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# gcc 只给沙箱用：编译小题代码和大模型写的参考解
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=frontend /src/dist /srv/frontend/dist
ENV FRONTEND_DIST=/srv/frontend/dist DATA_DIR=/srv/data
RUN useradd --system --uid 10001 app && mkdir -p /srv/data /run/sandbox && chown app /srv/data /run/sandbox
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.web.app:app --host 0.0.0.0 --port 8000 --proxy-headers"]

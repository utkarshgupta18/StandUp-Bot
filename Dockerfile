# StandUp Bot: API + built React app in one image.
#   docker compose up --build     (see docker-compose.yml: adds Postgres + pgvector)

# ---- build the React frontend
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- API
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    HF_HOME=/data/huggingface
# git for GitPython; repos are usually mounted from the host with another
# owner, so mark every directory safe for git.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
WORKDIR /srv
# CPU-only torch keeps the image ~2GB smaller than the default CUDA build.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app/ app/
COPY connectors/ connectors/
COPY db/ db/
COPY ingestion/ ingestion/
COPY rag/ rag/
COPY sample_data/ sample_data/
COPY --from=web /web/dist web/dist
RUN useradd --create-home --uid 1000 standup && mkdir -p /data && chown standup /data
USER standup
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

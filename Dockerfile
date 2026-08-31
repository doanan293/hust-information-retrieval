FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir .[dev] \
    && playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 crawler

COPY config ./config
RUN mkdir -p /app/data && chown -R crawler:crawler /app
USER crawler

ENTRYPOINT ["hust-crawl"]
CMD ["validate-config"]


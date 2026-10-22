FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install ".[runtime]" \
    && groupadd --system --gid 10001 messqry \
    && useradd --system --uid 10001 --gid messqry --home-dir /nonexistent --shell /usr/sbin/nologin messqry \
    && mkdir -p /tmp/messqry \
    && chown 10001:10001 /tmp/messqry

VOLUME ["/tmp/messqry"]

ENV TMPDIR=/tmp/messqry

USER 10001:10001
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn messqry.runtime.server:app --host 0.0.0.0 --port \"${MESSQRY_RUNTIME_PORT:-8000}\" --no-server-header"]

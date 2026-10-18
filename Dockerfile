FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY apps/__init__.py ./apps/__init__.py
COPY apps/worker ./apps/worker
RUN uv sync --locked --no-dev

# Run as an unprivileged user; the mounted data directory must be writable by uid 1000.
RUN useradd --uid 1000 --create-home app && mkdir /data && chown app /data
USER app
ENV PATH="/app/.venv/bin:$PATH" DORTRADER_RECORDER__DATA_DIR=/data

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD ["python", "-m", "apps.worker.healthcheck"]

CMD ["python", "-m", "apps.worker.recorder"]

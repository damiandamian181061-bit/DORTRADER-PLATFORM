# DORTRADER platform

BTC/USDC options research platform on Deribit: market data, option pricing, volatility analytics. Paper and research only; nothing here places orders. Plans live in `docs/plans/`.

## Setup

```bash
uv sync                      # Python 3.12 environment
cd apps/web && npm ci        # frontend
cp .env.example .env         # only needed once a database is used
```

## Record market data

```bash
uv run python -m apps.worker.recorder
```

Polls the Deribit public book summary every 30 s and writes Parquet under `DORTRADER_RECORDER__DATA_DIR` (default `data/`). Keep that path short on Windows. Settings are environment variables prefixed `DORTRADER_` (see `src/dortrader/config.py`). Chain history cannot be backfilled, so every hour the recorder is down is lost.

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest --cov --cov-fail-under=95
```

## Regenerating fixtures

```bash
uv run python scripts/capture_deribit_fixtures.py        # live Deribit payloads for tests
uv run python scripts/capture_sub_day_tickers.py         # run the day before an expiry
uv run --group reference python scripts/generate_black76_golden.py   # QuantLib reference values
UPDATE_SNAPSHOTS=1 uv run pytest tests/unit/test_domain.py           # domain JSON Schemas
```

## API and web app

```bash
uv run uvicorn apps.api.main:app --port 8000
cd apps/web && npm run dev
```

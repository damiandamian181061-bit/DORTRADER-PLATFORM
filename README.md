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

Underlying candles and the DVOL index come from a separate one-shot job (add `DORTRADER_BACKFILL__REFRESH_SECONDS` to keep it refreshing):

```bash
uv run python -m apps.worker.backfill
```

To score the volatility forecast baselines on the backfilled history and write `docs/validation/vol-forecast-baselines.md` (about 30 seconds):

```bash
uv run python scripts/evaluate_vol_forecasts.py --data-dir data
```

To run it around the clock in Docker (heartbeat, healthcheck, restart on failure), see `docs/deploy.md`.

## View the chain

```bash
uv run uvicorn apps.api.main:app --port 8000
cd apps/web && npm run dev          # http://localhost:5173
```

The **Option chain** tab shows the recorded chain at any recorded UTC time: bid, ask, mark, our implied vol next to Deribit's, delta, vega and quality flags. The API (`/api/chain`, `/api/instruments`, `/api/status`) reads the same `DORTRADER_RECORDER__DATA_DIR`. The **Volatility** tab charts realized volatility against DVOL, shows each model's current forecast and how well it has done out of sample (it needs the backfill and the evaluation script above). To compare our implied vols with Deribit's over a recorded range:

```bash
uv run python scripts/compare_chain_to_deribit.py --data-dir data --out docs/validation/chain-vs-deribit.md
```

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

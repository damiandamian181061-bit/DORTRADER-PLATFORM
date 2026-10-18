# Core implementation plan: foundation, market data, pricing, volatility analytics

Source: `BTC_USDC_Quant_AI_RL_Platform_Blueprint.pdf` (v1.0, 18 Oct 2026), sections 3, 4, 11, 13 and 15. This plan supersedes `phase-1-foundation.md`.

It covers four workstreams, in this order of dependency:

| ID | Workstream | Blueprint phase |
|---|---|---|
| **F** | Project foundation and infrastructure | P0 |
| **D** | Market data ingestion and storage | P1 |
| **P** | Pricing and Greeks engine | P2 |
| **V** | Volatility analytics and forecasting baselines | P2 (smile, term structure), P6 (EWMA/GARCH baselines) |

Out of scope here: strategy generation, backtester, risk gateway, ML challengers, agents, RL, order entry of any kind. Nothing in this plan touches a private Deribit endpoint or holds a trading credential.

## 0. Where we are

- M0 skeleton exists on `main` and is **uncommitted**: uv project, FastAPI `GET /api/health`, Vite + React + TypeScript + Tailwind page, Supabase migrations folder (placeholder), CI workflow.
- Step 0 of this plan is to commit M0 on a branch (`m0-skeleton`) so everything below builds on a recorded baseline.

## 0.1 Implementation status (updated 2026-10-18)

| Item | Status |
|---|---|
| F1 package restructure, import-linter | Done (5 contracts) |
| F2 domain schemas | Partly: `Instrument`, `OptionQuote`, `QualityFlag`, `Money`, value types. `BookLevel`, `OrderBook`, `ChainSnapshot` wait for D4 and D6 |
| F3 config, logging, clock | Done |
| F4 test harness, CI | Done except the Postgres service container (waits for F5). Markers, network blocked, coverage floor 95%, lock check, import-linter, pip-audit, npm audit, Docker build. CI ran green on Linux |
| D0 spike and fixtures | Done (fixtures, WebSocket probe). Observing the 19 Oct 08:00 UTC expiry not done |
| D1 REST client, catalog, normalizer | Done. Catalog is kept as Parquet snapshots until F5 |
| D2 Parquet archive | Done, including a kill-the-writer crash test |
| D3 Tier 1 recorder | Done and run live, with a heartbeat file, Docker healthcheck, `Dockerfile` and `docker-compose.yml` (`docs/deploy.md`). **Not yet running 24/7**: you still need to provision the host (D-1) |
| P1 Black-76 and Greeks | Done |
| P2 implied vol solver | Done |
| P3 reference validation | Done for QuantLib, properties, finite differences and Deribit replication. The report over recorded chains needs D3 data |
| D4 point-in-time replay | Done: `ChainSnapshot`, shared reducer, `ChainStore.as_of`, equivalence and determinism tests. The materialized 1-minute snapshot grid is deferred (reading the normalized archive directly is fast enough) |
| P4 chain pricing and arbitrage flags | Done: `price_chain`, monotonicity, butterfly and parity flags. Preliminary report in `docs/validation/chain-vs-deribit.md` (about 100 s of data) |
| D9 / P5 chain view | Done for the chain: `GET /api/chain`, `/api/instruments`, `/api/status` and the web chain page. The standalone pricing calculator and the data-quality coverage view are not built |
| F5, F6, D5 to D8, P6, V1 to V7 | Not started |

Deviations from the plan as written:

- The raw archive stores the BTC rows of each response as JSON plus the sha256 of the full response body, not the 1.6 MB original body, which would be about 4.6 GB per day at 30 s.
- Clock offset comes from `get_index_price`, not the book summary (see measurements below).
- The IV solver is vectorized safeguarded Newton with bisection fallback, not Brent. Statuses: `OK`, `INVALID_INPUT`, `BELOW_INTRINSIC`, `AT_INTRINSIC`, `ABOVE_UPPER_BOUND`, `NO_BRACKET`, `NOT_CONVERGED`.
- `config`, `log` and `clock` are single modules, not packages.
- Chain pricing reports the bid-to-ask IV range as the executable range of fair vol. The half-tick interval (`implied_vol_interval`) exists but is not used there, because bid and ask are exact quotes and a mark price has no tick.
- A new `dortrader.replay` package holds the reducer and `ChainStore`, with its own layering contract; the recorder keeps a live chain state with the same reducer.
- The recorder's flush interval default is 60 s, not 300 s, so a hard crash loses at most a minute.

## 1. Facts verified against Deribit on 2026-10-18

I read the official docs and made read-only public calls (no keys). Treat these as the ground truth for design, and re-verify in D0 because the exchange can change.

### Instruments

- `public/get_instruments?currency=USDC&kind=option` returned **3,422** options across 7 underlyings. Filter `base_currency == "BTC"` to get **596** BTC/USDC options.
- Example name: `BTC_USDC-19OCT26-71000-C`. Strikes with decimals are encoded in the name (for example `8d5` for 8.5 on another underlying), so **always read strike and expiry from fields, never parse the name**.
- Every BTC/USDC option has: `instrument_type = linear`, `quote_currency = counter_currency = settlement_currency = USDC`, `contract_size = 1.0` (BTC), `min_trade_amount = 0.01` (BTC), `tick_size = 5.0` USDC (20 USDC above a 1,000 USDC premium, via `tick_size_steps`), `price_index = btc_usdc`, commission 0.0003.
- Expiry is **08:00 UTC**. The live chain had 8 expiries: five dailies (19 to 23 Oct), one weekly (30 Oct), one monthly (27 Nov), one quarterly (25 Dec). Strikes ran 20,000 to 250,000. The contract set changes daily, so the universe must be point-in-time.
- The old BTC options (`BTC-19OCT26-71000-C`) are a different product: inverse, settled in BTC. They are **not** in scope and must not leak into the universe.

### Quotes and units

- Option prices are **USDC per 1 BTC of contract size**. Example: a 23 Oct 80,000 put had bid 800, ask 840, mark 824.48.
- `mark_iv`, `bid_iv`, `ask_iv` are **percent** (36.5 means 36.5% vol). Convert at the adapter boundary.
- `underlying_price` is the **expiry-matched future** named in `underlying_index` (for example `BTC_USDC-23OCT26`), not the spot index. In the sample it was 81,118.61 against an index of 81,053.27. This is the forward for Black-76.
- `interest_rate` was `0.0`.
- One `get_book_summary_by_currency?currency=USDC&kind=option` call returns every USDC option (1.6 MB). It carries bid, ask, mark, `mark_iv`, `underlying_price`, `interest_rate`, open interest and volume. It does **not** carry sizes, bid/ask IV or Greeks. In the sample 544 of 596 BTC rows had both a bid and an ask.

### Black-76 replication (the key de-risking result)

Using F = `underlying_price`, r = 0, T = ACT/365 from the ticker timestamp to 08:00 UTC expiry, sigma = `mark_iv`/100:

| Quantity | Ours | Deribit |
|---|---|---|
| Put mark price | 824.20 | 824.48 |
| Delta | -0.358872 | -0.35889 |
| Gamma | 0.000113 | 0.00011 |
| Vega per 1 vol point | 33.8082 | 33.8084 |
| Theta per calendar day | -135.913 | -135.935 |

The 0.03% price gap is consistent with `mark_iv` being rounded to two decimals (0.01 vol point is about 0.34 USDC at this vega). So the conventions are settled: **Black-76 on the matched future, zero rate, ACT/365, vol in decimal, vega per vol point, theta per calendar day.** Two caveats: Deribit documents theta as the *minimum of 1-day theta and lifetime theta* (matters in the last day), and their `rho` did not match a simple Black-76 rho, so **rho is excluded from v1**.

### History

- Public trade history is shallow. `get_last_trades_by_currency_and_time` returned data for the last day and **nothing for any one-day window ending 1 to 365 days ago**. `get_last_trades_by_instrument` returned nothing for an instrument that had a `last_price`.
- There is no order-book history endpoint. **Chain history only exists if we record it.** A paid vendor or Deribit data product could backfill; not verified (see decision D-5).
- Underlying history is deep: `get_tradingview_chart_data` gave daily candles for `BTC-PERPETUAL` back to 2021-04-27 and `BTC_USDC-PERPETUAL` back to 2022-03-09, plus hourly and 1-minute candles for recent windows. A request returns at most about 5,000 candles, so paginate in windows.
- DVOL (`get_volatility_index_data`, BTC) works: hourly for 30 days, daily back to at least 2024 (1,000 points per call, paged with `continuation`). **There is no DVOL for USDC.** `get_historical_volatility` for BTC returned about 16 days of hourly points; the USDC variant returned an implausible value (0.42), so **do not use it**. Compute realized vol ourselves.

### Limits and streaming

From the docs:

- `get_instruments` is expensive: about 1 call per second sustained, burst 50. Do not poll it.
- Other non-matching requests: 500 credits each, 50,000 pool, 10,000 credits/s refill (about 20 requests/s, burst 100). **Public unauthenticated requests are limited per IP, with no documented numbers.** `too_many_requests` (10028) terminates the session.
- 32 connections per IP. One `public/subscribe` can carry up to 500 channels; `subscribe` costs 3,000 credits (about 3.3/s, burst 10). Heartbeat minimum 10 s, and the client must answer `test_request` with `public/test` or be disconnected. HTTP connections expire after 15 minutes.

Measured on 2026-10-18 with an unauthenticated WebSocket probe (`scripts/probe_deribit_ws.py`, 60 s per run):

| Channel | Result |
|---|---|
| `ticker.{inst}.raw` | Rejected without authentication (error 13778) |
| `ticker.{inst}.100ms`, `ticker.{inst}.agg2` | Work. About 0.8 KB per message, roughly 15 messages per minute on a busy option |
| `incremental_ticker.{inst}` | Works. The first message is a full `snapshot`, later ones carry changes |
| `quote.{inst}` | Works. Best bid and ask only, about 0.3 KB |
| `book.{inst}.none.10.100ms`, `...none.10.agg2` | Work. Top-10 snapshots with `change_id`, about 0.4 KB |
| `book.{inst}.agg2` | Works. Incremental `new` and `delete` entries |
| `markprice.options.btc_usdc` | Works. Mark price and IV for the whole chain (about 600 instruments) in one message of about 60 KB roughly every 1.4 s, around 3.7 GB per day uncompressed |
| `deribit_price_index.btc_usdc`, `estimated_expiration_price.btc_usdc` | Work. Under 1 message per second |
| `instrument.state.option.USDC`, `instrument.state.any.USDC`, `instrument.creation.option.USDC`, `trades.option.USDC.100ms` and `.agg2` | Subscribe succeeds but nothing arrived in 60 s. Quiet window or low activity; recheck over a longer run |

Other measurements:

- **The book summary is cached server-side.** Its `usOut` lags real time by 0.5 to 1.8 s, and across 8,940 recorded rows the per-row `creation_timestamp` lags our receipt by a median of 1.1 s and a maximum of 2.2 s. Do not use the summary for clock synchronization. `get_index_price` and `get_time` give a stable offset (the local clock was about 130 ms behind the exchange).
- **Theta near expiry.** Deribit's theta equals our per-day theta times `min(1, days left)`, capped at minus the mark price (checked on options 0.52 days from expiry).
- **Ticker price and forward are not simultaneous.** Gaps between `mark_price` and the price implied by `underlying_price` and `mark_iv` were up to 2.4 bps of forward in the captured tickers.
- **Windows path length.** A deep data directory (over about 260 characters) made pyarrow writes fail. Keep `DORTRADER_RECORDER__DATA_DIR` short.

### Not verified

- Deribit's support articles (Linear USDC Options, API guidance) returned 403, so the **April 2026 settlement change** (in-the-money USDC options settling via a futures entry, then cash) is unverified. It does not affect these four workstreams, but D0 should record the first expiry (19 Oct 2026, 08:00 UTC) to observe it.

## 2. Architecture decisions

| # | Decision | Why |
|---|---|---|
| A1 | One installable Python package `dortrader` in `src/dortrader/`, with subpackages, not many separate packages | Blueprint says modular monolith first. A single package avoids workspace overhead. Layering is enforced by import-linter instead of package boundaries |
| A2 | **Parquet is the system of record for quotes. Postgres (Supabase) holds the catalog, the latest quotes, snapshot index, health, and model outputs** | Quote volume (section 6) would exceed a small Supabase database within days. The blueprint's "raw Parquet plus validated Postgres" split already supports this |
| A3 | Historical reads go through one `as_of(t)` interface built on a pure reducer shared by live and replay | Blueprint: "serve historical replay from the same canonical event representation used by the live aggregator" |
| A4 | Pricing is pure functions over numbers. No IO, no domain-object requirements | Keeps it testable and fast, and keeps the "deterministic, versioned" rule |
| A5 | Pydantic v2 frozen models at boundaries (API, config, adapters, storage rows); NumPy arrays and plain dataclasses inside numeric kernels | Typed contracts without per-element validation cost in hot paths |
| A6 | psycopg 3 with SQL files and small repository classes. No ORM | Simplest thing that is typed and testable |
| A7 | Polars for tabular work; pandas only inside `forecasting` because `arch` needs it | Blueprint stack, with one contained exception |
| A8 | Recorder is a separate long-running process (`apps/worker`), not part of the API | It must run 24/7 and restart independently |
| A9 | The browser only calls FastAPI. No Supabase keys in the frontend | Blueprint trust boundary |
| A10 | Every derived output carries `model_version` and `inputs_hash` | Blueprint: "model outputs explicitly versioned" |

### Target layout

```
src/dortrader/
  domain/        value types and entities (no IO)
  config/        pydantic-settings
  logging/       structlog setup
  clock.py       Clock protocol, SystemClock, FixedClock
  market_data/   deribit/ (rest.py, ws.py, models.py), normalize.py, quality.py, reducer.py, catalog.py
  storage/       parquet_archive.py, snapshots.py, db/ (pool, repositories), migrations notes
  pricing/       black76.py, greeks.py, implied_vol.py, chain.py, arbitrage.py
  volatility/    realized.py, iv_extract.py, svi.py, smile.py, term_structure.py, surface.py
  forecasting/   ewma.py, garch.py, har.py, evaluate.py
apps/api/        FastAPI routes only
apps/worker/     recorder entrypoints
apps/web/        Vite app
supabase/migrations/
tests/           unit, property, golden, integration, api, fixtures
docs/adr/        short decision records
research/        notebooks and experiment configs (later)
```

### Layering rules (enforced in CI)

- `domain` imports nothing from the project.
- `pricing` may import `domain` only.
- `volatility` may import `pricing` and `domain`.
- `forecasting` may import `domain` only.
- `market_data` and `storage` may import `domain`; **`pricing`, `volatility` and `forecasting` must not import `market_data` or `storage`**. Services in `apps/` wire them together.
- Nothing imports `apps`.

## 3. Workstream F: foundation and infrastructure

### F1. Package restructure (size M)

- Add a build backend and `src/` layout so `uv sync` installs `dortrader` editable. Move tool config to match. Keep `apps/api` working.
- Add import-linter with the contracts above.
- **Done when:** `uv run pytest`, ruff, mypy and `lint-imports` pass, and `from dortrader import ...` works from apps and tests without `pythonpath` hacks.

### F2. Typed domain schemas (size M)

Value types:

- `UtcDateTime` (aware, UTC only), `Money(amount: Decimal, currency)`, `Vol` (decimal fraction, never percent), `Rate`, `Qty` (BTC, Decimal), `Price` (USDC), `Strike`.
- Explicit conversion helpers: `vol_from_percent`, `ms_to_utc`.

Entities (blueprint list, scoped to this plan): `Instrument`, `OptionQuote` (the blueprint's `MarketSnapshot` row), `BookLevel`, `OrderBook`, `ChainSnapshot`, `VolPoint`, `SmileFit`, `TermStructurePoint`, `PricingInputs`, `PricingResult`, `Greeks`, `ModelVersion`.

Rules:

- Structural violations raise (naive datetime, negative size, unknown currency, zero strike).
- Market-quality problems become `QualityFlag` values, never exceptions: `NO_BID`, `NO_ASK`, `CROSSED`, `LOCKED`, `WIDE_SPREAD`, `STALE_EXCHANGE_TS`, `STALE_NO_UPDATE`, `OUTSIDE_PRICE_BAND`, `BELOW_TICK`, `IV_MISSING`, `UNDERLYING_STALE`, `SEQUENCE_GAP`, `CLOCK_DRIFT`, `DUPLICATE`.
- Keep original event ID and lineage fields (`source`, `channel`, `received_at`, `exchange_ts`, `raw_ref`) on every normalized row.
- **Tests:** validator unit tests; hypothesis round-trip (model to JSON to model); a snapshot test of the exported JSON Schema so contract changes are deliberate.
- **Done when:** schemas are frozen, documented, and the JSON Schema snapshot is committed.

### F3. Configuration, logging, clock (size S to M)

- **Config:** `pydantic-settings`, env prefix `DORTRADER_`, `.env` support. Sections: `deribit` (environment `prod|test`, base URLs), `storage` (data directory, database URL as `SecretStr`), `recorder` (intervals, universe filter, focus-set rule), `pricing` (rate source), `logging`. The settings object has **no private-key fields** in this plan. Validation fails fast at startup.
- **Logging:** structlog, JSON to stdout, bound `run_id` and `component`, stdlib bridge, a redaction processor for anything secret-shaped.
- **Clock:** a `Clock` protocol so time is injectable. Store `received_at` as wall-clock nanoseconds and measure latency with a monotonic clock. Estimate server clock offset from `public/get_time` on a timer and flag `CLOCK_DRIFT`.
- **Done when:** a unit test proves secrets never appear in logs, and the app refuses to start on invalid config.

### F4. Test harness and CI hardening (size M)

- pytest markers (`unit`, `property`, `golden`, `integration`, `slow`, `network`) with `--strict-markers`. **Network is blocked by default** (`pytest-socket`); `network` tests are opt-in and never run in CI.
- hypothesis profiles (fast locally, deeper in CI). `pytest-cov` with a floor (pricing and volatility high, the rest lower) that only ratchets up.
- CI adds: `uv lock --check`, import-linter, coverage, `pip-audit` and `npm audit`, a Postgres service container that applies migrations and runs integration tests, and a container build for `apps/api` and `apps/worker`.
- **CD for now = build and tag images on `main`.** Where they deploy is decision D-1.
- **Done when:** a deliberately broken layering import, a missing migration, and an unpinned dependency each fail CI.

### F5. Database layer and migrations (size M)

- psycopg 3 connection pool. Supabase's pooler in transaction mode does not support prepared statements, so configure `prepare_threshold=None` (verify against current Supabase docs).
- Migration policy: SQL files in `supabase/migrations/`, forward-only, applied by the Supabase CLI. Tests run against a local Postgres container, not the hosted project.
- Schema v1 (small tables only):
  - `instruments` (immutable attributes, `first_seen_at`, `last_seen_at`) and `instrument_state_events`.
  - `quotes_latest` (one row per instrument, upserted).
  - `chain_snapshots` (snapshot time, source, counts, coverage JSON, Parquet reference).
  - `ingest_health` (per-feed heartbeat, lag percentiles, reconnects, gap counts) and `data_quality_daily`.
  - `model_versions`, plus output tables created later in P and V (`iv_points`, `smile_fits`, `term_structure`, `realized_vol`, `vol_forecasts`).
- Repositories return typed models. Writes are idempotent (`ON CONFLICT`).
- **Done when:** migration, repository and idempotency tests pass in CI.

### F6. Observability baseline and ADRs (size S)

- `GET /api/status`: last event age per feed, recorder run state, schema version. Replaces the bare health check as the operational view.
- Counters persisted to `ingest_health`: feed freshness, packet gaps, IV solver failures, request latency. (Prometheus and OpenTelemetry stay deferred, per the blueprint's "upgrade when needed".)
- ADRs (one page each): modular monolith, storage split, pricing conventions, point-in-time rule, flag-don't-drop.

## 4. Workstream D: market data ingestion and storage

### D0. Spike and fixtures (size S, do first)

Purpose: turn the section 1 facts into committed fixtures and numbers before any production code.

- Save sanitized real payloads to `tests/fixtures/deribit/`: instruments (USDC options), book summary, a ticker for a liquid and an illiquid option, an order book, a chart-data page, a DVOL page, and the replication case from section 1.
- Run a throwaway WebSocket probe to settle the unverified items: exact channel and index names for USDC, which intervals public connections accept for ticker and book, message rates and sizes per channel, and the per-connection subscription ceiling.
- **Measure the data budget** (section 6) instead of trusting estimates.
- Record the 19 Oct 2026 08:00 UTC expiry (last quotes, delivery price) to observe settlement behaviour.
- **Done when:** fixtures are committed, the data budget is measured, and ADR "ingestion tiers" is written.

### Ingestion tiers

| Tier | Source | Scope | Cadence | Purpose |
|---|---|---|---|---|
| 1 | REST `get_book_summary_by_currency(USDC, option)` | All BTC/USDC options | Default every 30 s | Complete chain coverage and a recording that starts early |
| 2 | WebSocket ticker or incremental ticker, grouped book depth 10, option trades, index, instrument state | **Focus set**: nearest 3 expiries, strikes within a set log-moneyness band of the forward, plus any strike with open interest | Seconds, as measured in D0 | Sizes, bid/ask IV, Greeks, depth |
| 3 | REST backfill | Underlying candles, DVOL | Daily jobs | Inputs for realized vol and forecasts |

Tier 1 ships first. Tier 2 is added only after the recorder is stable.

### D1. Deribit REST client, catalog and normalizer (size M)

- Async `httpx` client over JSON-RPC GET. Retry with jittered backoff, per-method token buckets sized well under the documented limits, explicit timeouts, and a reconnect-safe session. Keep the **raw response bytes** and a sha256 alongside every parsed result.
- Parse into Pydantic models that **forbid unknown required changes**: a new field raises a schema-drift warning, a missing expected field is an error.
- Catalog: call `get_instruments` at startup and then on a slow reconcile timer (minutes, not seconds), never as a poll. Diff against the stored catalog, write `instrument_state_events`, and emit a daily catalog snapshot.
- Normalizer (pure functions): raw summary row to `OptionQuote` with percent-to-decimal conversion, USDC money typing, and the `QualityFlag` set. **Flag, never drop.**
- **Tests:** parser tests on fixtures; a drift test (add and remove fields in a fixture); flag tests for each case including crossed and locked books.
- **Done when:** a fixture-driven run produces correct `Instrument` and `OptionQuote` rows with the right flags, and no network is touched.

### D2. Parquet archive writer (size M)

- Layout: `data/raw/source=deribit/channel=<c>/date=YYYY-MM-DD/hour=HH/part-*.parquet`, zstd compressed. **Deviation from the blueprint:** partition by source, channel, date and hour, with `instrument` as a *sorted column* rather than a partition key. Partitioning by instrument would create roughly 600 tiny files per channel per day; sorted row groups give the same pruning.
- Each row keeps `received_at_ns`, `exchange_ts_ms`, `channel`, `instrument_name`, `connection_epoch`, the **original payload**, and its sha256.
- Buffered writes, rotation by time or size, written to a temporary name and atomically renamed, plus a manifest of file name, row count and checksum. Schema version stored in file metadata.
- **Spool and recover:** if the process dies, orphaned temporary files are detected on start and either completed or quarantined, never silently lost.
- **Tests:** round trip, crash-mid-write recovery, manifest checksum verification, and a reader that rejects a corrupted file.
- **Done when:** a kill-and-restart test loses no acknowledged rows.

### D3. REST snapshot recorder worker (size M), start recording

- `apps/worker` process: every interval fetch the book summary, filter to BTC/USDC via the catalog, normalize, write the archive, upsert `quotes_latest`, and record feed health. Also fetch `get_index_price(btc_usdc)` and `public/get_time` for the clock offset.
- **If Postgres is unreachable, keep recording to Parquet and backfill the database later.** Parquet writes never depend on the database.
- Supervisor behavior: restart on crash, log a `gap` marker with start and end whenever a cycle is missed.
- **Done when:** the recorder has run unattended for at least 24 hours with a per-day coverage report. **Every day this is delayed is lost history**, so D3 is the earliest-possible milestone after F1, F2 and D1.

### D4. Curated snapshots, reducer and `as_of` replay (size M)

- A pure reducer `apply(state, event) -> state` builds a `ChainSnapshot`. Live code runs it over incoming events; replay runs it over Parquet events in received order.
- `ChainSnapshot` carries per-expiry forward, per-instrument latest quote, coverage, and per-row flags.
- A materializer writes snapshots on a regular grid (default 1 minute) to `curated/chain_snapshots/` and indexes them in `chain_snapshots`.
- `as_of(t)` selects by **receipt time at or before t**, never exchange time alone, and never includes a contract that was not listed at t.
- **Tests:** out-of-order receipts; a contract listed after t is excluded; and an **equivalence test** that replaying a recorded stream reproduces the live reducer's state exactly (blueprint: "replay determinism").
- **Done when:** the equivalence and determinism tests pass on a recorded day.

### D5. WebSocket client core (size L)

- JSON-RPC framing with request/response matching, `public/set_heartbeat` (start at 15 s, minimum 10) and automatic `public/test` replies, reconnect with exponential backoff and jitter, and **resubscribe from a declared desired-subscription set**.
- Batch subscriptions in groups well under 500 channels, respect the subscribe cost, and cap connections far below 32 per IP. Keep ticker and book on separate connections.
- **Slow-consumer safety:** a bounded queue between the reader and the writer. If it fills, never drop silently: record a `SEQUENCE_GAP` marker, resubscribe, and count it in health.
- Every reconnect increments `connection_epoch`, so gaps are visible in the data.
- **Tests:** a local fake Deribit server (using `websockets`) that can drop connections, stall heartbeats, send out-of-order `change_id`, and flood the client.
- **Done when:** the fake-server suite proves heartbeat, reconnect, resubscribe and gap marking.

### D6. WebSocket recorders for the focus set (size L)

- Channels (names confirmed in D0): ticker or incremental ticker for the focus set, `book.{instrument}.none.10.<interval>` for depth, `trades.option.USDC.<interval>` filtered to BTC, the index price channel, and instrument state and creation.
- Using the grouped depth-10 channel means each message is a full top-of-book snapshot, so **no local book maintenance** is needed beyond `change_id` gap detection. Gaps trigger a REST `get_order_book` resync.
- Book invariants checked and flagged: crossed or locked, price not on the tick grid, non-positive size.
- Focus set recomputed on a timer and on `instrument.creation`, so new strikes are picked up.
- **Done when:** a multi-hour recording reconciles against Tier 1 (same marks within expected tolerance) and logs no unexplained gaps.

### D7. Data quality metrics and dashboards (size M)

- Per day and per expiry: expected versus received snapshots, share of instruments with two-sided quotes, gap list (start, end, duration, cause), feed lag percentiles, reconnect counts, schema drift events.
- **Label degraded periods** (`missing depth`, `poor liquidity`, `feed gap`) in `data_quality_daily` so research can exclude them. Blueprint requirement.
- **Done when:** a day with an injected outage shows up correctly in the report.

### D8. Underlying and DVOL backfill (size S to M)

- Paginated jobs for `get_tradingview_chart_data` (instruments `BTC_USDC-PERPETUAL` and `BTC-PERPETUAL`, resolutions 1 minute, 1 hour, 1 day) and `get_volatility_index_data` (BTC, hourly and daily, following `continuation`). Chunk windows below the candle cap, deduplicate on timestamp, store in Parquet, and verify no gaps.
- Daily incremental run after the initial backfill.
- **Done when:** the daily series from 2022-03-09 (USDC perp) and hourly series are stored with a verified-continuous report.

### D9. Data API and GUI views (size M)

- Endpoints: `GET /api/instruments`, `GET /api/chain?at=<UTC>&expiries=N`, `GET /api/data-quality?date=`, `GET /api/status`.
- Views: **Chain at time** (bid, ask, mark, flags, per-expiry forward), **Data quality** (coverage heatmap by day and expiry, gap list), **Feed status**.
- **Done when:** a recorded timestamp renders a chain with correct forwards and visible flags, and the same request returns identical JSON twice.

## 5. Workstream P: pricing and Greeks engine

### P1. Black-76 kernel and Greeks (size M)

- Inputs: forward `F` and strike `K` in USDC, time `T` in years, `sigma` (decimal), discount factor `df`, and option type. `T` is ACT/365 measured to the **08:00 UTC expiry timestamp** from the instrument record, not a calendar date.
- Rate source defaults to Deribit's reported `interest_rate` (0.0), as a parameter with an explicit discount-factor convention.
- Outputs: price, delta, gamma, vega **per 1 vol point**, theta **per calendar day**. Add vanna and volga ("cross-Greeks where needed"), clearly separated and marked as extras. **Rho is excluded from v1** because Deribit's convention did not match a simple Black-76 rho.
- Provide a **Deribit-style theta variant** (minimum of 1-day theta and lifetime theta) used only for comparison near expiry.
- Position scaling is explicit: value = premium x `contract_size` x quantity, with quantity in BTC in multiples of `min_trade_amount`. Greeks scale the same way and are labeled per-contract versus per-position.
- Vectorized NumPy implementation with a scalar convenience API. Units are stated once in the module docstring and nowhere else.
- Output carries `PRICING_MODEL_VERSION` (for example `black76-1`).
- **Done when:** all of P3's checks pass for this kernel.

### P2. Implied volatility solver (size M)

- Pre-checks: price must lie inside no-arbitrage bounds (intrinsic `df * max(+-(F-K), 0)` up to `df*F` for a call or `df*K` for a put). Otherwise return a status, not a number.
- Solve with a safeguarded method: a good initial guess, a bracket, Newton steps using analytic vega, and a Brent fallback. Vectorized for whole chains (about 600 contracts).
- Result type: `value`, `status` in `ok | below_intrinsic | above_upper_bound | no_bracket | not_converged | vega_too_small`, iteration count, and residual. **Never return a silent number.**
- **Tick-aware uncertainty:** because prices move in 5 USDC ticks (20 above 1,000), also report an IV interval from price minus and plus half a tick. A wide interval is a data-quality fact for deep out-of-the-money options, surfaced as a flag.
- Use the out-of-the-money side for smile work (calls above the forward, puts below), converting through put-call parity, because in-the-money prices carry little vega.
- **Tests:** round trip (sigma to price to sigma within 1e-8) across moneyness and expiry grids, including `T` of a few minutes; bound violations return the right status; monotonic in price; vectorized equals scalar.
- **Done when:** round-trip holds on the grid and every failure mode returns a labeled status.

### P3. Reference validation (size M)

Layers of evidence, in the blueprint's Model QA order:

1. **Golden values** from an independent implementation: QuantLib (dev-only, in a separate optional test group), generated once, committed as JSON with the generator script and library version recorded. No hand-typed numbers.
2. **Identities and properties** (hypothesis): put-call parity `C - P = df*(F-K)`; price monotonic in sigma; convex in strike (butterfly); delta within bounds and monotonic in strike; gamma and vega non-negative; price homogeneous in (F, K); put-call symmetry.
3. **Finite-difference checks** of delta, gamma, vega and theta, with documented step sizes and tolerances.
4. **Stability:** extreme strikes, `T` of minutes, tiny and huge sigma, `F = K`. No NaN or infinity, status flags instead.
5. **Live replication against Deribit:** the section 1 case as a fixture, plus a larger set from D3 recordings once available. Report the **distribution** of differences (price, IV, delta, gamma, vega, theta) by moneyness and time to expiry. **Do not choose a tolerance to make this pass**; the report says what the gap is and why.
- **Done when:** all five layers run in CI (the QuantLib generation is a manual, documented step, not a CI dependency).

### P4. Chain pricing service and no-arbitrage flags (size M)

- From a `ChainSnapshot`, compute per contract: IV from bid, ask, mid and mark; Greeks; Deribit's own IV and Greeks when present; and the differences.
- Chain-level flags per expiry: put-call parity violation across a strike, vertical-spread monotonicity (call price must fall as strike rises), butterfly convexity, and cross-expiry calendar checks on total variance. Also `STALE_INPUT` when the forward or quote is old.
- Cache results keyed by `(snapshot_id, model_version)`.
- **Done when:** a recorded chain produces IV, Greeks and flags with reproducible output, and the comparison report from P3 is generated from real recordings.

### P5. Pricing API and GUI (size S to M)

- `POST /api/price` (calculator: F, K, T, sigma or price, type) and an extended `GET /api/chain` that includes IV and Greeks.
- Views: **Pricing calculator** and the **chain table with IV and Greeks columns**, with flagged cells and solver status visible.
- **Done when:** the calculator matches the unit-test golden cases and the chain view shows our IV next to Deribit's.

### P6. Numerics and performance hardening (size S)

- Benchmarks as tests with generous ceilings (price for 600 contracts, IV for a full chain). Fuzz the solver with random valid and invalid inputs.
- **Done when:** benchmarks are recorded and a regression fails CI.

## 6. Workstream V: volatility analytics and forecasting

### V1. Realized volatility (size M)

- Inputs come from D8 candles for `BTC_USDC-PERPETUAL` (primary) and `BTC-PERPETUAL` (cross-check).
- Estimators: close-to-close, Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang. Windows: 1, 7, 30 days; sampling: 1 minute, 5 minutes, 1 hour. **Annualize with 365 days** (24/7 market) and state it.
- Compare our realized vol to Deribit's `get_historical_volatility` for BTC only as a sanity check, not as the source.
- **Tests:** simulated GBM with known sigma recovers it within statistical error; estimators agree on constant-vol data; bias behavior documented on jumpy data.
- **Done when:** a realized-vol table by window and estimator is stored with `model_version`.

### V2. IV extraction for surfaces (size M)

- From P4 output, build a clean `VolPoint` set per expiry: out-of-the-money options only, two-sided quotes, spread and staleness filters, and the tick-aware IV interval as a weight (wide interval means low weight).
- Coordinates: log-moneyness `k = ln(K/F)` and total variance `w = sigma^2 * T`.
- Robust outlier handling (for example median-absolute-deviation filtering on local smile) with every rejection recorded, not hidden.
- **Done when:** each rejected point carries a reason, and results are deterministic for a given snapshot.

### V3. Smile fitting (size L)

- Baseline per-expiry model: **SVI** (raw parameterization) fitted by constrained least squares, with a fallback to a monotone spline on total variance when there are too few points or the fit fails. Minimum strike count and a fit-quality gate decide which is used and the result says which.
- No-arbitrage diagnostics on each slice: butterfly (non-negative density) and a check for `w >= 0`. Report violations and residuals; **do not silently repair**.
- Short-dated expiries (a few hours to days) are noisy, so every fit stores `n_points`, RMSE, parameter bounds hit, and a quality flag.
- Heston, SABR and rough Bergomi stay **out**, per the blueprint, until baseline diagnostics justify them.
- **Tests:** recover known SVI parameters from synthetic data with noise; butterfly check catches a deliberately bad slice; fit is stable under small input perturbation.
- **Done when:** fits for recorded snapshots are stored in `smile_fits` with diagnostics.

### V4. Term structure and surface (size M to L)

- ATM implied variance by expiry, interpolated **linearly in total variance** to constant maturities (1 day, 7 days, 30 days), with a calendar-arbitrage check (total variance must not decrease with maturity at fixed `k`).
- Delta-space metrics from the fitted smile: ATM vol, 25-delta risk reversal and butterfly, skew slope. Forward variance between expiries.
- Surface object `w(k, T)` with explicit extrapolation rules (flat in `k` beyond the quoted wings, no extrapolation beyond the longest expiry) and flags when queried outside the supported region.
- **Done when:** the term-structure and surface objects reproduce the fitted slices at their own expiries and pass the calendar check on recordings.

### V5. Volatility forecast baselines and evaluation (size L)

- Baselines: **EWMA** (RiskMetrics lambda 0.94, plus a fitted lambda), **GARCH(1,1)** via `arch`, a GJR variant for asymmetry, and **HAR-RV** as a cheap, strong benchmark (optional but recommended). Naive references: last realized vol and trailing average.
- Targets (default, decision D-6): future realized vol over the next 24 hours and the next 7 days, from hourly `BTC_USDC-PERPETUAL` returns.
- Evaluation harness: **rolling-origin only**, no random splits, an embargo between train and test where overlapping windows would leak. Metrics: QLIKE and MSE on variance, MAE on vol, Mincer-Zarnowitz regression, and comparison against the current ATM implied vol.
- Register each fitted model as a `ModelVersion` (code hash, parameter hash, data hash). **A model that cannot beat the naive baseline out of sample is reported as such.**
- **Done when:** a reproducible evaluation report ranks baselines on a frozen window and stores the forecasts.

### V6. Implied versus realized analytics (size M)

- Variance risk premium (ATM implied minus forecast and minus subsequent realized), vol cone by horizon, and IV percentile or rank once enough recorded history exists. Until then these views show the available span honestly.
- **Done when:** the premium series is computed, stored and its history length is displayed with it.

### V7. Volatility API and GUI (size M to L)

- Endpoints: `GET /api/smile?at=&expiry=`, `GET /api/term-structure?at=`, `GET /api/realized-vol`, `GET /api/vol-forecast`.
- Views (Plotly, per the blueprint; use the slim basic bundle and lazy-load it): **Smile** per expiry (bid, ask, mark and fitted curve, with flagged points), **Term structure**, **Realized vs implied vs forecast**. A 3D surface comes last.
- **Done when:** a recorded timestamp renders smile and term structure that match the stored fits, and the charts show data-quality flags.

## 7. Data budget (estimates to replace with D0 measurements)

These are order-of-magnitude assumptions, not measurements.

| Item | Assumption | Rough result |
|---|---|---|
| Tier 1 rows | 596 instruments x 30 s cadence | about 1.7 million rows per day |
| Tier 1 on disk | about 20 typed columns, zstd, sorted | tens of MB per day |
| Tier 2 raw messages | focus set, ticker and depth at second-scale | could reach hundreds of MB per day if too aggressive |
| Postgres | catalog, latest quotes, snapshot index, fits | small (megabytes) |

The decisive point: storing every quote in a small hosted Postgres would fill it quickly, so quotes stay in Parquet. Check Supabase's current plan size limits when choosing a tier (the free tier has historically been small).

## 8. Execution order

Dependencies:

```
F1 -> F2 -> D1 -> D2 -> D3 -> D4 -> P4
          \-> P1 -> P2 -> P3 -> P4 -> V2 -> V3 -> V4 -> V6
D0 (first) ; F3, F4 in parallel with D1 ; F5 before D4
D3 -> D8 -> V1 -> V5 -> V6
D5 -> D6 -> D7
D4 + P5 -> D9 ; V7 after V3/V4/V5
```

Waves:

| Wave | Items | Exit |
|---|---|---|
| 0 | Commit M0; **D0**; F1 | Facts frozen, fixtures committed, package restructured |
| 1 | F2, F3, F4, D1, D2, P1 | Typed schemas, tested parsers, archive writer, Black-76 kernel |
| 2 | **D3 (recording starts)**, P2, P3, F5, D8 | Recorder running, IV solver validated, candle history stored |
| 3 | D4, P4, P5, V1, V2 | Replayable chain with IV and Greeks in the GUI |
| 4 | D5, D6, V3, V4, V5, D7 | WebSocket tier, smile and surface, forecast baselines |
| 5 | D9, V6, V7, P6, F6 | Full GUI, benchmarks, docs |

Parallel sessions that are safe once F2 lands: a **data track** (D1 to D4), a **pricing track** (P1 to P3), and later a **volatility track** (V1 after D8, V2 onward after P4). They touch different packages, so they do not collide.

## 9. Acceptance gates

- **Data (blueprint P1):** replay a historical as-of snapshot from recorded data; gaps and schema drift are detected and labeled; the recorder has run at least 7 days with a per-day completeness report. Proposed bar: at least 99% of expected Tier 1 snapshots present, with every shortfall explained by a labeled gap.
- **Pricing (blueprint P2):** golden cases cross-validated against an independent library; no-arbitrage flags raised on constructed bad inputs; stable behavior for near-expiry options; live comparison report with Deribit published.
- **Volatility:** smile fits with stored diagnostics; calendar check passing or flagged; forecast baselines evaluated out of sample with the naive baseline included.
- **Platform:** CI green on layering, coverage floors, migrations, container builds; the chain, smile and term structure views render a recorded timestamp reproducibly.

## 10. Risks

| Risk | Mitigation |
|---|---|
| **No history unless recorded** | D3 is the first production milestone; evaluate a vendor for backfill (D-5) |
| Recorder downtime creates gaps | Always-on host (D-1), gap markers, Parquet-first writes, labeled degraded periods |
| Public per-IP rate limits are undocumented | Conservative request rates, measure in D0, back off on any limit error |
| Short-dated options give noisy IV and fits | Tick-aware IV intervals, strike-count gates, quality flags on every fit |
| Deribit changes schema or contract details | Drift detection, metadata-driven contracts, never parse names, never hard-code multipliers |
| Marks and fills conflated later | Keep mark, mid, quote and fill as distinct fields from day one |
| Hosted database size or availability | Quotes in Parquet, small Postgres, buffer-and-backfill |
| Look-ahead leakage | Receipt-time `as_of`, point-in-time universe, rolling-origin forecasting evaluation, equivalence tests |
| Pandas pulled in by `arch` | Confined to `forecasting`, converted at its boundary |
| Settlement behavior (April 2026 change) unverified | Observe the 19 Oct expiry in D0; matters for later P&L, not these workstreams |

## 11. Decisions to freeze

Recommended defaults are in bold. Each is needed by the milestone named.

| # | Decision | Needed by |
|---|---|---|
| D-1 | **Where the recorder runs.** Recommend a small always-on host (cheap VPS or container service) rather than a laptop that sleeps | D3 |
| D-2 | **Supabase tier and retention.** Recommend a paid tier if you want database-side analytics history; otherwise keep Postgres small as designed | F5 |
| D-3 | **Tier 1 cadence.** **30 s**, adjustable after D0 measurements | D3 |
| D-4 | **Focus set.** **Nearest 3 expiries, strikes within a log-moneyness band of the forward, plus strikes with open interest** | D6 |
| D-5 | **Historical backfill vendor.** **None for now (record forward)**; evaluate cost and coverage separately | before V6 relies on deep chain history |
| D-6 | **Forecast target.** **Realized vol over the next 24 hours and 7 days** | V5 |
| D-7 | **Discount rate.** **Use Deribit's reported `interest_rate` (0.0)** | P1 |
| D-8 | **Accept pandas inside `forecasting` only** | V5 |
| D-9 | **Plotly (slim bundle) for charts** | V7 |
| D-10 | **Capital denomination USDC, risk budget deferred** (blueprint decision-log item, not needed until strategies) | later |

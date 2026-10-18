# Running the recorder around the clock

Chain history cannot be backfilled, so the recorder should run on an always-on machine. It needs outbound HTTPS to `www.deribit.com` only; open no inbound ports.

## One-time setup (Linux host with Docker and the compose plugin)

```bash
git clone https://github.com/damiandamian181061-bit/DORTRADER-PLATFORM.git
cd DORTRADER-PLATFORM
mkdir -p data && sudo chown 1000:1000 data    # the container runs as uid 1000
docker compose up -d --build
```

Make sure the host clock is synchronized (`chrony` or `systemd-timesyncd`). The recorder compares its clock with the exchange every cycle and flags quotes with `clock_drift` beyond 2 s.

## Check that it works

```bash
docker compose ps                  # STATUS should become "healthy" after about 90 s
docker compose logs -f recorder    # one "cycle" line every 30 s
cat data/_heartbeat.json           # last good cycle time and quote count
```

The healthcheck fails when no cycle succeeded for three poll intervals. `restart: unless-stopped` brings the container back after a crash or reboot, and on start the recorder settles any file a crash left half-written.

## Data

Parquet files land under `data/` (`raw/`, `normalized/`, `catalog/`, `events/`). A finished file never changes, so it is safe to copy while the recorder runs. Check growth with `du -sh data` after a day; size has not been measured yet.

Copy `data/` to separate storage regularly, because a single host is a single point of failure. For example, nightly:

```bash
rclone sync data remote:dortrader-data --exclude "_heartbeat.json*"
```

## Updating

```bash
git pull && docker compose up -d --build
```

The data directory is a volume and survives rebuilds.

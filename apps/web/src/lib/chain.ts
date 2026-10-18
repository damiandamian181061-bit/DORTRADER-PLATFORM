export type Contract = {
  instrument_name: string
  bid: number | null
  ask: number | null
  mark: number
  quote_age_seconds: number
  open_interest: number
  bid_iv: number | null
  ask_iv: number | null
  mid_iv: number | null
  mark_iv: number | null
  deribit_mark_iv: number | null
  iv_diff_points: number | null
  iv_spread_points: number | null
  delta: number | null
  gamma: number | null
  vega: number | null
  theta: number | null
  flags: string[]
}

export type StrikeRow = {
  strike: number
  call: Contract | null
  put: Contract | null
}

export type Expiry = {
  expiration: string
  forward: number | null
  years_to_expiry: number
  strikes: StrikeRow[]
}

export type Chain = {
  snapshot_at: string
  model_version: string
  inputs_hash: string
  listed_without_quote: number
  quotes_without_instrument: number
  expiries: Expiry[]
}

export type Gap = { start: string; end: string; seconds: number }

export type Status = {
  now: string
  healthy: boolean
  last_good_at: string | null
  age_seconds: number | null
  quotes_per_cycle: number | null
  run_id: string | null
  recorded_from: string | null
  recorded_to: string | null
  recent_gaps: Gap[]
}

type Fetch = typeof fetch

async function getJson<T>(
  url: string,
  fetchImpl: Fetch,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetchImpl(url, { signal })
  if (!response.ok) {
    let detail = `request failed: ${response.status}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // keep the status-based message
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export function fetchChain(
  params: { at?: string; expiries: number },
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
): Promise<Chain> {
  const query = new URLSearchParams({ expiries: String(params.expiries) })
  if (params.at) query.set('at', params.at)
  return getJson<Chain>(`/api/chain?${query}`, fetchImpl, signal)
}

export function fetchStatus(
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
): Promise<Status> {
  return getJson<Status>('/api/status', fetchImpl, signal)
}

/** A `datetime-local` value is read as UTC: '2026-10-18T19:17' becomes '2026-10-18T19:17:00Z'. */
export function utcInputToIso(value: string): string | undefined {
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(:\d{2})?$/.exec(value)
  if (!match) return undefined
  return `${match[1]}${match[2] ?? ':00'}Z`
}

/** The reverse, for filling the input from an API timestamp. */
export function isoToUtcInput(iso: string): string {
  return iso.slice(0, 16)
}

export const dash = '–'

export function fmtPrice(value: number | null): string {
  if (value === null) return dash
  return value.toLocaleString('en-US', {
    maximumFractionDigits: value < 100 ? 2 : 0,
  })
}

export function fmtIv(value: number | null): string {
  return value === null ? dash : (value * 100).toFixed(1)
}

export function fmtGreek(value: number | null, digits = 2): string {
  return value === null ? dash : value.toFixed(digits)
}

export function fmtSigned(value: number | null, digits = 2): string {
  if (value === null) return dash
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`
}

export function fmtAge(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} s`
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

export function fmtDays(years: number): string {
  const days = years * 365
  return days < 1 ? `${(days * 24).toFixed(1)} h` : `${days.toFixed(2)} d`
}

export function fmtUtc(iso: string): string {
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`
}

/** Index of the strike closest to the forward, or -1 when there is nothing to compare. */
export function nearestStrikeIndex(
  strikes: StrikeRow[],
  forward: number | null,
): number {
  if (forward === null || strikes.length === 0) return -1
  let best = 0
  for (let i = 1; i < strikes.length; i++) {
    if (
      Math.abs(strikes[i].strike - forward) <
      Math.abs(strikes[best].strike - forward)
    )
      best = i
  }
  return best
}

const FLAG_LABELS: Record<string, string> = {
  no_bid: 'No bid',
  no_ask: 'No ask',
  crossed: 'Crossed book',
  locked: 'Locked book',
  wide_spread: 'Wide spread',
  off_tick: 'Price off the tick grid',
  iv_missing: 'No implied vol from the exchange',
  stale_exchange_ts: 'Exchange timestamp is old',
  stale_no_update: 'Quote has not updated recently',
  duplicate: 'Duplicate row',
  unknown_instrument: 'Instrument not in the catalog',
  clock_drift: 'Local clock differs from the exchange',
  monotonicity_violation: 'Price breaks monotonicity across strikes',
  butterfly_violation: 'Price breaks convexity across strikes',
  parity_violation: 'Call and put break put-call parity',
}

export function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag
}

/** Flags that suggest the numbers are wrong, as opposed to merely one-sided or quiet. */
export const SERIOUS_FLAGS = new Set([
  'crossed',
  'locked',
  'monotonicity_violation',
  'butterfly_violation',
  'parity_violation',
  'clock_drift',
  'stale_no_update',
  'stale_exchange_ts',
])

export function hasSeriousFlag(flags: string[]): boolean {
  return flags.some((flag) => SERIOUS_FLAGS.has(flag))
}

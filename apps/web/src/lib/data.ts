import { getJson } from './api'
import type { Fetch } from './api'
import { fmtAge } from './chain'
import type { Trace } from './vol'

export type SeriesCoverage = {
  instrument: string
  step_seconds: number
  first: string
  last: string
  candles: number
  gaps: number
  missing: number
}

export type BarPoint = {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
}

export type Bars = {
  instrument: string
  step_seconds: number
  stride: number
  points: BarPoint[]
}

export type RecorderEvent = {
  at: string
  run_id: string
  kind: string
  detail: Record<string, unknown>
}

export type InstrumentInfo = {
  instrument_id: number
  name: string
  option_type: 'call' | 'put'
  strike: number
  expiration: string
  created_at: string
  contract_size: number
  tick_size: number
  tick_size_steps: { above_price: number; tick_size: number }[]
  min_trade_amount: number
  state: string
  is_active: boolean
  maker_commission: number | null
  taker_commission: number | null
}

export const EVENT_KINDS = [
  'gap',
  'error',
  'schema_drift',
  'catalog_change',
] as const

export function fetchCoverage(fetchImpl: Fetch = fetch, signal?: AbortSignal) {
  return getJson<SeriesCoverage[]>('/api/bars/coverage', fetchImpl, signal)
}

export function fetchBars(
  params: { instrument: string; stepSeconds: number; days: number },
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
) {
  const query = new URLSearchParams({
    instrument: params.instrument,
    step_seconds: String(params.stepSeconds),
    days: String(params.days),
  })
  return getJson<Bars>(`/api/bars?${query}`, fetchImpl, signal)
}

export function fetchEvents(
  params: { kind?: string; days: number; limit: number },
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
) {
  const query = new URLSearchParams({
    days: String(params.days),
    limit: String(params.limit),
  })
  if (params.kind) query.set('kind', params.kind)
  return getJson<RecorderEvent[]>(`/api/events?${query}`, fetchImpl, signal)
}

export function fetchInstruments(
  at?: string,
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
) {
  const query = at ? `?${new URLSearchParams({ at })}` : ''
  return getJson<InstrumentInfo[]>(
    `/api/instruments${query}`,
    fetchImpl,
    signal,
  )
}

export function stepLabel(seconds: number): string {
  if (seconds % 86_400 === 0)
    return seconds === 86_400 ? '1 day' : `${seconds / 86_400} days`
  if (seconds % 3600 === 0)
    return seconds === 3600 ? '1 hour' : `${seconds / 3600} hours`
  if (seconds % 60 === 0)
    return seconds === 60 ? '1 minute' : `${seconds / 60} minutes`
  return `${seconds} s`
}

export function seriesKey(series: {
  instrument: string
  step_seconds: number
}): string {
  return `${series.instrument}|${series.step_seconds}`
}

export function barTraces(bars: Bars): Trace[] {
  const label = bars.instrument.startsWith('DVOL') ? 'DVOL index' : 'Close'
  return [
    {
      name: `${label}, ${stepLabel(bars.step_seconds)} candles`,
      x: bars.points.map((p) => p.time),
      y: bars.points.map((p) => p.close),
      color: '#38bdf8',
    },
  ]
}

export function eventSummary(event: RecorderEvent): string {
  const d = event.detail
  const list = (value: unknown) => (Array.isArray(value) ? value : [])
  switch (event.kind) {
    case 'gap':
      return `${fmtAge(Number(d.seconds))} without data`
    case 'error':
      return `${String(d.where ?? 'unknown')}: ${String(d.error ?? '')}`
    case 'schema_drift':
      return `unexpected fields in ${String(d.what ?? 'a response')}: ${list(d.unexpected_fields).join(', ')}`
    case 'catalog_change':
      return (
        `${list(d.added).length} listed, ${list(d.removed).length} delisted, ` +
        `${list(d.state_changes).length} state changes, ${list(d.changed).length} changed`
      )
    default:
      return JSON.stringify(d)
  }
}

export type InstrumentFilter = {
  text: string
  optionType: '' | 'call' | 'put'
  expiry: string
}

export function filterInstruments(
  instruments: InstrumentInfo[],
  filter: InstrumentFilter,
): InstrumentInfo[] {
  const text = filter.text.trim().toLowerCase()
  return instruments.filter(
    (i) =>
      (filter.optionType === '' || i.option_type === filter.optionType) &&
      (filter.expiry === '' || i.expiration === filter.expiry) &&
      (text === '' ||
        i.name.toLowerCase().includes(text) ||
        String(i.strike).includes(text)),
  )
}

export function expiryOptions(instruments: InstrumentInfo[]): string[] {
  return [...new Set(instruments.map((i) => i.expiration))].sort()
}

export function fmtTicks(instrument: InstrumentInfo): string {
  const steps = [...instrument.tick_size_steps].sort(
    (a, b) => a.above_price - b.above_price,
  )
  return steps.reduce(
    (text, step) =>
      `${text}, then ${step.tick_size} above ${step.above_price.toLocaleString('en-US')}`,
    String(instrument.tick_size),
  )
}

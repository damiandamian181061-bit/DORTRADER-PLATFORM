import { getJson } from './api'
import type { Fetch } from './api'

export type VolPoint = { time: string; vol: number | null }
export type RealizedSeries = { estimator: string; points: VolPoint[] }
export type RealizedVol = {
  instrument: string
  window_days: number
  estimators: string[]
  series: RealizedSeries[]
  dvol: VolPoint[]
}

export type ModelForecast = { model: string; vol: number }
export type HorizonForecast = {
  horizon_hours: number
  forecasts: ModelForecast[]
}
export type LastRealized = { window_hours: number; vol: number | null }
export type Forecast = {
  instrument: string
  as_of: string
  last_realized: LastRealized[]
  dvol: number | null
  horizons: HorizonForecast[]
}

export type ModelScore = {
  model: string
  params_hash: string
  n: number
  qlike: number
  mse_variance: number
  mae_vol: number
  bias_vol: number
  mz_intercept: number | null
  mz_slope: number | null
  mz_r2: number | null
  gain_vs_naive: number | null
  gain_low: number | null
  gain_high: number | null
  verdict: string
}
export type HorizonReport = {
  horizon_hours: number
  step_hours: number
  origins: number
  scores: ModelScore[]
}
export type Evaluation = {
  series: string
  generated_at: string
  data_hash: string
  window_start: string
  window_end: string
  horizons: HorizonReport[]
}

export function fetchRealized(
  params: { windowDays: number; days: number; estimators: string[] },
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
): Promise<RealizedVol> {
  const query = new URLSearchParams({
    window_days: String(params.windowDays),
    days: String(params.days),
  })
  for (const estimator of params.estimators)
    query.append('estimators', estimator)
  return getJson<RealizedVol>(`/api/vol/realized?${query}`, fetchImpl, signal)
}

export function fetchForecast(
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
): Promise<Forecast> {
  return getJson<Forecast>('/api/vol/forecast', fetchImpl, signal)
}

export function fetchEvaluation(
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
): Promise<Evaluation> {
  return getJson<Evaluation>('/api/vol/evaluation', fetchImpl, signal)
}

export const dash = '–'

export function fmtVol(value: number | null | undefined): string {
  return value === null || value === undefined
    ? dash
    : `${(value * 100).toFixed(1)}%`
}

export function fmtPoints(value: number | null): string {
  if (value === null) return dash
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}`
}

export function fmtNumber(value: number | null, digits = 3): string {
  return value === null ? dash : value.toFixed(digits)
}

export function fmtGain(score: ModelScore): string {
  if (
    score.gain_vs_naive === null ||
    score.gain_low === null ||
    score.gain_high === null
  ) {
    return dash
  }
  const signed = (n: number) => `${n >= 0 ? '+' : '-'}${Math.abs(n).toFixed(3)}`
  return `${signed(score.gain_vs_naive)} (${signed(score.gain_low)}, ${signed(score.gain_high)})`
}

export function horizonLabel(hours: number): string {
  if (hours % 24 !== 0) return `${hours} hours`
  const days = hours / 24
  return days === 1 ? '24 hours' : `${days} days`
}

const MODEL_LABELS: Record<string, string> = {
  naive: 'Last window (naive)',
  trailing_30d: 'Trailing 30 days',
  ewma_riskmetrics: 'EWMA (RiskMetrics)',
  ewma_fitted: 'EWMA (fitted)',
  garch: 'GARCH(1,1)',
  gjr_garch: 'GJR-GARCH(1,1)',
  har: 'HAR-RV',
  dvol_30d_implied: 'DVOL (30-day implied)',
}

export function modelLabel(name: string): string {
  return MODEL_LABELS[name] ?? name
}

export const ESTIMATOR_LABELS: Record<string, string> = {
  close_to_close: 'Close-to-close',
  parkinson: 'Parkinson',
  garman_klass: 'Garman-Klass',
  rogers_satchell: 'Rogers-Satchell',
  yang_zhang: 'Yang-Zhang',
}

export type Trace = {
  name: string
  x: string[]
  y: (number | null)[]
  color: string
  dash?: string
}

const ESTIMATOR_COLORS = ['#38bdf8', '#a78bfa', '#34d399', '#f472b6', '#fb923c']
const DVOL_COLOR = '#fbbf24'

/** Chart series in vol points (percent), one per estimator plus DVOL when it exists. */
export function toTraces(realized: RealizedVol): Trace[] {
  const traces: Trace[] = realized.series.map((series, index) => ({
    name: `${ESTIMATOR_LABELS[series.estimator] ?? series.estimator}, ${realized.window_days}-day`,
    x: series.points.map((p) => p.time),
    y: series.points.map((p) => (p.vol === null ? null : p.vol * 100)),
    color: ESTIMATOR_COLORS[index % ESTIMATOR_COLORS.length],
  }))
  if (realized.dvol.some((p) => p.vol !== null)) {
    traces.push({
      name: 'DVOL (30-day implied)',
      x: realized.dvol.map((p) => p.time),
      y: realized.dvol.map((p) => (p.vol === null ? null : p.vol * 100)),
      color: DVOL_COLOR,
      dash: 'dot',
    })
  }
  return traces
}

export type ForecastRow = {
  model: string
  values: Record<number, number | null>
}

/** One row per model, with its forecast at each horizon (null where it has none). */
export function forecastRows(forecast: Forecast): ForecastRow[] {
  const order: string[] = []
  for (const horizon of forecast.horizons) {
    for (const f of horizon.forecasts)
      if (!order.includes(f.model)) order.push(f.model)
  }
  return order.map((model) => ({
    model,
    values: Object.fromEntries(
      forecast.horizons.map((h) => [
        h.horizon_hours,
        h.forecasts.find((f) => f.model === model)?.vol ?? null,
      ]),
    ),
  }))
}

export function verdictClass(verdict: string): string {
  if (verdict === 'better than naive') return 'text-emerald-300'
  if (verdict === 'worse than naive') return 'text-red-300'
  return 'text-slate-400'
}

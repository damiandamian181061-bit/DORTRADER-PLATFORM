import { lazy, Suspense, useMemo, useState } from 'react'
import { fmtUtc } from '../lib/chain'
import {
  ESTIMATOR_LABELS,
  dash,
  fetchEvaluation,
  fetchForecast,
  fetchRealized,
  fmtGain,
  fmtNumber,
  fmtPoints,
  fmtVol,
  forecastRows,
  horizonLabel,
  modelLabel,
  toTraces,
  verdictClass,
} from '../lib/vol'
import type { Forecast } from '../lib/vol'
import { Alert, Field, Note, Section, inputClass } from './ui'
import { useRemote } from './useRemote'

const PlotlyChart = lazy(() => import('./PlotlyChart'))

const INSTRUMENTS = ['BTC_USDC-PERPETUAL', 'BTC-PERPETUAL']
const WINDOWS = [7, 14, 30, 90]
const RANGES = [90, 180, 365, 730]

export function VolatilityView() {
  const [instrument, setInstrument] = useState(INSTRUMENTS[0])
  const [windowDays, setWindowDays] = useState(30)
  const [days, setDays] = useState(365)
  const [estimators, setEstimators] = useState<string[]>([
    'close_to_close',
    'yang_zhang',
  ])
  const [horizon, setHorizon] = useState(24)

  const realized = useRemote(
    (signal) =>
      fetchRealized(
        { instrument, windowDays, days, estimators },
        fetch,
        signal,
      ),
    `realized-${instrument}-${windowDays}-${days}-${estimators.join(',')}`,
  )
  const forecast = useRemote(
    (signal) => fetchForecast(instrument, fetch, signal),
    `forecast-${instrument}`,
  )
  const evaluation = useRemote(
    (signal) => fetchEvaluation(fetch, signal),
    'evaluation',
  )

  const traces = useMemo(
    () => (realized.data ? toTraces(realized.data) : []),
    [realized.data],
  )
  const horizonReport = evaluation.data?.horizons.find(
    (h) => h.horizon_hours === horizon,
  )

  const toggleEstimator = (id: string) =>
    setEstimators((current) =>
      current.includes(id)
        ? current.length > 1
          ? current.filter((e) => e !== id)
          : current // at least one estimator stays selected
        : [...current, id],
    )

  return (
    <div className="space-y-10">
      <Section title="Realized volatility against DVOL">
        <div className="flex flex-wrap items-end gap-4">
          <Field label="Series">
            <select
              className={inputClass}
              value={instrument}
              onChange={(e) => setInstrument(e.target.value)}
            >
              {INSTRUMENTS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Window">
            <select
              className={inputClass}
              value={windowDays}
              onChange={(e) => setWindowDays(Number(e.target.value))}
            >
              {WINDOWS.map((n) => (
                <option key={n} value={n}>
                  {n} days
                </option>
              ))}
            </select>
          </Field>
          <Field label="Show">
            <select
              className={inputClass}
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
            >
              {RANGES.map((n) => (
                <option key={n} value={n}>
                  last {n} days
                </option>
              ))}
            </select>
          </Field>
          <fieldset className="flex flex-col gap-1 text-sm text-slate-400">
            <legend className="sr-only">Estimators</legend>
            Estimators
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {Object.entries(ESTIMATOR_LABELS).map(([id, label]) => (
                <label
                  key={id}
                  className="flex items-center gap-1.5 text-slate-200"
                >
                  <input
                    type="checkbox"
                    checked={estimators.includes(id)}
                    onChange={() => toggleEstimator(id)}
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
        </div>
        <Note>
          Annualized, from hourly {instrument} candles, one point per day. DVOL
          is Deribit&apos;s 30-day implied volatility index. Range-based
          estimators (Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang) use
          each candle&apos;s high and low and are steadier than close-to-close,
          but read a little low on sampled prices.
        </Note>
        {realized.error && <Alert text={realized.error} />}
        {realized.loading && !realized.data && <Note>Loading chart…</Note>}
        {realized.data && (
          <div className={realized.loading ? 'opacity-60' : undefined}>
            <Suspense fallback={<Note>Loading chart…</Note>}>
              <PlotlyChart
                traces={traces}
                yTitle="annualized vol (%)"
                label={`Realized volatility of ${instrument} and DVOL over the last ${days} days`}
              />
            </Suspense>
          </div>
        )}
      </Section>

      <Section title="Forecasts now">
        {forecast.error && <Alert text={forecast.error} />}
        {forecast.data && <ForecastTable forecast={forecast.data} />}
        {forecast.loading && !forecast.error && (
          <Note>
            Fitting the models, this takes a few seconds the first time…
          </Note>
        )}
      </Section>

      <Section title="How well each model has forecast">
        {evaluation.error && <Alert text={evaluation.error} />}
        {evaluation.data && (
          <>
            <Note>
              Out of sample on {evaluation.data.series},{' '}
              {fmtUtc(evaluation.data.window_start)} to{' '}
              {fmtUtc(evaluation.data.window_end)}. QLIKE is the loss (lower is
              better). A model is better than naive only if the whole interval
              of its QLIKE gain is above zero. Run{' '}
              <code className="text-slate-300">
                uv run python scripts/evaluate_vol_forecasts.py
              </code>{' '}
              to refresh it.
            </Note>
            <div
              role="tablist"
              aria-label="Forecast horizon"
              className="flex gap-2"
            >
              {evaluation.data.horizons.map((h) => (
                <button
                  key={h.horizon_hours}
                  role="tab"
                  type="button"
                  aria-selected={h.horizon_hours === horizon}
                  onClick={() => setHorizon(h.horizon_hours)}
                  className={`rounded border px-3 py-1 text-sm ${
                    h.horizon_hours === horizon
                      ? 'border-sky-500 bg-slate-800 text-white'
                      : 'border-slate-700 text-slate-300 hover:bg-slate-800'
                  }`}
                >
                  {horizonLabel(h.horizon_hours)}
                </button>
              ))}
            </div>
          </>
        )}
        {horizonReport && (
          <div className="overflow-x-auto rounded border border-slate-800">
            <table className="w-full min-w-[44rem] border-collapse text-sm">
              <caption className="sr-only">
                Forecast accuracy by model for the next {horizonLabel(horizon)},
                from {horizonReport.origins} scored forecasts.
              </caption>
              <thead className="bg-slate-900 text-slate-400">
                <tr>
                  <th scope="col" className="px-3 py-2 text-left font-normal">
                    Model ({horizonReport.origins} forecasts)
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">
                    QLIKE
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">
                    Mean error (vol pts)
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">
                    Calibration slope
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">
                    QLIKE gain vs naive (95% interval)
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-normal">
                    Verdict
                  </th>
                </tr>
              </thead>
              <tbody className="font-mono tabular-nums">
                {[...horizonReport.scores]
                  .sort((a, b) => a.qlike - b.qlike)
                  .map((score) => (
                    <tr
                      key={score.model}
                      className="border-t border-slate-800/70"
                    >
                      <th
                        scope="row"
                        className="px-3 py-2 text-left font-sans font-normal text-slate-200"
                      >
                        {modelLabel(score.model)}
                      </th>
                      <td className="px-3 py-2 text-right">
                        {fmtNumber(score.qlike)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {fmtPoints(score.bias_vol)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {fmtNumber(score.mz_slope, 2)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {score.verdict === 'baseline'
                          ? 'baseline'
                          : fmtGain(score)}
                      </td>
                      <td
                        className={`px-3 py-2 font-sans ${verdictClass(score.verdict)}`}
                      >
                        {score.verdict}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  )
}

function ForecastTable({ forecast }: { forecast: Forecast }) {
  const rows = forecastRows(forecast)
  const horizons = forecast.horizons.map((h) => h.horizon_hours)
  const realized = (hours: number) =>
    forecast.last_realized.find((r) => r.window_hours === hours)?.vol ?? null
  return (
    <>
      <Note>
        {forecast.instrument}, as of {fmtUtc(forecast.as_of)}: the annualized
        volatility each model expects over the next 24 hours, 7 days and 30
        days.
      </Note>
      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="w-full min-w-[32rem] border-collapse text-sm">
          <caption className="sr-only">
            Forecast annualized volatility by model and horizon
          </caption>
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th scope="col" className="px-3 py-2 text-left font-normal">
                Model
              </th>
              {horizons.map((h) => (
                <th
                  key={h}
                  scope="col"
                  className="px-3 py-2 text-right font-normal"
                >
                  Next {horizonLabel(h)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="font-mono tabular-nums">
            {rows.map((row) => (
              <tr key={row.model} className="border-t border-slate-800/70">
                <th
                  scope="row"
                  className="px-3 py-2 text-left font-sans font-normal text-slate-200"
                >
                  {modelLabel(row.model)}
                </th>
                {horizons.map((h) => (
                  <td key={h} className="px-3 py-2 text-right">
                    {fmtVol(row.values[h])}
                  </td>
                ))}
              </tr>
            ))}
            <tr className="border-t border-slate-700 text-slate-400">
              <th
                scope="row"
                className="px-3 py-2 text-left font-sans font-normal"
              >
                Realized over the last same-length window
              </th>
              {horizons.map((h) => (
                <td key={h} className="px-3 py-2 text-right">
                  {fmtVol(realized(h))}
                </td>
              ))}
            </tr>
            <tr className="text-slate-400">
              <th
                scope="row"
                className="px-3 py-2 text-left font-sans font-normal"
              >
                DVOL, 30-day implied
              </th>
              {horizons.map((h) => (
                <td key={h} className="px-3 py-2 text-right">
                  {h === 720 ? fmtVol(forecast.dvol) : dash}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <Note>
        GARCH forecasts head toward their fitted long-run volatility, which on
        this data sits well above recent realized volatility; the evaluation
        below shows how much that costs.
      </Note>
    </>
  )
}

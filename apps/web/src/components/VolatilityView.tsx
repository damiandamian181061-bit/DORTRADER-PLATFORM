import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { fmtUtc } from '../lib/chain'
import {
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
import type { Evaluation, Forecast, RealizedVol } from '../lib/vol'

const PlotlyChart = lazy(() => import('./PlotlyChart'))

const WINDOWS = [7, 30, 90]
const ESTIMATORS = ['close_to_close', 'yang_zhang']

function message(error: unknown): string {
  return error instanceof Error ? error.message : 'request failed'
}

function Alert({ text }: { text: string }) {
  return (
    <p
      role="alert"
      className="rounded border border-red-900 bg-red-950/40 px-3 py-2 text-sm text-red-200"
    >
      {text}
    </p>
  )
}

/** Fetches once per key and reports data, error and whether the request is still running. */
function useRemote<T>(load: (signal: AbortSignal) => Promise<T>, key: string) {
  const [state, setState] = useState<{
    key: string
    data: T | null
    error: string | null
  }>({
    key: '',
    data: null,
    error: null,
  })
  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
      .then((data) => setState({ key, data, error: null }))
      .catch((failure: unknown) => {
        if (!controller.signal.aborted)
          setState({ key, data: null, error: message(failure) })
      })
    return () => controller.abort()
    // `load` is rebuilt every render; the key says when the request really changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return { data: state.data, error: state.error, loading: state.key !== key }
}

export function VolatilityView() {
  const [windowDays, setWindowDays] = useState(30)
  const [horizon, setHorizon] = useState(24)

  const realized = useRemote<RealizedVol>(
    (signal) =>
      fetchRealized(
        { windowDays, days: 365, estimators: ESTIMATORS },
        fetch,
        signal,
      ),
    `realized-${windowDays}`,
  )
  const forecast = useRemote<Forecast>(
    (signal) => fetchForecast(fetch, signal),
    'forecast',
  )
  const evaluation = useRemote<Evaluation>(
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

  return (
    <div className="space-y-10">
      <section className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h2 className="text-lg font-medium">
            Realized volatility against DVOL
          </h2>
          <label className="flex flex-col gap-1 text-sm text-slate-400">
            Window
            <select
              value={windowDays}
              onChange={(event) => setWindowDays(Number(event.target.value))}
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
            >
              {WINDOWS.map((days) => (
                <option key={days} value={days}>
                  {days} days
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="text-sm text-slate-400">
          Annualized, from hourly {realized.data?.instrument ?? 'perpetual'}{' '}
          candles, one point per day. DVOL is Deribit&apos;s 30-day implied
          volatility index.
        </p>
        {realized.error && <Alert text={realized.error} />}
        {realized.loading && !realized.data && (
          <p className="text-sm text-slate-500">Loading chart…</p>
        )}
        {realized.data && (
          <div className={realized.loading ? 'opacity-60' : undefined}>
            <Suspense
              fallback={
                <p className="text-sm text-slate-500">Loading chart…</p>
              }
            >
              <PlotlyChart
                traces={traces}
                yTitle="annualized vol (%)"
                label="Realized volatility and DVOL over the last year"
              />
            </Suspense>
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Forecasts now</h2>
        {forecast.error && <Alert text={forecast.error} />}
        {forecast.data && <ForecastTable forecast={forecast.data} />}
        {forecast.loading && !forecast.data && !forecast.error && (
          <p className="text-sm text-slate-500">
            Fitting models, this takes a few seconds…
          </p>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">
          How well each model has forecast
        </h2>
        {evaluation.error && <Alert text={evaluation.error} />}
        {evaluation.data && (
          <>
            <p className="text-sm text-slate-400">
              Out of sample on {evaluation.data.series},{' '}
              {fmtUtc(evaluation.data.window_start)} to{' '}
              {fmtUtc(evaluation.data.window_end)}. QLIKE is the loss (lower is
              better). A model is better than naive only if the whole interval
              of its QLIKE gain is above zero.
            </p>
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
      </section>
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
      <p className="text-sm text-slate-400">
        As of {fmtUtc(forecast.as_of)}: the annualized volatility each model
        expects over the next 24 hours, 7 days and 30 days.
      </p>
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
    </>
  )
}

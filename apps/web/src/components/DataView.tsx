import { lazy, Suspense, useMemo, useState } from 'react'
import {
  fetchStatus,
  fmtAge,
  fmtPrice,
  fmtUtc,
  utcInputToIso,
} from '../lib/chain'
import {
  EVENT_KINDS,
  barTraces,
  eventSummary,
  expiryOptions,
  fetchBars,
  fetchCoverage,
  fetchEvents,
  fetchInstruments,
  filterInstruments,
  fmtTicks,
  seriesKey,
  stepLabel,
} from '../lib/data'
import type { InstrumentFilter, SeriesCoverage } from '../lib/data'
import { StatusStrip } from './StatusStrip'
import { Alert, Badge, Field, Note, Section, inputClass } from './ui'
import { useRemote } from './useRemote'

const PlotlyChart = lazy(() => import('./PlotlyChart'))
const MAX_INSTRUMENT_ROWS = 300

export function DataView() {
  const [selected, setSelected] = useState<string | null>(null)
  const coverage = useRemote(
    (signal) => fetchCoverage(fetch, signal),
    'coverage',
  )
  const rows = coverage.data ?? []
  const preferred =
    rows.find(
      (r) => r.instrument === 'BTC_USDC-PERPETUAL' && r.step_seconds === 3600,
    ) ?? rows[0]
  const active = rows.find((r) => seriesKey(r) === selected) ?? preferred

  return (
    <div className="space-y-12">
      <RecorderSection />
      <Section title="Candle coverage">
        <Note>
          What the backfill has stored. Holes are reported, never filled. To
          fetch or refresh candles run{' '}
          <code className="text-slate-300">
            uv run python -m apps.worker.backfill
          </code>
          .
        </Note>
        {coverage.error && <Alert text={coverage.error} />}
        {coverage.data && rows.length === 0 && (
          <Alert text="No candles stored yet. Run the backfill first." />
        )}
        {rows.length > 0 && (
          <CoverageTable
            rows={rows}
            active={active}
            onSelect={(r) => setSelected(seriesKey(r))}
          />
        )}
      </Section>
      {active && <CandlesSection series={active} />}
      <EventsSection />
      <InstrumentsSection />
    </div>
  )
}

function RecorderSection() {
  const [refresh, setRefresh] = useState(0)
  const status = useRemote(
    (signal) => fetchStatus(fetch, signal),
    `status-${refresh}`,
  )
  const s = status.data
  return (
    <Section title="Recorder">
      <StatusStrip status={s} />
      {status.error && <Alert text={status.error} />}
      {s && (
        <dl className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm sm:grid-cols-4">
          {[
            ['Run', s.run_id ?? '–'],
            ['Quotes per cycle', s.quotes_per_cycle?.toString() ?? '–'],
            ['Last good cycle', s.last_good_at ? fmtUtc(s.last_good_at) : '–'],
            ['Age', s.age_seconds === null ? '–' : fmtAge(s.age_seconds)],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="text-slate-500">{label}</dt>
              <dd className="font-mono text-slate-200">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <button
        type="button"
        onClick={() => setRefresh((n) => n + 1)}
        className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-200 hover:bg-slate-800"
      >
        Refresh
      </button>
    </Section>
  )
}

function CoverageTable({
  rows,
  active,
  onSelect,
}: {
  rows: SeriesCoverage[]
  active: SeriesCoverage | undefined
  onSelect: (row: SeriesCoverage) => void
}) {
  return (
    <div className="overflow-x-auto rounded border border-slate-800">
      <table className="w-full min-w-[42rem] border-collapse text-sm">
        <caption className="sr-only">
          Stored candle series. Select a row to chart it.
        </caption>
        <thead className="bg-slate-900 text-slate-400">
          <tr>
            {[
              'Series',
              'Candle',
              'First',
              'Last',
              'Candles',
              'Holes',
              'Missing',
            ].map((name, i) => (
              <th
                key={name}
                scope="col"
                className={`px-3 py-2 font-normal ${i < 2 ? 'text-left' : 'text-right'}`}
              >
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="font-mono tabular-nums">
          {rows.map((row) => {
            const isActive =
              active !== undefined && seriesKey(row) === seriesKey(active)
            return (
              <tr
                key={seriesKey(row)}
                className={`border-t border-slate-800/70 ${isActive ? 'bg-slate-800/60' : ''}`}
              >
                <th
                  scope="row"
                  className="px-3 py-2 text-left font-sans font-normal"
                >
                  <button
                    type="button"
                    onClick={() => onSelect(row)}
                    aria-pressed={isActive}
                    className="text-sky-300 hover:underline"
                  >
                    {row.instrument}
                  </button>
                </th>
                <td className="px-3 py-2 text-left font-sans">
                  {stepLabel(row.step_seconds)}
                </td>
                <td className="px-3 py-2 text-right">{fmtUtc(row.first)}</td>
                <td className="px-3 py-2 text-right">{fmtUtc(row.last)}</td>
                <td className="px-3 py-2 text-right">
                  {row.candles.toLocaleString('en-US')}
                </td>
                <td className="px-3 py-2 text-right">
                  {row.gaps === 0 ? '0' : <Badge tone="warn">{row.gaps}</Badge>}
                </td>
                <td className="px-3 py-2 text-right">{row.missing}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const RANGES = [30, 90, 365, 1500]

function CandlesSection({ series }: { series: SeriesCoverage }) {
  const [days, setDays] = useState(90)
  const key = `${seriesKey(series)}-${days}`
  const bars = useRemote(
    (signal) =>
      fetchBars(
        {
          instrument: series.instrument,
          stepSeconds: series.step_seconds,
          days,
        },
        fetch,
        signal,
      ),
    key,
  )
  const traces = useMemo(
    () => (bars.data ? barTraces(bars.data) : []),
    [bars.data],
  )
  const last = bars.data?.points[bars.data.points.length - 1]
  return (
    <Section
      title={`${series.instrument}, ${stepLabel(series.step_seconds)} candles`}
    >
      <div className="flex flex-wrap items-end gap-4">
        <Field label="Range">
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
        {last && (
          <Note>
            Latest candle {fmtUtc(last.time)}, close{' '}
            <span className="font-mono text-slate-200">
              {fmtPrice(last.close)}
            </span>
            {bars.data && bars.data.stride > 1
              ? `. Showing every ${bars.data.stride}th candle.`
              : '.'}
          </Note>
        )}
      </div>
      {bars.error && <Alert text={bars.error} />}
      {bars.data && (
        <Suspense fallback={<Note>Loading chart…</Note>}>
          <PlotlyChart
            traces={traces}
            yTitle={
              series.instrument.startsWith('DVOL') ? 'index points' : 'USDC'
            }
            label={`${series.instrument} close over the last ${days} days`}
            fromZero={series.instrument.startsWith('DVOL')}
          />
        </Suspense>
      )}
    </Section>
  )
}

function EventsSection() {
  const [kind, setKind] = useState('')
  const [days, setDays] = useState(7)
  const events = useRemote(
    (signal) =>
      fetchEvents({ kind: kind || undefined, days, limit: 200 }, fetch, signal),
    `events-${kind}-${days}`,
  )
  const list = events.data ?? []
  return (
    <Section title="Recorder events">
      <div className="flex flex-wrap items-end gap-4">
        <Field label="Kind">
          <select
            className={inputClass}
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="">all</option>
            {EVENT_KINDS.map((k) => (
              <option key={k} value={k}>
                {k.replace('_', ' ')}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Window">
          <select
            className={inputClass}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            {[1, 7, 30, 90].map((n) => (
              <option key={n} value={n}>
                last {n} days
              </option>
            ))}
          </select>
        </Field>
        <Note>
          Gaps, failed cycles, unexpected exchange fields and catalog changes,
          newest first.
        </Note>
      </div>
      {events.error && <Alert text={events.error} />}
      {events.data && list.length === 0 && (
        <Note>No events in this window.</Note>
      )}
      {list.length > 0 && (
        <div className="overflow-x-auto rounded border border-slate-800">
          <table className="w-full min-w-[40rem] border-collapse text-sm">
            <caption className="sr-only">Recorder events</caption>
            <thead className="bg-slate-900 text-slate-400">
              <tr>
                <th scope="col" className="px-3 py-2 text-left font-normal">
                  Time
                </th>
                <th scope="col" className="px-3 py-2 text-left font-normal">
                  Kind
                </th>
                <th scope="col" className="px-3 py-2 text-left font-normal">
                  What happened
                </th>
              </tr>
            </thead>
            <tbody>
              {list.map((event, index) => (
                <tr
                  key={`${event.at}-${index}`}
                  className="border-t border-slate-800/70"
                >
                  <td className="whitespace-nowrap px-3 py-2 font-mono tabular-nums">
                    {fmtUtc(event.at)}
                  </td>
                  <td className="px-3 py-2">
                    <Badge
                      tone={
                        event.kind === 'error'
                          ? 'bad'
                          : event.kind === 'gap'
                            ? 'warn'
                            : 'plain'
                      }
                    >
                      {event.kind.replace('_', ' ')}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 text-slate-300">
                    {eventSummary(event)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}

function InstrumentsSection() {
  const [timeInput, setTimeInput] = useState('')
  const [filter, setFilter] = useState<InstrumentFilter>({
    text: '',
    optionType: '',
    expiry: '',
  })
  const at = utcInputToIso(timeInput)
  const instruments = useRemote(
    (signal) => fetchInstruments(at, fetch, signal),
    `instruments-${at ?? 'latest'}`,
  )
  const all = useMemo(() => instruments.data ?? [], [instruments.data])
  const expiries = useMemo(() => expiryOptions(all), [all])
  const shown = useMemo(() => filterInstruments(all, filter), [all, filter])
  return (
    <Section title="Instrument catalog">
      <div className="flex flex-wrap items-end gap-4">
        <Field label="Listed at (UTC)">
          <input
            type="datetime-local"
            className={inputClass}
            value={timeInput}
            onChange={(e) => setTimeInput(e.target.value)}
          />
        </Field>
        <Field label="Search">
          <input
            className={inputClass}
            placeholder="name or strike"
            value={filter.text}
            onChange={(e) => setFilter({ ...filter, text: e.target.value })}
          />
        </Field>
        <Field label="Type">
          <select
            className={inputClass}
            value={filter.optionType}
            onChange={(e) =>
              setFilter({
                ...filter,
                optionType: e.target.value as InstrumentFilter['optionType'],
              })
            }
          >
            <option value="">calls and puts</option>
            <option value="call">calls</option>
            <option value="put">puts</option>
          </select>
        </Field>
        <Field label="Expiry">
          <select
            className={inputClass}
            value={filter.expiry}
            onChange={(e) => setFilter({ ...filter, expiry: e.target.value })}
          >
            <option value="">all expiries</option>
            {expiries.map((e) => (
              <option key={e} value={e}>
                {fmtUtc(e)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Note>
        Instruments that were listed and quoted at that time, with the specs
        read from the exchange.
        {instruments.data && ` ${shown.length} of ${all.length} shown.`}
      </Note>
      {instruments.error && <Alert text={instruments.error} />}
      {shown.length > 0 && (
        <div className="overflow-x-auto rounded border border-slate-800">
          <table className="w-full min-w-[60rem] border-collapse text-xs">
            <caption className="sr-only">Listed instruments</caption>
            <thead className="bg-slate-900 text-slate-400">
              <tr>
                {[
                  'Instrument',
                  'Type',
                  'Strike',
                  'Expiry',
                  'Tick (USDC)',
                  'Min size',
                  'Contract',
                  'State',
                  'Taker fee',
                ].map((name) => (
                  <th
                    key={name}
                    scope="col"
                    className="px-2 py-2 text-left font-normal"
                  >
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="font-mono tabular-nums">
              {shown.slice(0, MAX_INSTRUMENT_ROWS).map((i) => (
                <tr
                  key={i.instrument_id}
                  className="border-t border-slate-800/70"
                >
                  <th
                    scope="row"
                    className="px-2 py-1 text-left font-normal text-slate-200"
                  >
                    {i.name}
                  </th>
                  <td className="px-2 py-1">{i.option_type}</td>
                  <td className="px-2 py-1">
                    {i.strike.toLocaleString('en-US')}
                  </td>
                  <td className="px-2 py-1">{fmtUtc(i.expiration)}</td>
                  <td className="px-2 py-1">{fmtTicks(i)}</td>
                  <td className="px-2 py-1">{i.min_trade_amount} BTC</td>
                  <td className="px-2 py-1">{i.contract_size} BTC</td>
                  <td className="px-2 py-1">{i.state}</td>
                  <td className="px-2 py-1">
                    {i.taker_commission === null
                      ? '–'
                      : `${(i.taker_commission * 100).toFixed(2)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {shown.length > MAX_INSTRUMENT_ROWS && (
        <Note>
          Showing the first {MAX_INSTRUMENT_ROWS}; narrow the filters to see the
          rest.
        </Note>
      )}
    </Section>
  )
}

import { describe, expect, it } from 'vitest'
import {
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
} from './data'
import type { InstrumentInfo, RecorderEvent } from './data'

const instrument = (
  name: string,
  strike: number,
  type: 'call' | 'put',
  expiration: string,
): InstrumentInfo => ({
  instrument_id: 1,
  name,
  option_type: type,
  strike,
  expiration,
  created_at: '2026-10-01T00:00:00Z',
  contract_size: 1,
  tick_size: 5,
  tick_size_steps: [{ above_price: 1000, tick_size: 20 }],
  min_trade_amount: 0.01,
  state: 'open',
  is_active: true,
  maker_commission: 0.0003,
  taker_commission: 0.0003,
})

const list = [
  instrument('BTC_USDC-20OCT26-81000-C', 81000, 'call', '2026-10-20T08:00:00Z'),
  instrument('BTC_USDC-20OCT26-81000-P', 81000, 'put', '2026-10-20T08:00:00Z'),
  instrument('BTC_USDC-27NOV26-90000-C', 90000, 'call', '2026-11-27T08:00:00Z'),
]

describe('labels', () => {
  it('names candle sizes and series', () => {
    expect(stepLabel(60)).toBe('1 minute')
    expect(stepLabel(3600)).toBe('1 hour')
    expect(stepLabel(7200)).toBe('2 hours')
    expect(stepLabel(86400)).toBe('1 day')
    expect(stepLabel(45)).toBe('45 s')
    expect(seriesKey({ instrument: 'X', step_seconds: 3600 })).toBe('X|3600')
  })

  it('draws candles as a close line, and DVOL as an index', () => {
    const bars = {
      instrument: 'BTC-PERPETUAL',
      step_seconds: 3600,
      stride: 1,
      points: [
        { time: 't1', open: 1, high: 3, low: 1, close: 2, volume: 1 },
        { time: 't2', open: 2, high: 4, low: 2, close: 3, volume: null },
      ],
    }

    expect(barTraces(bars)[0]).toMatchObject({
      name: 'Close, 1 hour candles',
      x: ['t1', 't2'],
      y: [2, 3],
    })
    expect(barTraces({ ...bars, instrument: 'DVOL-BTC' })[0].name).toContain(
      'DVOL index',
    )
  })
})

describe('eventSummary', () => {
  const event = (
    kind: string,
    detail: Record<string, unknown>,
  ): RecorderEvent => ({
    at: 't',
    run_id: 'r',
    kind,
    detail,
  })

  it('describes each kind of recorder event', () => {
    expect(eventSummary(event('gap', { seconds: 400 }))).toBe(
      '7 min without data',
    )
    expect(
      eventSummary(event('error', { where: 'cycle', error: 'boom' })),
    ).toBe('cycle: boom')
    expect(
      eventSummary(
        event('schema_drift', {
          what: 'instrument',
          unexpected_fields: ['a', 'b'],
        }),
      ),
    ).toBe('unexpected fields in instrument: a, b')
    expect(
      eventSummary(
        event('catalog_change', {
          added: ['x'],
          removed: [],
          state_changes: [{}],
          changed: [],
        }),
      ),
    ).toBe('1 listed, 0 delisted, 1 state changes, 0 changed')
    expect(eventSummary(event('other', { a: 1 }))).toBe('{"a":1}')
  })
})

describe('instrument browser helpers', () => {
  it('filters by text, type and expiry together', () => {
    const all = { text: '', optionType: '' as const, expiry: '' }

    expect(filterInstruments(list, all)).toHaveLength(3)
    expect(
      filterInstruments(list, { ...all, optionType: 'put' }).map((i) => i.name),
    ).toEqual(['BTC_USDC-20OCT26-81000-P'])
    expect(filterInstruments(list, { ...all, text: ' 90000 ' })).toHaveLength(1)
    expect(filterInstruments(list, { ...all, text: 'nov' })).toHaveLength(1)
    expect(
      filterInstruments(list, { ...all, expiry: '2026-10-20T08:00:00Z' }),
    ).toHaveLength(2)
    expect(
      filterInstruments(list, { text: 'nov', optionType: 'put', expiry: '' }),
    ).toHaveLength(0)
  })

  it('lists expiries once, in order, and spells out tick steps', () => {
    expect(expiryOptions(list)).toEqual([
      '2026-10-20T08:00:00Z',
      '2026-11-27T08:00:00Z',
    ])
    expect(fmtTicks(list[0])).toBe('5, then 20 above 1,000')
    expect(fmtTicks({ ...list[0], tick_size_steps: [] })).toBe('5')
  })
})

describe('api client', () => {
  it('builds the query strings', async () => {
    const urls: string[] = []
    const fake: typeof fetch = async (input) => {
      urls.push(String(input))
      return Response.json([])
    }

    await fetchCoverage(fake)
    await fetchBars(
      { instrument: 'BTC-PERPETUAL', stepSeconds: 3600, days: 90 },
      fake,
    )
    await fetchEvents({ kind: 'gap', days: 7, limit: 50 }, fake)
    await fetchEvents({ days: 1, limit: 5 }, fake)
    await fetchInstruments(undefined, fake)
    await fetchInstruments('2026-10-18T19:17:00Z', fake)

    expect(urls).toEqual([
      '/api/bars/coverage',
      '/api/bars?instrument=BTC-PERPETUAL&step_seconds=3600&days=90',
      '/api/events?days=7&limit=50&kind=gap',
      '/api/events?days=1&limit=5',
      '/api/instruments',
      '/api/instruments?at=2026-10-18T19%3A17%3A00Z',
    ])
  })
})

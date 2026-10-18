import { describe, expect, it } from 'vitest'
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
} from './vol'
import type { Forecast, ModelScore, RealizedVol } from './vol'

const realized: RealizedVol = {
  instrument: 'BTC_USDC-PERPETUAL',
  window_days: 30,
  estimators: ['close_to_close', 'yang_zhang'],
  series: [
    {
      estimator: 'close_to_close',
      points: [
        { time: '2026-10-01T00:00:00Z', vol: null },
        { time: '2026-10-02T00:00:00Z', vol: 0.45 },
      ],
    },
    {
      estimator: 'yang_zhang',
      points: [{ time: '2026-10-02T00:00:00Z', vol: 0.4 }],
    },
  ],
  dvol: [{ time: '2026-10-02T00:00:00Z', vol: 0.5 }],
}

describe('toTraces', () => {
  it('turns decimals into percent, keeps gaps as null and appends DVOL', () => {
    const traces = toTraces(realized)

    expect(traces.map((t) => t.name)).toEqual([
      'Close-to-close, 30-day',
      'Yang-Zhang, 30-day',
      'DVOL (30-day implied)',
    ])
    expect(traces[0].y).toEqual([null, 45])
    expect(traces[2].y).toEqual([50])
    expect(traces[2].dash).toBe('dot')
  })

  it('leaves DVOL out when there is none', () => {
    const traces = toTraces({
      ...realized,
      dvol: [{ time: '2026-10-02T00:00:00Z', vol: null }],
    })

    expect(traces).toHaveLength(2)
  })
})

describe('forecastRows', () => {
  const forecast: Forecast = {
    instrument: 'X',
    as_of: '2026-10-18T20:00:00Z',
    last_realized: [],
    dvol: 0.44,
    horizons: [
      {
        horizon_hours: 24,
        forecasts: [
          { model: 'naive', vol: 0.5 },
          { model: 'har', vol: 0.55 },
        ],
      },
      { horizon_hours: 168, forecasts: [{ model: 'naive', vol: 0.48 }] },
    ],
  }

  it('gives one row per model with null where a horizon has no forecast', () => {
    expect(forecastRows(forecast)).toEqual([
      { model: 'naive', values: { 24: 0.5, 168: 0.48 } },
      { model: 'har', values: { 24: 0.55, 168: null } },
    ])
  })
})

describe('formatting', () => {
  it('formats volatilities, points and gains', () => {
    expect(fmtVol(0.4412)).toBe('44.1%')
    expect(fmtVol(null)).toBe(dash)
    expect(fmtPoints(0.015)).toBe('+1.5')
    expect(fmtPoints(-0.02)).toBe('-2.0')
    expect(fmtNumber(1.0504)).toBe('1.050')
    expect(fmtNumber(null)).toBe(dash)
    const score = {
      gain_vs_naive: 0.1192,
      gain_low: -0.007,
      gain_high: 0.354,
    } as ModelScore
    expect(fmtGain(score)).toBe('+0.119 (-0.007, +0.354)')
    expect(fmtGain({ ...score, gain_vs_naive: null })).toBe(dash)
  })

  it('labels horizons and models', () => {
    expect(horizonLabel(24)).toBe('24 hours')
    expect(horizonLabel(168)).toBe('7 days')
    expect(horizonLabel(720)).toBe('30 days')
    expect(horizonLabel(5)).toBe('5 hours')
    expect(modelLabel('gjr_garch')).toBe('GJR-GARCH(1,1)')
    expect(modelLabel('mystery')).toBe('mystery')
  })

  it('colours verdicts', () => {
    expect(verdictClass('better than naive')).toContain('emerald')
    expect(verdictClass('worse than naive')).toContain('red')
    expect(verdictClass('no clear difference')).toContain('slate')
  })
})

describe('api client', () => {
  it('sends window, days and each estimator as query parameters', async () => {
    const urls: string[] = []
    const fake: typeof fetch = async (input) => {
      urls.push(String(input))
      return Response.json({})
    }

    await fetchRealized(
      {
        windowDays: 30,
        days: 365,
        estimators: ['close_to_close', 'parkinson'],
      },
      fake,
    )
    await fetchForecast(fake)
    await fetchEvaluation(fake)

    expect(urls).toEqual([
      '/api/vol/realized?window_days=30&days=365&estimators=close_to_close&estimators=parkinson',
      '/api/vol/forecast',
      '/api/vol/evaluation',
    ])
  })

  it("surfaces the API's own message", async () => {
    const fake: typeof fetch = async () =>
      Response.json({ detail: 'no forecast evaluation yet' }, { status: 404 })

    await expect(fetchEvaluation(fake)).rejects.toThrow(
      'no forecast evaluation yet',
    )
  })
})

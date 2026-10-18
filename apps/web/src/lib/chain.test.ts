import { describe, expect, it } from 'vitest'
import {
  dash,
  fetchChain,
  fetchStatus,
  flagLabel,
  fmtAge,
  fmtDays,
  fmtIv,
  fmtPrice,
  fmtSigned,
  fmtUtc,
  hasSeriousFlag,
  isoToUtcInput,
  nearestStrikeIndex,
  utcInputToIso,
} from './chain'
import type { StrikeRow } from './chain'

const row = (strike: number): StrikeRow => ({ strike, call: null, put: null })

describe('utcInputToIso', () => {
  it('reads a datetime-local value as UTC', () => {
    expect(utcInputToIso('2026-10-18T19:17')).toBe('2026-10-18T19:17:00Z')
    expect(utcInputToIso('2026-10-18T19:17:45')).toBe('2026-10-18T19:17:45Z')
  })

  it('returns undefined for empty or malformed input', () => {
    expect(utcInputToIso('')).toBeUndefined()
    expect(utcInputToIso('yesterday')).toBeUndefined()
    expect(utcInputToIso('2026-10-18')).toBeUndefined()
  })

  it('round-trips with isoToUtcInput', () => {
    expect(utcInputToIso(isoToUtcInput('2026-10-18T19:17:39Z'))).toBe(
      '2026-10-18T19:17:00Z',
    )
  })
})

describe('formatting', () => {
  it('shows a dash for missing values', () => {
    expect(fmtPrice(null)).toBe(dash)
    expect(fmtIv(null)).toBe(dash)
    expect(fmtSigned(null)).toBe(dash)
  })

  it('formats prices, vols and signed numbers', () => {
    expect(fmtPrice(81118.61)).toBe('81,119')
    expect(fmtPrice(12.345)).toBe('12.35')
    expect(fmtIv(0.3651)).toBe('36.5')
    expect(fmtSigned(0.004)).toBe('+0.00')
    expect(fmtSigned(-1.234)).toBe('-1.23')
  })

  it('formats ages, times to expiry and timestamps', () => {
    expect(fmtAge(12)).toBe('12 s')
    expect(fmtAge(300)).toBe('5 min')
    expect(fmtAge(7200)).toBe('2.0 h')
    expect(fmtDays(0.5 / 365)).toBe('12.0 h')
    expect(fmtDays(1.529 / 365)).toBe('1.53 d')
    expect(fmtUtc('2026-10-18T19:17:39Z')).toBe('2026-10-18 19:17 UTC')
  })
})

describe('nearestStrikeIndex', () => {
  it('finds the strike closest to the forward', () => {
    const strikes = [row(80000), row(81000), row(82000)]
    expect(nearestStrikeIndex(strikes, 81400)).toBe(1)
    expect(nearestStrikeIndex(strikes, 99999)).toBe(2)
  })

  it('returns -1 without a forward or strikes', () => {
    expect(nearestStrikeIndex([row(80000)], null)).toBe(-1)
    expect(nearestStrikeIndex([], 81000)).toBe(-1)
  })
})

describe('flags', () => {
  it('labels known flags and passes unknown ones through', () => {
    expect(flagLabel('parity_violation')).toBe(
      'Call and put break put-call parity',
    )
    expect(flagLabel('brand_new_flag')).toBe('brand_new_flag')
  })

  it('treats one-sided quotes as ordinary and rule violations as serious', () => {
    expect(hasSeriousFlag(['no_bid', 'wide_spread'])).toBe(false)
    expect(hasSeriousFlag(['no_bid', 'butterfly_violation'])).toBe(true)
  })
})

describe('api client', () => {
  it('sends the time and expiry count as query parameters', async () => {
    const urls: string[] = []
    const fake: typeof fetch = async (input) => {
      urls.push(String(input))
      return Response.json({ expiries: [] })
    }

    await fetchChain({ at: '2026-10-18T19:17:00Z', expiries: 2 }, fake)
    await fetchChain({ expiries: 3 }, fake)

    expect(urls[0]).toBe('/api/chain?expiries=2&at=2026-10-18T19%3A17%3A00Z')
    expect(urls[1]).toBe('/api/chain?expiries=3')
  })

  it("surfaces the API's own error message", async () => {
    const fake: typeof fetch = async () =>
      Response.json(
        { detail: 'nothing has been recorded yet' },
        { status: 404 },
      )

    await expect(fetchChain({ expiries: 3 }, fake)).rejects.toThrow(
      'nothing has been recorded yet',
    )
  })

  it('falls back to the status code when the body is not JSON', async () => {
    const fake: typeof fetch = async () => new Response('oops', { status: 502 })

    await expect(fetchStatus(fake)).rejects.toThrow('request failed: 502')
  })
})

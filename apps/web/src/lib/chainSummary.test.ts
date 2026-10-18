import { describe, expect, it } from 'vitest'
import { summarizeChain } from './chain'
import type { Chain, Contract } from './chain'

const contract = (overrides: Partial<Contract> = {}): Contract => ({
  instrument_name: 'X',
  bid: 1,
  ask: 2,
  mark: 1.5,
  quote_age_seconds: 1,
  open_interest: 1,
  bid_iv: null,
  ask_iv: null,
  mid_iv: null,
  mark_iv: 0.4,
  deribit_mark_iv: 0.4,
  iv_diff_points: 0.02,
  iv_spread_points: null,
  delta: 0.5,
  gamma: null,
  vega: null,
  theta: null,
  flags: [],
  ...overrides,
})

const chain = (expiries: Chain['expiries']): Chain => ({
  snapshot_at: '2026-10-18T19:17:00Z',
  model_version: 'black76-1',
  inputs_hash: 'h',
  listed_without_quote: 0,
  quotes_without_instrument: 0,
  expiries,
})

describe('summarizeChain', () => {
  it('counts contracts, solved vols, flags, and compares only out-of-the-money IVs', () => {
    const summary = summarizeChain(
      chain([
        {
          expiration: '2026-10-20T08:00:00Z',
          forward: 81000,
          years_to_expiry: 0.005,
          strikes: [
            // call below the forward is in the money: its 13.7-point gap must not count
            {
              strike: 80000,
              call: contract({ iv_diff_points: 13.7 }),
              put: contract({ iv_diff_points: -0.04 }),
            },
            {
              strike: 82000,
              call: contract({ iv_diff_points: 0.1, flags: ['no_bid'] }),
              put: contract({
                mark_iv: null,
                iv_diff_points: null,
                flags: ['no_bid', 'wide_spread'],
              }),
            },
            { strike: 83000, call: null, put: null },
          ],
        },
      ]),
    )

    expect(summary).toMatchObject({
      contracts: 4,
      withVol: 3,
      flagged: 2,
      flagCounts: { no_bid: 2, wide_spread: 1 },
      otmCompared: 2, // the 80000 put and the 82000 call
      otmMaxAbsDiff: 0.1,
    })
    expect(summary.otmMedianAbsDiff).toBeCloseTo(0.07)
  })

  it('reports nothing to compare rather than zero', () => {
    const empty = summarizeChain(chain([]))

    expect(empty).toMatchObject({
      contracts: 0,
      otmCompared: 0,
      otmMedianAbsDiff: null,
      otmMaxAbsDiff: null,
    })
  })

  it('cannot tell out-of-the-money without a forward', () => {
    const summary = summarizeChain(
      chain([
        {
          expiration: 'x',
          forward: null,
          years_to_expiry: 1,
          strikes: [{ strike: 1, call: contract(), put: contract() }],
        },
      ]),
    )

    expect(summary.contracts).toBe(2)
    expect(summary.otmCompared).toBe(0)
  })
})

import { describe, expect, it } from 'vitest'
import { hashForView, viewFromHash } from './route'

describe('routing by hash', () => {
  it('reads known views and defaults to the chain', () => {
    expect(viewFromHash('#/pricing')).toBe('pricing')
    expect(viewFromHash('#data')).toBe('data')
    expect(viewFromHash('')).toBe('chain')
    expect(viewFromHash('#/nonsense')).toBe('chain')
  })

  it('round-trips every view', () => {
    for (const view of ['chain', 'volatility', 'pricing', 'data'] as const) {
      expect(viewFromHash(hashForView(view))).toBe(view)
    }
  })
})

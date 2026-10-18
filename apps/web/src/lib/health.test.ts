import { describe, expect, it } from 'vitest'
import { fetchHealth } from './health'

describe('fetchHealth', () => {
  it('returns the status from the API', async () => {
    const fakeFetch = async () => Response.json({ status: 'ok' })

    await expect(fetchHealth(fakeFetch)).resolves.toBe('ok')
  })

  it('throws when the API responds with an error', async () => {
    const fakeFetch = async () => new Response(null, { status: 500 })

    await expect(fetchHealth(fakeFetch)).rejects.toThrow(
      'health check failed: 500',
    )
  })
})

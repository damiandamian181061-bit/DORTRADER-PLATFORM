import { describe, expect, it } from 'vitest'
import { detailMessage, getJson, postJson } from './api'

describe('detailMessage', () => {
  it('uses a string detail as is', () => {
    expect(
      detailMessage({ detail: 'nothing has been recorded yet' }, 404),
    ).toBe('nothing has been recorded yet')
  })

  it('turns FastAPI validation issues into field: message pairs', () => {
    const body = {
      detail: [
        {
          loc: ['body', 'days_to_expiry'],
          msg: 'Input should be greater than 0',
        },
        { loc: ['query', 'days'], msg: 'Input should be a valid integer' },
        { msg: 'Value error' },
      ],
    }

    expect(detailMessage(body, 422)).toBe(
      'days_to_expiry: Input should be greater than 0; days: Input should be a valid integer; Value error',
    )
  })

  it('falls back to the status when there is nothing readable', () => {
    expect(detailMessage(null, 502)).toBe('request failed: 502')
    expect(detailMessage({ detail: [] }, 500)).toBe('request failed: 500')
  })
})

describe('json helpers', () => {
  it('posts JSON with a content type and reads the reply', async () => {
    let seen: RequestInit | undefined
    const fake: typeof fetch = async (_input, init) => {
      seen = init
      return Response.json({ ok: true })
    }

    const result = await postJson<{ ok: boolean }>('/x', { a: 1 }, fake)

    expect(result.ok).toBe(true)
    expect(seen?.method).toBe('POST')
    expect(seen?.body).toBe('{"a":1}')
    expect(new Headers(seen?.headers).get('content-type')).toBe(
      'application/json',
    )
  })

  it('raises the readable message for a failed request', async () => {
    const fake: typeof fetch = async () =>
      Response.json(
        { detail: [{ loc: ['body', 'value'], msg: 'bad' }] },
        { status: 422 },
      )

    await expect(postJson('/x', {}, fake)).rejects.toThrow('value: bad')
    await expect(getJson('/x', fake)).rejects.toThrow('value: bad')
  })
})

import { describe, expect, it } from 'vitest'
import {
  EXAMPLE_FORM,
  fetchPrice,
  formFromContract,
  fmtSig,
  formToRequest,
  statusText,
} from './pricing'

describe('formToRequest', () => {
  it('converts the example form, with the rate in percent turned into a decimal', () => {
    expect(formToRequest({ ...EXAMPLE_FORM, ratePercent: '5' })).toEqual({
      forward: 81000,
      strike: 80000,
      days_to_expiry: 30,
      option_type: 'put',
      rate: 0.05,
      mode: 'vol',
      value: 40,
      quantity: 1,
    })
  })

  it('names the first field that is missing or not a number', () => {
    expect(formToRequest({ ...EXAMPLE_FORM, forward: '' })).toBe(
      'Forward is required',
    )
    expect(formToRequest({ ...EXAMPLE_FORM, strike: 'abc' })).toBe(
      'Strike must be a number',
    )
    expect(formToRequest({ ...EXAMPLE_FORM, mode: 'price', value: '' })).toBe(
      'Premium is required',
    )
    expect(formToRequest({ ...EXAMPLE_FORM, value: '' })).toBe(
      'Volatility is required',
    )
  })

  it('sends the tick only when solving from a price', () => {
    const priceForm = {
      ...EXAMPLE_FORM,
      mode: 'price' as const,
      value: '500',
      tick: '5',
    }

    expect(formToRequest(priceForm)).toMatchObject({ mode: 'price', tick: 5 })
    expect(formToRequest({ ...priceForm, mode: 'vol' })).not.toHaveProperty(
      'tick',
    )
    expect(formToRequest({ ...priceForm, tick: 'x' })).toBe(
      'Tick must be a number',
    )
  })
})

describe('formFromContract', () => {
  it('prefills the calculator from a chain contract in price mode', () => {
    const form = formFromContract({
      optionType: 'call',
      forward: 80885.4321,
      strike: 81000,
      yearsToExpiry: 0.52 / 365,
      mark: 391.19,
    })

    expect(form).toMatchObject({
      optionType: 'call',
      forward: '80885.43',
      strike: '81000',
      days: '0.52',
      mode: 'price',
      value: '391.19',
      tick: '5',
    })
    expect(
      formFromContract({
        optionType: 'put',
        forward: null,
        strike: 1,
        yearsToExpiry: 1,
        mark: 1500,
      }),
    ).toMatchObject({ forward: '', tick: '20' })
  })
})

describe('api and wording', () => {
  it('posts the request as JSON', async () => {
    let body = ''
    const fake: typeof fetch = async (_input, init) => {
      body = String(init?.body)
      return Response.json({ vol: 0.4 })
    }

    await fetchPrice(
      { ...(formToRequest(EXAMPLE_FORM) as object) } as never,
      fake,
    )

    expect(JSON.parse(body)).toMatchObject({ strike: 80000 })
  })

  it('explains solver statuses and passes unknown ones through', () => {
    expect(statusText('BELOW_INTRINSIC')).toContain('below intrinsic')
    expect(statusText('OK')).toBe('solved')
    expect(statusText('SOMETHING_NEW')).toBe('SOMETHING_NEW')
  })
})

describe('fmtSig', () => {
  it('keeps significant figures and avoids scientific notation', () => {
    expect(fmtSig(0.000113177, 3)).toBe('0.000113')
    expect(fmtSig(-135.913, 4)).toBe('-135.9')
    expect(fmtSig(33.808226, 4)).toBe('33.81')
    expect(fmtSig(1234567.89, 4)).toBe('1,235,000')
    expect(fmtSig(null)).toBe('–')
  })
})

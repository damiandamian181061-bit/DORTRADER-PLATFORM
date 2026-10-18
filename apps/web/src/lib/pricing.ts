import { postJson } from './api'
import type { Fetch } from './api'

export type OptionType = 'call' | 'put'
export type Mode = 'vol' | 'price'

/** The calculator's inputs as typed: strings, so a half-typed number is not an error yet. */
export type PricingForm = {
  optionType: OptionType
  forward: string
  strike: string
  days: string
  ratePercent: string
  mode: Mode
  value: string // vol in percent, or premium in USDC
  quantity: string
  tick: string
}

export type PriceRequest = {
  forward: number
  strike: number
  days_to_expiry: number
  option_type: OptionType
  rate: number
  mode: Mode
  value: number
  quantity: number
  tick?: number
}

export type GreeksView = {
  price: number
  delta: number
  gamma: number
  vega: number
  theta: number
  vanna: number
  volga: number
}

export type PriceResult = {
  model_version: string
  years_to_expiry: number
  vol: number | null
  vol_status: string
  intrinsic: number
  time_value: number | null
  per_contract: GreeksView | null
  position: GreeksView | null
  theta_deribit_style: number | null
  iv_interval: { low: number | null; high: number | null } | null
}

export const EXAMPLE_FORM: PricingForm = {
  optionType: 'put',
  forward: '81000',
  strike: '80000',
  days: '30',
  ratePercent: '0',
  mode: 'vol',
  value: '40',
  quantity: '1',
  tick: '',
}

/** The request for a form, or why it cannot be sent yet. */
export function formToRequest(form: PricingForm): PriceRequest | string {
  const read = (label: string, text: string): number | string => {
    if (text.trim() === '') return `${label} is required`
    const value = Number(text)
    return Number.isFinite(value) ? value : `${label} must be a number`
  }
  const forward = read('Forward', form.forward)
  const strike = read('Strike', form.strike)
  const days = read('Days to expiry', form.days)
  const rate = read('Rate', form.ratePercent)
  const value = read(form.mode === 'vol' ? 'Volatility' : 'Premium', form.value)
  const quantity = read('Quantity', form.quantity)
  for (const field of [forward, strike, days, rate, value, quantity]) {
    if (typeof field === 'string') return field
  }
  const request: PriceRequest = {
    forward: forward as number,
    strike: strike as number,
    days_to_expiry: days as number,
    option_type: form.optionType,
    rate: (rate as number) / 100,
    mode: form.mode,
    value: value as number,
    quantity: quantity as number,
  }
  if (form.mode === 'price' && form.tick.trim() !== '') {
    const tick = read('Tick', form.tick)
    if (typeof tick === 'string') return tick
    request.tick = tick
  }
  return request
}

export function fetchPrice(
  request: PriceRequest,
  fetchImpl: Fetch = fetch,
  signal?: AbortSignal,
): Promise<PriceResult> {
  return postJson<PriceResult>('/api/price', request, fetchImpl, signal)
}

/** A contract picked on the chain page, carried over to the calculator. */
export type ContractPick = {
  optionType: OptionType
  forward: number | null
  strike: number
  yearsToExpiry: number
  mark: number
}

export function formFromContract(pick: ContractPick): PricingForm {
  return {
    ...EXAMPLE_FORM,
    optionType: pick.optionType,
    forward:
      pick.forward === null ? '' : String(Math.round(pick.forward * 100) / 100),
    strike: String(pick.strike),
    days: String(Math.round(pick.yearsToExpiry * 365 * 10_000) / 10_000),
    mode: 'price',
    value: String(pick.mark),
    tick: pick.mark > 1000 ? '20' : '5',
  }
}

const STATUS_TEXT: Record<string, string> = {
  given: 'as entered',
  OK: 'solved',
  BELOW_INTRINSIC:
    'price is below intrinsic value, so no volatility explains it',
  AT_INTRINSIC:
    'price equals intrinsic value, so volatility is indistinguishable from zero',
  ABOVE_UPPER_BOUND: 'price is at or above the maximum an option can be worth',
  NO_BRACKET: 'would need a volatility above 2000%',
  NOT_CONVERGED: 'the solver did not converge',
  INVALID_INPUT: 'the inputs are not valid',
}

export function statusText(status: string): string {
  return STATUS_TEXT[status] ?? status
}

/** A number to `digits` significant figures without scientific notation. */
export function fmtSig(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined) return '–'
  return Number(value.toPrecision(digits)).toLocaleString('en-US', {
    maximumFractionDigits: 12,
  })
}

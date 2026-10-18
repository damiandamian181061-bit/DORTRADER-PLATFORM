import { useEffect, useMemo, useState } from 'react'
import { fmtPrice } from '../lib/chain'
import {
  EXAMPLE_FORM,
  fetchPrice,
  fmtSig,
  formToRequest,
  statusText,
} from '../lib/pricing'
import type {
  GreeksView,
  Mode,
  OptionType,
  PriceResult,
  PricingForm,
} from '../lib/pricing'
import { fmtVol } from '../lib/vol'
import { Alert, Badge, Card, Choice, Field, Note, inputClass } from './ui'

const TYPES = [
  { id: 'call', label: 'Call' },
  { id: 'put', label: 'Put' },
] as const
const MODES = [
  { id: 'vol', label: 'Vol → price' },
  { id: 'price', label: 'Price → vol' },
] as const

const GREEK_ROWS: {
  key: keyof GreeksView
  label: string
  digits: number
  note: string
}[] = [
  {
    key: 'delta',
    label: 'Delta',
    digits: 4,
    note: 'change in price per 1 USDC move in the forward',
  },
  {
    key: 'gamma',
    label: 'Gamma',
    digits: 3,
    note: 'change in delta per 1 USDC move',
  },
  {
    key: 'vega',
    label: 'Vega',
    digits: 4,
    note: 'change in price per 1 vol point',
  },
  {
    key: 'theta',
    label: 'Theta',
    digits: 4,
    note: 'change in price per calendar day',
  },
  {
    key: 'vanna',
    label: 'Vanna',
    digits: 3,
    note: 'change in delta per vol point',
  },
  {
    key: 'volga',
    label: 'Volga',
    digits: 3,
    note: 'change in vega per vol point',
  },
]

export function PricingView({ initial }: { initial: PricingForm | null }) {
  const [form, setForm] = useState<PricingForm>(initial ?? EXAMPLE_FORM)
  const [result, setResult] = useState<PriceResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const request = useMemo(() => formToRequest(form), [form])
  const set = <K extends keyof PricingForm>(key: K, value: PricingForm[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  useEffect(() => {
    if (typeof request === 'string') return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      fetchPrice(request, fetch, controller.signal)
        .then((reply) => {
          setResult(reply)
          setError(null)
        })
        .catch((failure: unknown) => {
          if (!controller.signal.aborted) {
            setResult(null)
            setError(
              failure instanceof Error ? failure.message : 'request failed',
            )
          }
        })
    }, 250)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [request])

  const inputIssue = typeof request === 'string' ? request : null
  const solving = form.mode === 'price'

  return (
    <div className="grid gap-8 lg:grid-cols-[22rem_1fr]">
      <form
        className="space-y-4"
        onSubmit={(event) => event.preventDefault()}
        aria-label="Pricing inputs"
      >
        <div className="flex flex-wrap gap-4">
          <Choice
            label="Option"
            value={form.optionType}
            options={TYPES}
            onChange={(id: OptionType) => set('optionType', id)}
          />
          <Choice
            label="Solve for"
            value={form.mode}
            options={MODES}
            onChange={(id: Mode) => set('mode', id)}
          />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Forward (USDC)">
            <input
              className={inputClass}
              inputMode="decimal"
              value={form.forward}
              onChange={(e) => set('forward', e.target.value)}
            />
          </Field>
          <Field label="Strike (USDC)">
            <input
              className={inputClass}
              inputMode="decimal"
              value={form.strike}
              onChange={(e) => set('strike', e.target.value)}
            />
          </Field>
          <Field label="Days to expiry">
            <input
              className={inputClass}
              inputMode="decimal"
              value={form.days}
              onChange={(e) => set('days', e.target.value)}
            />
          </Field>
          <Field label="Rate (% a year)">
            <input
              className={inputClass}
              inputMode="decimal"
              value={form.ratePercent}
              onChange={(e) => set('ratePercent', e.target.value)}
            />
          </Field>
          <Field label={solving ? 'Premium (USDC per BTC)' : 'Volatility (%)'}>
            <input
              className={inputClass}
              inputMode="decimal"
              value={form.value}
              onChange={(e) => set('value', e.target.value)}
            />
          </Field>
          <Field label="Quantity (BTC)">
            <input
              className={inputClass}
              inputMode="decimal"
              value={form.quantity}
              onChange={(e) => set('quantity', e.target.value)}
            />
          </Field>
          {solving && (
            <Field label="Price tick (USDC, optional)">
              <input
                className={inputClass}
                inputMode="decimal"
                value={form.tick}
                placeholder="5"
                onChange={(e) => set('tick', e.target.value)}
              />
            </Field>
          )}
        </div>
        <button
          type="button"
          onClick={() => setForm(EXAMPLE_FORM)}
          className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-200 hover:bg-slate-800"
        >
          Reset to example
        </button>
        <Note>
          Black-76 on the expiry-matched future, ACT/365, rate compounded
          continuously. Use the forward Deribit shows for the expiry, not the
          spot index.
        </Note>
      </form>

      <div className="space-y-6" aria-live="polite">
        {inputIssue && <Note>{inputIssue}</Note>}
        {error && <Alert text={error} />}
        {result && <Results result={result} form={form} />}
      </div>
    </div>
  )
}

function Results({ result, form }: { result: PriceResult; form: PricingForm }) {
  const contract = result.per_contract
  const premium =
    contract?.price ?? (form.mode === 'price' ? Number(form.value) : null)
  const solved = result.vol_status === 'OK' || result.vol_status === 'given'
  return (
    <>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card
          title={form.mode === 'price' ? 'Implied vol' : 'Volatility'}
          value={fmtVol(result.vol)}
          hint={statusText(result.vol_status)}
        />
        <Card
          title="Price per BTC"
          value={premium === null ? '–' : fmtPrice(premium)}
          hint="USDC"
        />
        <Card
          title="Intrinsic"
          value={fmtPrice(result.intrinsic)}
          hint="USDC"
        />
        <Card
          title="Time value"
          value={result.time_value === null ? '–' : fmtPrice(result.time_value)}
          hint="USDC"
        />
      </div>

      {!solved && (
        <div className="flex items-center gap-2">
          <Badge tone="warn">{result.vol_status}</Badge>
          <Note>
            {statusText(result.vol_status)}. No Greeks without a volatility.
          </Note>
        </div>
      )}

      {result.iv_interval && (
        <Note>
          Allowing the quoted price to be off by half a tick, the volatility
          lies between{' '}
          <span className="font-mono text-slate-200">
            {fmtVol(result.iv_interval.low)}
          </span>{' '}
          and{' '}
          <span className="font-mono text-slate-200">
            {result.iv_interval.high === null
              ? 'no upper limit'
              : fmtVol(result.iv_interval.high)}
          </span>
          . A wide range means the price tells you little about the volatility.
        </Note>
      )}

      {contract && result.position && (
        <div className="space-y-2">
          <div className="overflow-x-auto rounded border border-slate-800">
            <table className="w-full min-w-[34rem] border-collapse text-sm">
              <caption className="sr-only">
                Greeks per contract and for the position
              </caption>
              <thead className="bg-slate-900 text-slate-400">
                <tr>
                  <th scope="col" className="px-3 py-2 text-left font-normal">
                    Greek
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">
                    Per 1 BTC contract
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">
                    Position ({form.quantity} BTC)
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-normal">
                    Meaning
                  </th>
                </tr>
              </thead>
              <tbody className="font-mono tabular-nums">
                {GREEK_ROWS.map((row) => (
                  <tr key={row.key} className="border-t border-slate-800/70">
                    <th
                      scope="row"
                      className="px-3 py-2 text-left font-sans font-normal text-slate-200"
                    >
                      {row.label}
                    </th>
                    <td className="px-3 py-2 text-right">
                      {fmtSig(contract[row.key], row.digits)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {fmtSig(result.position?.[row.key], row.digits)}
                    </td>
                    <td className="px-3 py-2 font-sans text-xs text-slate-500">
                      {row.note}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.theta_deribit_style !== null &&
            result.years_to_expiry * 365 < 1 && (
              <Note>
                Inside the last day Deribit reports theta as decay over the time
                that is left, capped at the option&apos;s value:{' '}
                <span className="font-mono text-slate-200">
                  {fmtSig(result.theta_deribit_style)}
                </span>{' '}
                per BTC, against the plain theta above.
              </Note>
            )}
        </div>
      )}
      <p className="text-xs text-slate-600">Model {result.model_version}</p>
    </>
  )
}

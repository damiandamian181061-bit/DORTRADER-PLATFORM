import { useEffect, useMemo, useState } from 'react'
import {
  fetchChain,
  fetchStatus,
  flagLabel,
  fmtUtc,
  isoToUtcInput,
  summarizeChain,
  utcInputToIso,
} from '../lib/chain'
import type { Chain, Status } from '../lib/chain'
import type { ContractPick } from '../lib/pricing'
import { ExpiryTable } from './ExpiryTable'
import { StatusStrip } from './StatusStrip'
import { Card, Note } from './ui'

const STATUS_REFRESH_MS = 30_000

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'request failed'
}

export function ChainView({
  onPrice,
}: {
  onPrice: (pick: ContractPick) => void
}) {
  const [timeInput, setTimeInput] = useState('') // empty means "latest recorded"
  const [expiries, setExpiries] = useState(3)
  const [chain, setChain] = useState<Chain | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState<Status | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchChain(
      { at: utcInputToIso(timeInput), expiries },
      fetch,
      controller.signal,
    )
      .then((result) => {
        setChain(result)
        setError(null)
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) {
          setChain(null)
          setError(errorMessage(failure))
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [timeInput, expiries])

  useEffect(() => {
    const controller = new AbortController()
    const refresh = () => {
      fetchStatus(fetch, controller.signal)
        .then(setStatus)
        .catch(() => {
          if (!controller.signal.aborted) setStatus(null)
        })
    }
    refresh()
    const timer = setInterval(refresh, STATUS_REFRESH_MS)
    return () => {
      controller.abort()
      clearInterval(timer)
    }
  }, [])

  const changeTime = (value: string) => {
    setLoading(true)
    setTimeInput(value)
  }
  const changeExpiries = (value: number) => {
    setLoading(true)
    setExpiries(value)
  }

  const summary = useMemo(() => (chain ? summarizeChain(chain) : null), [chain])
  const min = status?.recorded_from
    ? isoToUtcInput(status.recorded_from)
    : undefined
  const max = status?.recorded_to
    ? isoToUtcInput(status.recorded_to)
    : undefined

  return (
    <div className="space-y-6">
      <StatusStrip status={status} />

      <form
        className="flex flex-wrap items-end gap-4"
        onSubmit={(event) => event.preventDefault()}
      >
        <label className="flex flex-col gap-1 text-sm text-slate-400">
          Time (UTC)
          <input
            type="datetime-local"
            value={timeInput}
            min={min}
            max={max}
            onChange={(event) => changeTime(event.target.value)}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
          />
        </label>
        <button
          type="button"
          onClick={() => changeTime('')}
          disabled={timeInput === ''}
          className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-200 hover:bg-slate-800 disabled:cursor-default disabled:text-slate-600 disabled:hover:bg-transparent"
        >
          Latest
        </button>
        <label className="flex flex-col gap-1 text-sm text-slate-400">
          Expiries
          <select
            value={expiries}
            onChange={(event) => changeExpiries(Number(event.target.value))}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
          >
            {[1, 2, 3, 4, 6, 8].map((count) => (
              <option key={count} value={count}>
                {count}
              </option>
            ))}
          </select>
        </label>
        {chain && (
          <p className="pb-1 text-sm text-slate-400" aria-live="polite">
            Showing {fmtUtc(chain.snapshot_at)}
            {timeInput === '' ? ' (latest)' : ''} · {chain.model_version}
          </p>
        )}
      </form>

      {error && (
        <p
          role="alert"
          className="rounded border border-red-900 bg-red-950/40 px-3 py-2 text-sm text-red-200"
        >
          {error}
        </p>
      )}
      {loading && !chain && (
        <p className="text-sm text-slate-500">Loading chain…</p>
      )}

      {chain && (
        <div className={loading ? 'space-y-8 opacity-60' : 'space-y-8'}>
          {summary && <ChainSummaryCards summary={summary} />}
          {chain.expiries.map((expiry) => (
            <ExpiryTable
              key={expiry.expiration}
              expiry={expiry}
              onPrice={onPrice}
            />
          ))}
          {(chain.listed_without_quote > 0 ||
            chain.quotes_without_instrument > 0) && (
            <p className="text-sm text-slate-500">
              {chain.listed_without_quote} listed contracts had no quote and{' '}
              {chain.quotes_without_instrument} quotes had no catalog entry at
              this time.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function ChainSummaryCards({
  summary,
}: {
  summary: ReturnType<typeof summarizeChain>
}) {
  const flags = Object.entries(summary.flagCounts).sort((a, b) => b[1] - a[1])
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card
          title="Contracts"
          value={String(summary.contracts)}
          hint="calls and puts shown"
        />
        <Card
          title="With a solved vol"
          value={`${summary.withVol} / ${summary.contracts}`}
          hint="vol from the mark price"
        />
        <Card
          title="Flagged"
          value={String(summary.flagged)}
          hint={
            flags.length === 0 ? 'no flags' : `${flags.length} kinds, see below`
          }
        />
        <Card
          title="Our IV vs Deribit's"
          value={
            summary.otmMedianAbsDiff === null
              ? '–'
              : `${summary.otmMedianAbsDiff.toFixed(2)} pts`
          }
          hint={
            summary.otmMaxAbsDiff === null
              ? 'nothing to compare'
              : `median gap, out of the money (max ${summary.otmMaxAbsDiff.toFixed(2)}, n ${summary.otmCompared})`
          }
        />
      </div>
      {flags.length > 0 && (
        <Note>
          Flags:{' '}
          {flags
            .map(([flag, count]) => `${flagLabel(flag)} (${count})`)
            .join(', ')}
          . Hover a flag count in the table for the contract&apos;s own flags.
          Click a mark price to open it in the pricing calculator.
        </Note>
      )}
    </div>
  )
}

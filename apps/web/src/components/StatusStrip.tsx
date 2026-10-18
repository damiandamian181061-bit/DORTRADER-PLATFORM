import { fmtAge, fmtUtc } from '../lib/chain'
import type { Status } from '../lib/chain'

export function StatusStrip({ status }: { status: Status | null }) {
  if (status === null) {
    return (
      <p className="text-sm text-slate-500">Recorder status unavailable.</p>
    )
  }
  const gaps = status.recent_gaps
  const gapTitle = gaps
    .map(
      (gap) =>
        `${fmtUtc(gap.start)} to ${fmtUtc(gap.end)} (${fmtAge(gap.seconds)})`,
    )
    .join('\n')
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm text-slate-400">
      <span className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className={`inline-block size-2 rounded-full ${status.healthy ? 'bg-emerald-400' : 'bg-red-400'}`}
        />
        <span className={status.healthy ? 'text-slate-200' : 'text-red-300'}>
          {status.healthy ? 'Recording' : 'Recorder is not running'}
        </span>
        {status.age_seconds !== null && (
          <span>last cycle {fmtAge(status.age_seconds)} ago</span>
        )}
      </span>
      {status.recorded_from && status.recorded_to && (
        <span>
          recorded {fmtUtc(status.recorded_from)} to{' '}
          {fmtUtc(status.recorded_to)}
        </span>
      )}
      <span title={gapTitle || undefined}>
        {gaps.length === 0
          ? 'no gaps in the last 7 days'
          : `${gaps.length} gap(s) in the last 7 days`}
      </span>
    </div>
  )
}

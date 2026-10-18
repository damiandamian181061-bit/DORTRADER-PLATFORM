import type { ReactNode } from 'react'

export const inputClass =
  'rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100 focus:border-sky-500 focus:outline-none'

export function Alert({ text }: { text: string }) {
  return (
    <p
      role="alert"
      className="rounded border border-red-900 bg-red-950/40 px-3 py-2 text-sm text-red-200"
    >
      {text}
    </p>
  )
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="text-sm text-slate-400">{children}</p>
}

export function Field({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <label className="flex flex-col gap-1 text-sm text-slate-400">
      {label}
      {children}
    </label>
  )
}

/** A small set of mutually exclusive buttons, announced as radio-like tabs. */
export function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: readonly { id: T; label: string }[]
  onChange: (id: T) => void
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="flex flex-col gap-1 text-sm text-slate-400"
    >
      {label}
      <div className="flex overflow-hidden rounded border border-slate-700">
        {options.map((option) => (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={option.id === value}
            onClick={() => onChange(option.id)}
            className={`px-3 py-1 ${
              option.id === value
                ? 'bg-slate-700 text-white'
                : 'text-slate-300 hover:bg-slate-800'
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}

export function Badge({
  tone,
  children,
}: {
  tone: 'good' | 'bad' | 'warn' | 'plain'
  children: ReactNode
}) {
  const color = {
    good: 'border-emerald-800 bg-emerald-950/40 text-emerald-300',
    bad: 'border-red-900 bg-red-950/40 text-red-300',
    warn: 'border-amber-800 bg-amber-950/40 text-amber-300',
    plain: 'border-slate-700 bg-slate-900 text-slate-300',
  }[tone]
  return (
    <span className={`rounded border px-2 py-0.5 text-xs ${color}`}>
      {children}
    </span>
  )
}

export function Card({
  title,
  value,
  hint,
}: {
  title: string
  value: string
  hint?: string
}) {
  return (
    <div className="rounded border border-slate-800 bg-slate-900/50 px-4 py-3">
      <p className="text-xs uppercase tracking-wide text-slate-500">{title}</p>
      <p className="font-mono text-xl tabular-nums text-slate-100">{value}</p>
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

export function Section({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-medium">{title}</h2>
      {children}
    </section>
  )
}

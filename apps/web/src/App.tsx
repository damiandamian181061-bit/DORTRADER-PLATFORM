import { lazy, Suspense, useState } from 'react'
import { ChainView } from './components/ChainView'

// The volatility page pulls in the charting library, so it only loads when opened.
const VolatilityView = lazy(() =>
  import('./components/VolatilityView').then((module) => ({
    default: module.VolatilityView,
  })),
)

const VIEWS = [
  { id: 'chain', label: 'Option chain' },
  { id: 'volatility', label: 'Volatility' },
] as const

type ViewId = (typeof VIEWS)[number]['id']

function App() {
  const [view, setView] = useState<ViewId>('chain')

  return (
    <main className="min-h-screen bg-slate-950 px-4 py-6 text-slate-100 sm:px-8">
      <div className="mx-auto max-w-[96rem] space-y-6">
        <header className="space-y-4">
          <h1 className="text-2xl font-semibold tracking-tight">
            BTC/USDC options
          </h1>
          <div role="tablist" aria-label="Page" className="flex gap-2">
            {VIEWS.map(({ id, label }) => (
              <button
                key={id}
                role="tab"
                type="button"
                aria-selected={view === id}
                onClick={() => setView(id)}
                className={`rounded border px-4 py-1.5 text-sm ${
                  view === id
                    ? 'border-sky-500 bg-slate-800 text-white'
                    : 'border-slate-700 text-slate-300 hover:bg-slate-800'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </header>

        {view === 'chain' ? (
          <ChainView />
        ) : (
          <Suspense
            fallback={<p className="text-sm text-slate-500">Loading…</p>}
          >
            <VolatilityView />
          </Suspense>
        )}
      </div>
    </main>
  )
}

export default App

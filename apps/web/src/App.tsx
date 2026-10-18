import { lazy, Suspense, useEffect, useState } from 'react'
import { ChainView } from './components/ChainView'
import { DataView } from './components/DataView'
import { PricingView } from './components/PricingView'
import { formFromContract } from './lib/pricing'
import type { ContractPick, PricingForm } from './lib/pricing'
import { VIEWS, hashForView, viewFromHash } from './lib/route'
import type { ViewId } from './lib/route'

// The volatility page pulls in the charting library, so it only loads when opened.
const VolatilityView = lazy(() =>
  import('./components/VolatilityView').then((module) => ({
    default: module.VolatilityView,
  })),
)

function App() {
  const [view, setView] = useState<ViewId>(() =>
    viewFromHash(window.location.hash),
  )
  // A contract picked on the chain page, carried into the calculator.
  const [pricingStart, setPricingStart] = useState<PricingForm | null>(null)
  const [pricingKey, setPricingKey] = useState(0)

  useEffect(() => {
    const sync = () => setView(viewFromHash(window.location.hash))
    window.addEventListener('hashchange', sync)
    window.addEventListener('popstate', sync)
    return () => {
      window.removeEventListener('hashchange', sync)
      window.removeEventListener('popstate', sync)
    }
  }, [])

  const open = (id: ViewId) => {
    window.history.pushState(null, '', hashForView(id))
    setView(id)
  }

  const priceContract = (pick: ContractPick) => {
    setPricingStart(formFromContract(pick))
    setPricingKey((n) => n + 1) // a fresh calculator, filled from the contract
    open('pricing')
  }

  return (
    <main className="min-h-screen bg-slate-950 px-4 py-6 text-slate-100 sm:px-8">
      <div className="mx-auto max-w-[96rem] space-y-6">
        <header className="space-y-4">
          <h1 className="text-2xl font-semibold tracking-tight">
            BTC/USDC options
          </h1>
          <div
            role="tablist"
            aria-label="Page"
            className="flex flex-wrap gap-2"
          >
            {VIEWS.map(({ id, label }) => (
              <button
                key={id}
                role="tab"
                type="button"
                aria-selected={view === id}
                onClick={() => open(id)}
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

        <Suspense fallback={<p className="text-sm text-slate-500">Loading…</p>}>
          {view === 'chain' && <ChainView onPrice={priceContract} />}
          {view === 'volatility' && <VolatilityView />}
          {view === 'pricing' && (
            <PricingView key={pricingKey} initial={pricingStart} />
          )}
          {view === 'data' && <DataView />}
        </Suspense>
      </div>
    </main>
  )
}

export default App

import { useEffect, useState } from 'react'
import { fetchHealth } from './lib/health'

function App() {
  const [status, setStatus] = useState<string>('checking…')

  useEffect(() => {
    fetchHealth()
      .then(setStatus)
      .catch((error: unknown) => {
        setStatus(error instanceof Error ? error.message : 'unreachable')
      })
  }, [])

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-2 bg-slate-950 text-slate-100">
      <h1 className="text-3xl font-semibold tracking-tight">DORTRADER</h1>
      <p className="text-slate-400">
        API health: <span className="font-mono text-emerald-400">{status}</span>
      </p>
    </main>
  )
}

export default App

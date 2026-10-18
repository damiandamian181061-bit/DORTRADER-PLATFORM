import { useEffect, useRef } from 'react'
import type { Trace } from '../lib/vol'

const LAYOUT = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: { color: '#cbd5e1', size: 12 },
  margin: { t: 8, r: 16, b: 40, l: 56 },
  legend: { orientation: 'h', y: 1.12, x: 0 },
  hovermode: 'x unified',
  xaxis: { gridcolor: '#1e293b', zerolinecolor: '#1e293b' },
}

type Purgeable = { purge(root: HTMLElement): void }

/** Line chart on the slim Plotly bundle, loaded on first use so other pages stay light. */
export default function PlotlyChart({
  traces,
  yTitle,
  label,
  fromZero = true,
}: {
  traces: Trace[]
  yTitle: string
  label: string
  fromZero?: boolean // volatility starts at zero; a price chart should not
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const element = ref.current
    if (!element) return
    let cancelled = false
    let plotly: Purgeable | null = null
    import('plotly.js-basic-dist-min').then(({ default: Plotly }) => {
      if (cancelled) return
      plotly = Plotly
      Plotly.react(
        element,
        traces.map((t) => ({
          type: 'scatter',
          mode: 'lines',
          name: t.name,
          x: t.x,
          y: t.y,
          connectgaps: false,
          line: { color: t.color, width: 1.8, dash: t.dash ?? 'solid' },
          hovertemplate: '%{y:.1f}',
        })),
        {
          ...LAYOUT,
          yaxis: {
            title: { text: yTitle },
            gridcolor: '#1e293b',
            rangemode: fromZero ? 'tozero' : 'normal',
          },
        },
        { displayModeBar: false, responsive: true },
      )
    })
    return () => {
      // Synchronous, so a re-run of this effect can never be wiped by a late purge.
      cancelled = true
      plotly?.purge(element)
    }
  }, [traces, yTitle, fromZero])

  return <div ref={ref} role="img" aria-label={label} className="h-80 w-full" />
}

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

/** Line chart on the slim Plotly bundle, loaded on first use so other pages stay light. */
export default function PlotlyChart({
  traces,
  yTitle,
  label,
}: {
  traces: Trace[]
  yTitle: string
  label: string
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const element = ref.current
    if (!element) return
    let cancelled = false
    import('plotly.js-basic-dist-min').then(({ default: Plotly }) => {
      if (cancelled) return
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
            rangemode: 'tozero',
          },
        },
        { displayModeBar: false, responsive: true },
      )
    })
    return () => {
      cancelled = true
    }
  }, [traces, yTitle])

  useEffect(() => {
    const element = ref.current
    return () => {
      if (element)
        void import('plotly.js-basic-dist-min').then(({ default: P }) =>
          P.purge(element),
        )
    }
  }, [])

  return <div ref={ref} role="img" aria-label={label} className="h-80 w-full" />
}

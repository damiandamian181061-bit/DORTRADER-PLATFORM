export const VIEWS = [
  { id: 'chain', label: 'Option chain' },
  { id: 'volatility', label: 'Volatility' },
  { id: 'pricing', label: 'Pricing' },
  { id: 'data', label: 'Data' },
] as const

export type ViewId = (typeof VIEWS)[number]['id']

/** The view named by a URL hash such as `#/pricing`; anything unknown opens the chain. */
export function viewFromHash(hash: string): ViewId {
  const name = hash.replace(/^#\/?/, '')
  return VIEWS.find((view) => view.id === name)?.id ?? 'chain'
}

export function hashForView(view: ViewId): string {
  return `#/${view}`
}

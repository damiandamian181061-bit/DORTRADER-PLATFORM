export type Fetch = typeof fetch

export async function getJson<T>(
  url: string,
  fetchImpl: Fetch,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetchImpl(url, { signal })
  if (!response.ok) {
    let detail = `request failed: ${response.status}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // keep the status-based message
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

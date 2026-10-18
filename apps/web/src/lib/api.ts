export type Fetch = typeof fetch

type ValidationIssue = { loc?: unknown[]; msg?: string }

/** The message to show for an error body: FastAPI sends a string, or a list for 422. */
export function detailMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return (detail as ValidationIssue[])
      .map((issue) => {
        const field = issue.loc
          ?.filter((part) => part !== 'body' && part !== 'query')
          .join('.')
        return field
          ? `${field}: ${issue.msg ?? 'invalid'}`
          : (issue.msg ?? 'invalid')
      })
      .join('; ')
  }
  return `request failed: ${status}`
}

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      // keep the status-based message
    }
    throw new Error(detailMessage(body, response.status))
  }
  return (await response.json()) as T
}

export async function getJson<T>(
  url: string,
  fetchImpl: Fetch,
  signal?: AbortSignal,
): Promise<T> {
  return parse<T>(await fetchImpl(url, { signal }))
}

export async function postJson<T>(
  url: string,
  body: unknown,
  fetchImpl: Fetch,
  signal?: AbortSignal,
): Promise<T> {
  return parse<T>(
    await fetchImpl(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    }),
  )
}

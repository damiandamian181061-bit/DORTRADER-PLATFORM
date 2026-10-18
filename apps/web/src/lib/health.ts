export async function fetchHealth(
  fetchImpl: typeof fetch = fetch,
): Promise<string> {
  const response = await fetchImpl('/api/health')
  if (!response.ok) {
    throw new Error(`health check failed: ${response.status}`)
  }
  const body = (await response.json()) as { status: string }
  return body.status
}

import { useEffect, useState } from 'react'

function message(error: unknown): string {
  return error instanceof Error ? error.message : 'request failed'
}

/**
 * Fetches when `key` changes. `load` is rebuilt every render, so the key is what says the
 * request is really different. While a new key loads, the previous data stays available.
 */
export function useRemote<T>(
  load: (signal: AbortSignal) => Promise<T>,
  key: string,
) {
  const [state, setState] = useState<{
    key: string
    data: T | null
    error: string | null
  }>({
    key: '',
    data: null,
    error: null,
  })
  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
      .then((data) => setState({ key, data, error: null }))
      .catch((failure: unknown) => {
        if (!controller.signal.aborted)
          setState({ key, data: null, error: message(failure) })
      })
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return { data: state.data, error: state.error, loading: state.key !== key }
}

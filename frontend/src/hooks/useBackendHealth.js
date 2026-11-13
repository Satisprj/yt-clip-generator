import { useEffect, useState } from 'react'
import { getReadiness } from '../api/health.js'

/** Polls the backend readiness endpoint. status: loading | ok | degraded | offline */
export function useBackendHealth(intervalMs = 15000) {
  const [state, setState] = useState({ status: 'loading', checks: null, error: null })

  useEffect(() => {
    let cancelled = false
    let timer
    let controller

    async function load() {
      controller = new AbortController()
      try {
        const data = await getReadiness(controller.signal)
        if (!cancelled) setState({ status: data.status, checks: data.checks, error: null })
      } catch (err) {
        if (cancelled || err.name === 'AbortError') return
        setState({ status: 'offline', checks: null, error: err.message })
      } finally {
        if (!cancelled) timer = setTimeout(load, intervalMs)
      }
    }

    load()
    return () => {
      cancelled = true
      controller?.abort()
      clearTimeout(timer)
    }
  }, [intervalMs])

  return state
}

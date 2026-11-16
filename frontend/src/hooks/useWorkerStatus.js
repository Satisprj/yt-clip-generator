import { useEffect, useState } from 'react'
import { getWorkerStatus } from '../api/health.js'

/** Polls whether a worker is running, only while `enabled` (e.g. a job is waiting). */
export function useWorkerStatus(enabled, intervalMs = 5000) {
  const [status, setStatus] = useState(null)

  useEffect(() => {
    if (!enabled) {
      setStatus(null)
      return undefined
    }
    let cancelled = false
    let timer
    const controller = new AbortController()

    async function load() {
      try {
        const data = await getWorkerStatus(controller.signal)
        if (!cancelled) setStatus(data)
      } catch {
        // the API itself may be down; the header badge reports that
      }
      if (!cancelled) timer = setTimeout(load, intervalMs)
    }

    load()
    return () => {
      cancelled = true
      controller.abort()
      clearTimeout(timer)
    }
  }, [enabled, intervalMs])

  return status
}

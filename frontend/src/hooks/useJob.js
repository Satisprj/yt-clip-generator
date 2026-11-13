import { useEffect, useState } from 'react'
import { getJob } from '../api/jobs.js'
import { isTerminal } from '../utils/jobStatus.js'

/** Loads a job and keeps polling until it completes or fails. */
export function useJob(jobId, intervalMs = 2000) {
  const [state, setState] = useState({ job: null, error: null, loading: true })

  useEffect(() => {
    let cancelled = false
    let timer
    let controller

    async function load() {
      controller = new AbortController()
      let keepPolling = true
      try {
        const job = await getJob(jobId, controller.signal)
        if (cancelled) return
        setState({ job, error: null, loading: false })
        keepPolling = !isTerminal(job.status)
      } catch (err) {
        if (cancelled || err.name === 'AbortError') return
        // A missing job won't appear later; other errors (network) are retried.
        keepPolling = err.status !== 404
        setState((prev) => ({ ...prev, error: err, loading: false }))
      }
      if (keepPolling && !cancelled) timer = setTimeout(load, intervalMs)
    }

    setState({ job: null, error: null, loading: true })
    load()
    return () => {
      cancelled = true
      controller?.abort()
      clearTimeout(timer)
    }
  }, [jobId, intervalMs])

  return state
}

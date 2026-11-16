import { useCallback, useEffect, useMemo, useState } from 'react'
import { listJobPublications } from '../api/publishing.js'

const ACTIVE = new Set(['queued', 'uploading'])

/** A job's publications, grouped by clip; polls while any upload is queued or running. */
export function usePublications(jobId, intervalMs = 3000) {
  const [items, setItems] = useState([])
  const [tick, setTick] = useState(0)

  const refresh = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    listJobPublications(jobId, controller.signal)
      .then(setItems)
      .catch(() => {}) // transient errors: the next poll retries
    return () => controller.abort()
  }, [jobId, tick])

  const active = items.some((p) => ACTIVE.has(p.status))
  useEffect(() => {
    if (!active) return undefined
    const timer = setTimeout(refresh, intervalMs)
    return () => clearTimeout(timer)
  }, [active, items, intervalMs, refresh])

  const byClip = useMemo(() => {
    const map = {}
    for (const p of items) (map[p.clip_id] ??= []).push(p)
    return map
  }, [items])

  return { byClip, refresh }
}

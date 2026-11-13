import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listJobs } from '../api/jobs.js'
import StatusBadge from '../components/StatusBadge.jsx'
import VideoThumbnail from '../components/VideoThumbnail.jsx'
import { formatRelativeTime } from '../utils/format.js'

const PAGE_SIZE = 20

export default function HistoryPage() {
  const [jobs, setJobs] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const loadPage = useCallback(async (offset, signal) => {
    setLoading(true)
    setError(null)
    try {
      const page = await listJobs({ limit: PAGE_SIZE, offset }, signal)
      setJobs((prev) => (offset === 0 ? page.items : [...prev, ...page.items]))
      setTotal(page.total)
    } catch (err) {
      if (err.name !== 'AbortError') setError(err.message)
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    loadPage(0, controller.signal)
    return () => controller.abort()
  }, [loadPage])

  return (
    <section>
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-bold tracking-tight">Processing history</h1>
        {total > 0 && <span className="text-sm text-slate-400">{total} job{total === 1 ? '' : 's'}</span>}
      </div>

      {error && (
        <div role="alert" className="mt-6 flex items-center justify-between gap-4 rounded-lg border border-rose-900 bg-rose-950/50 p-3 text-sm text-rose-200">
          <span>{error}</span>
          <button onClick={() => loadPage(jobs.length)} className="rounded-md bg-rose-900 px-3 py-1 hover:bg-rose-800">Retry</button>
        </div>
      )}

      {!loading && !error && jobs.length === 0 && (
        <div className="mt-6 rounded-xl border border-dashed border-slate-800 p-10 text-center text-slate-400">
          No jobs yet. <Link to="/" className="text-rose-400 hover:underline">Submit your first video</Link>.
        </div>
      )}

      <ul className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {jobs.map((job) => (
          <li key={job.id}>
            <Link
              to={`/jobs/${job.id}`}
              className="block rounded-xl border border-slate-800 bg-slate-900/50 p-3 transition-colors hover:border-slate-600"
            >
              <VideoThumbnail src={job.thumbnail_url} alt="" durationSeconds={job.duration_seconds} />
              <div className="mt-3 flex items-start justify-between gap-2">
                <h2 className="line-clamp-2 text-sm font-medium">{job.title ?? job.youtube_url}</h2>
                <StatusBadge status={job.status} />
              </div>
              <p className="mt-1 truncate text-xs text-slate-500">
                {[job.channel, formatRelativeTime(job.created_at)].filter(Boolean).join(' · ')}
              </p>
            </Link>
          </li>
        ))}
      </ul>

      {loading && <p className="mt-6 text-center text-slate-400">Loading…</p>}

      {!loading && jobs.length < total && (
        <div className="mt-6 text-center">
          <button
            onClick={() => loadPage(jobs.length)}
            className="rounded-lg border border-slate-700 px-4 py-2 text-sm hover:border-slate-500"
          >
            Load more
          </button>
        </div>
      )}
    </section>
  )
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { deleteJob, listJobs } from '../api/jobs.js'
import StatusBadge from '../components/StatusBadge.jsx'
import VideoThumbnail from '../components/VideoThumbnail.jsx'
import { formatBytes, formatRelativeTime } from '../utils/format.js'
import { isTerminal } from '../utils/jobStatus.js'

const PAGE_SIZE = 20
const ACTIVE_REFRESH_MS = 4000

function JobCard({ job, onDeleted }) {
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState(null)

  async function handleDelete(event) {
    event.preventDefault() // the card is a link
    if (!window.confirm(`Delete "${job.title ?? job.youtube_url}" and its files (${formatBytes(job.storage_bytes)})?`)) return
    setDeleting(true)
    setError(null)
    try {
      await deleteJob(job.id)
      onDeleted(job)
    } catch (err) {
      setError(err.message)
      setDeleting(false)
    }
  }

  return (
    <li className={deleting ? 'opacity-50' : ''}>
      <Link
        to={`/jobs/${job.id}`}
        className="group block rounded-xl border border-slate-800 bg-slate-900/50 p-3 transition-colors hover:border-slate-600"
      >
        <VideoThumbnail src={job.thumbnail_url} alt="" durationSeconds={job.duration_seconds} />
        <div className="mt-3 flex items-start justify-between gap-2">
          <h2 className="line-clamp-2 text-sm font-medium">{job.title ?? job.youtube_url}</h2>
          <StatusBadge status={job.status} />
        </div>
        <p className="mt-1 truncate text-xs text-slate-500">
          {[job.channel, formatRelativeTime(job.created_at)].filter(Boolean).join(' · ')}
        </p>
        <div className="mt-2 flex items-center justify-between gap-2 text-xs text-slate-400">
          <span>
            {job.ready_clip_count > 0
              ? `🎬 ${job.ready_clip_count} clip${job.ready_clip_count === 1 ? '' : 's'}`
              : isTerminal(job.status) ? 'No clips' : `${job.progress}%`}
            {job.storage_bytes > 0 && ` · ${formatBytes(job.storage_bytes)}`}
          </span>
          {isTerminal(job.status) && (
            <button
              onClick={handleDelete}
              disabled={deleting}
              aria-label={`Delete ${job.title ?? 'job'}`}
              className="rounded-md px-2 py-0.5 text-slate-500 opacity-70 hover:bg-rose-950 hover:text-rose-300 group-hover:opacity-100"
            >
              {deleting ? 'Deleting…' : 'Delete'}
            </button>
          )}
        </div>
        {error && <p role="alert" className="mt-2 text-xs text-rose-300">{error}</p>}
      </Link>
    </li>
  )
}

export default function HistoryPage() {
  const [jobs, setJobs] = useState([])
  const [total, setTotal] = useState(0)
  const [storageTotal, setStorageTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const loadedCount = useRef(0)

  const loadPage = useCallback(async (offset, signal) => {
    setLoading(true)
    setError(null)
    try {
      const page = await listJobs({ limit: PAGE_SIZE, offset }, signal)
      setJobs((prev) => {
        const next = offset === 0 ? page.items : [...prev, ...page.items]
        loadedCount.current = next.length
        return next
      })
      setTotal(page.total)
      setStorageTotal(page.storage_bytes_total)
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

  // While any visible job is running, quietly refresh the loaded rows.
  const anyActive = jobs.some((j) => !isTerminal(j.status))
  useEffect(() => {
    if (!anyActive) return undefined
    const timer = setInterval(async () => {
      try {
        const page = await listJobs({ limit: Math.max(PAGE_SIZE, loadedCount.current), offset: 0 })
        setJobs(page.items)
        setTotal(page.total)
        setStorageTotal(page.storage_bytes_total)
      } catch {
        // keep the current list; the next tick retries
      }
    }, ACTIVE_REFRESH_MS)
    return () => clearInterval(timer)
  }, [anyActive])

  function handleDeleted(job) {
    setJobs((prev) => prev.filter((j) => j.id !== job.id))
    setTotal((t) => t - 1)
    setStorageTotal((s) => Math.max(0, s - job.storage_bytes))
  }

  return (
    <section>
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <h1 className="text-2xl font-bold tracking-tight">Processing history</h1>
        {total > 0 && (
          <span className="text-sm text-slate-400">
            {total} job{total === 1 ? '' : 's'} · {formatBytes(storageTotal)} on disk
          </span>
        )}
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
        {jobs.map((job) => <JobCard key={job.id} job={job} onDeleted={handleDeleted} />)}
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

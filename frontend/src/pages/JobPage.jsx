import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { retryJob } from '../api/jobs.js'
import ProgressBar from '../components/ProgressBar.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import TranscriptPanel from '../components/TranscriptPanel.jsx'
import VideoThumbnail from '../components/VideoThumbnail.jsx'
import { useJob } from '../hooks/useJob.js'
import { formatDateTime, formatDuration } from '../utils/format.js'

function Detail({ label, children }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-200">{children}</dd>
    </div>
  )
}

function RetryButton({ jobId, onRetried }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function handleRetry() {
    setBusy(true)
    setError(null)
    try {
      onRetried(await retryJob(jobId))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <button
        onClick={handleRetry}
        disabled={busy}
        className="rounded-lg bg-rose-500 px-4 py-2 text-sm font-semibold hover:bg-rose-400 disabled:opacity-50"
      >
        {busy ? 'Retrying…' : 'Retry'}
      </button>
      <span className="text-xs text-slate-400">Finished steps (like the download) are reused.</span>
      {error && <span role="alert" className="text-sm text-rose-300">{error}</span>}
    </div>
  )
}

export default function JobPage() {
  const { jobId } = useParams()
  const { job, error, loading, replaceJob } = useJob(jobId)

  if (loading) {
    return <p className="text-center text-slate-400">Loading job…</p>
  }
  if (!job) {
    return (
      <section className="text-center">
        <h1 className="text-xl font-semibold">{error?.status === 404 ? 'Job not found' : 'Could not load job'}</h1>
        <p className="mt-2 text-slate-400">{error?.message}</p>
        <Link to="/history" className="mt-4 inline-block text-rose-400 hover:underline">Go to history</Link>
      </section>
    )
  }

  const failed = job.status === 'failed'

  return (
    <section className="mx-auto max-w-3xl">
      <Link to="/history" className="text-sm text-slate-400 hover:text-white">← History</Link>

      <div className="mt-4 grid gap-6 sm:grid-cols-[280px_1fr]">
        <VideoThumbnail src={job.thumbnail_url} alt={job.title ?? 'Video thumbnail'} durationSeconds={job.duration_seconds} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={job.status} />
          </div>
          <h1 className="mt-2 text-xl font-semibold break-words">{job.title ?? 'Fetching video details…'}</h1>
          {job.channel && <p className="mt-1 text-sm text-slate-400">{job.channel}</p>}
          <a href={job.youtube_url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm text-rose-400 hover:underline">
            Open on YouTube ↗
          </a>
        </div>
      </div>

      <div className="mt-8 rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <div className="flex items-baseline justify-between gap-4 text-sm">
          <span className="text-slate-300">{failed ? 'Processing failed' : job.stage_message ?? 'Waiting…'}</span>
          <span className="tabular-nums text-slate-400">{job.progress}%</span>
        </div>
        <div className="mt-3">
          <ProgressBar value={failed ? 100 : job.progress} failed={failed} />
        </div>
        {failed && job.error_message && (
          <p role="alert" className="mt-4 rounded-lg border border-rose-900 bg-rose-950/50 p-3 text-sm text-rose-200">
            {job.error_message}
          </p>
        )}
        {failed && <RetryButton jobId={job.id} onRetried={replaceJob} />}
      </div>

      <dl className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Detail label="Duration">{formatDuration(job.duration_seconds)}</Detail>
        <Detail label="Video ID">{job.youtube_video_id}</Detail>
        <Detail label="Submitted">{formatDateTime(job.created_at)}</Detail>
        <Detail label="Finished">{job.completed_at ? formatDateTime(job.completed_at) : '–'}</Detail>
      </dl>

      {error && (
        <p className="mt-4 text-sm text-amber-400">Connection problem, retrying… ({error.message})</p>
      )}

      {/* Loaded once the job stops running; a job that failed later on still has one. */}
      {(job.status === 'completed' || job.status === 'failed') && (
        <div className="mt-8">
          <TranscriptPanel key={job.updated_at} jobId={job.id} youtubeUrl={job.youtube_url} />
        </div>
      )}
    </section>
  )
}

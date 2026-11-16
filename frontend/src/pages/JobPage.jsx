import { Link, useParams } from 'react-router-dom'
import ClipsPanel from '../components/ClipsPanel.jsx'
import JobActions from '../components/JobActions.jsx'
import ProgressBar from '../components/ProgressBar.jsx'
import StageStepper from '../components/StageStepper.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import TranscriptPanel from '../components/TranscriptPanel.jsx'
import VideoThumbnail from '../components/VideoThumbnail.jsx'
import { useJob } from '../hooks/useJob.js'
import { formatDateTime, formatDuration } from '../utils/format.js'
import { isTerminal } from '../utils/jobStatus.js'

// Statuses after which a transcript / clips can exist.
const HAS_TRANSCRIPT = new Set(['analyzing', 'rendering', 'completed', 'failed'])
const HAS_CLIPS = new Set(['rendering', 'completed', 'failed'])

function Detail({ label, children }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-200">{children}</dd>
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
    <section className="mx-auto max-w-5xl">
      <Link to="/history" className="text-sm text-slate-400 hover:text-white">← History</Link>

      <div className="mt-4 grid gap-6 sm:grid-cols-[280px_1fr]">
        <VideoThumbnail src={job.thumbnail_url} alt={job.title ?? 'Video thumbnail'} durationSeconds={job.duration_seconds} />
        <div className="min-w-0">
          <StatusBadge status={job.status} />
          <h1 className="mt-2 text-xl font-semibold break-words">{job.title ?? 'Fetching video details…'}</h1>
          {job.channel && <p className="mt-1 text-sm text-slate-400">{job.channel}</p>}
          <a href={job.youtube_url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm text-rose-400 hover:underline">
            Open on YouTube ↗
          </a>
        </div>
      </div>

      <div className="mt-8 rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <StageStepper status={job.status} failedStage={job.failed_stage} />
        <div className="mt-4 flex items-baseline justify-between gap-4 text-sm">
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
        {isTerminal(job.status) && <JobActions job={job} onJobChanged={replaceJob} />}
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

      {/* Clips appear while rendering and refresh each time one finishes. */}
      {HAS_CLIPS.has(job.status) && (
        <div className="mt-8">
          <ClipsPanel
            jobId={job.id}
            videoId={job.youtube_video_id}
            refreshKey={`${job.status}-${job.ready_clip_count}-${job.clip_count}`}
          />
        </div>
      )}
      {HAS_TRANSCRIPT.has(job.status) && (
        <div className="mt-8">
          <TranscriptPanel key={isTerminal(job.status) ? 'final' : 'running'} jobId={job.id} youtubeUrl={job.youtube_url} />
        </div>
      )}
    </section>
  )
}

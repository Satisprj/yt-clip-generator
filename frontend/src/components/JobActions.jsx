import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { clipsZipUrl, deleteJob, freeSpace, reprocessJob, retryJob } from '../api/jobs.js'
import { formatBytes } from '../utils/format.js'

const BUTTON = 'rounded-lg border border-slate-700 px-3 py-1.5 text-sm hover:border-slate-500 disabled:opacity-50'
const PRIMARY = 'rounded-lg bg-rose-500 px-3 py-1.5 text-sm font-semibold hover:bg-rose-400 disabled:opacity-50'

/** Actions for a finished (completed or failed) job. `onJobChanged` receives the updated job. */
export default function JobActions({ job, onJobChanged }) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  async function run(name, action, { confirmText } = {}) {
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(name)
    setError(null)
    setNotice(null)
    try {
      await action()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }

  const failed = job.status === 'failed'
  const hasClips = job.clip_count > 0

  return (
    <div className="mt-4 border-t border-slate-800 pt-4">
      <div className="flex flex-wrap items-center gap-2">
        {failed && (
          <button
            className={PRIMARY}
            disabled={busy !== null}
            onClick={() => run('retry', async () => onJobChanged(await retryJob(job.id)))}
            title="Continue from where it stopped; finished steps are reused"
          >
            {busy === 'retry' ? 'Retrying…' : '↻ Retry'}
          </button>
        )}
        {job.ready_clip_count > 0 && (
          <a className={PRIMARY} href={clipsZipUrl(job.id)}>
            ⬇ Download all ({job.ready_clip_count}) as ZIP
          </a>
        )}
        {!failed && (
          <button
            className={BUTTON}
            disabled={busy !== null}
            onClick={() => run('highlights', async () => onJobChanged(await reprocessJob(job.id, 'highlights')), {
              confirmText: 'Ask Claude for new highlights? The current clips will be replaced.',
            })}
            title="Claude picks new moments; the download and transcript are reused"
          >
            {busy === 'highlights' ? 'Starting…' : '✨ New highlights'}
          </button>
        )}
        {hasClips && (
          <button
            className={BUTTON}
            disabled={busy !== null}
            onClick={() => run('render', async () => onJobChanged(await reprocessJob(job.id, 'render')), {
              confirmText: 'Render the same clips again (for example after changing caption or quality settings)?',
            })}
          >
            {busy === 'render' ? 'Starting…' : '🎬 Re-render clips'}
          </button>
        )}
        {job.has_source_files && (
          <button
            className={BUTTON}
            disabled={busy !== null}
            onClick={() => run('space', async () => {
              const result = await freeSpace(job.id)
              onJobChanged(result.job)
              setNotice(`Freed ${formatBytes(result.freed_bytes)}. Clips are kept; the video is downloaded again if needed.`)
            }, {
              confirmText: 'Delete the downloaded video and audio to save disk space? Finished clips are kept.',
            })}
            title="Delete the downloaded source video and audio; keep the clips"
          >
            {busy === 'space' ? 'Freeing…' : '🧹 Free up space'}
          </button>
        )}
        <button
          className={`${BUTTON} ml-auto text-rose-300 hover:border-rose-700`}
          disabled={busy !== null}
          onClick={() => run('delete', async () => {
            await deleteJob(job.id)
            navigate('/history', { replace: true })
          }, { confirmText: `Delete this job and all its files (${formatBytes(job.storage_bytes)})? This can't be undone.` })}
        >
          {busy === 'delete' ? 'Deleting…' : '🗑 Delete job'}
        </button>
      </div>
      <p className="mt-2 text-xs text-slate-500">Disk used: {formatBytes(job.storage_bytes)}</p>
      {notice && <p role="status" className="mt-2 text-sm text-emerald-300">{notice}</p>}
      {error && <p role="alert" className="mt-2 text-sm text-rose-300">{error}</p>}
    </div>
  )
}

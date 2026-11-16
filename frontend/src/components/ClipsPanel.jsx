import { useEffect, useState } from 'react'
import { clipFileUrl, listClips } from '../api/jobs.js'
import { formatDuration } from '../utils/format.js'

const CATEGORY_STYLES = {
  funny: 'bg-amber-950 text-amber-300',
  emotional: 'bg-rose-950 text-rose-300',
  educational: 'bg-sky-950 text-sky-300',
  controversial: 'bg-red-950 text-red-300',
  inspirational: 'bg-emerald-950 text-emerald-300',
  story: 'bg-violet-950 text-violet-300',
  surprising: 'bg-fuchsia-950 text-fuchsia-300',
  insightful: 'bg-teal-950 text-teal-300',
}

const CRITERIA = ['hook', 'standalone', 'payoff', 'emotion', 'shareability']

function ScoreBar({ label, value }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-24 capitalize text-slate-400">{label}</span>
      <div className="h-1.5 flex-1 rounded-full bg-slate-800">
        <div className="h-full rounded-full bg-rose-400" style={{ width: `${value * 10}%` }} />
      </div>
      <span className="w-5 text-right tabular-nums text-slate-300">{value}</span>
    </div>
  )
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }
  return (
    <button onClick={copy} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs hover:border-slate-500">
      {copied ? 'Copied ✓' : 'Copy caption'}
    </button>
  )
}

function ClipCard({ clip, videoId }) {
  const [preview, setPreview] = useState(false)
  const [details, setDetails] = useState(false)
  const start = Math.floor(clip.start_seconds)
  const end = Math.ceil(clip.end_seconds)
  const caption = [clip.title, clip.description, clip.hashtags.join(' ')].filter(Boolean).join('\n\n')

  const ready = clip.render_status === 'ready'

  return (
    <li className="flex flex-col gap-4 rounded-xl border border-slate-800 bg-slate-900/50 p-4 sm:flex-row">
      {ready && (
        <div className="mx-auto w-full max-w-[220px] shrink-0 sm:mx-0 sm:w-[180px]">
          <video
            controls
            preload="none"
            playsInline
            poster={clipFileUrl(clip.id, 'thumbnail')}
            src={clipFileUrl(clip.id, 'video')}
            className="aspect-[9/16] w-full rounded-lg bg-black object-cover"
          />
        </div>
      )}
      <div className="min-w-0 flex-1">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-md bg-slate-800 px-2 py-0.5 font-semibold">#{clip.rank}</span>
        <span className={`rounded-md px-2 py-0.5 capitalize ${CATEGORY_STYLES[clip.category] ?? 'bg-slate-800'}`}>
          {clip.category}
        </span>
        <span className="text-slate-400 tabular-nums">
          {formatDuration(clip.start_seconds)}–{formatDuration(clip.end_seconds)} · {clip.duration_seconds.toFixed(1)} s
        </span>
        <span className="ml-auto font-semibold tabular-nums text-rose-300" title="Viral potential (0–10)">
          ★ {clip.viral_score.toFixed(1)}
        </span>
      </div>

      <h3 className="mt-2 font-semibold">{clip.title}</h3>
      {clip.description && <p className="mt-1 text-sm text-slate-300">{clip.description}</p>}
      {clip.hashtags.length > 0 && (
        <p className="mt-1 text-sm text-sky-300">{clip.hashtags.join(' ')}</p>
      )}

      {preview && (
        <div className="mt-3 aspect-video overflow-hidden rounded-lg bg-black">
          <iframe
            title={`Preview of clip ${clip.rank}`}
            src={`https://www.youtube-nocookie.com/embed/${videoId}?start=${start}&end=${end}&autoplay=1&rel=0`}
            allow="autoplay; encrypted-media; picture-in-picture"
            allowFullScreen
            className="h-full w-full"
          />
        </div>
      )}

      {clip.render_status === 'failed' && (
        <p role="alert" className="mt-2 rounded-md border border-rose-900 bg-rose-950/50 p-2 text-xs text-rose-200">
          Rendering failed: {clip.render_error}
        </p>
      )}
      {clip.render_status === 'pending' && (
        <p className="mt-2 text-xs text-slate-400">Not rendered yet.</p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {ready ? (
          <>
            <a
              href={clipFileUrl(clip.id, 'video', { download: true })}
              className="rounded-md bg-rose-500 px-3 py-1 text-xs font-semibold hover:bg-rose-400"
            >
              ⬇ Download MP4{clip.file_size_bytes ? ` (${(clip.file_size_bytes / 1e6).toFixed(1)} MB)` : ''}
            </a>
            <a
              href={clipFileUrl(clip.id, 'subtitles')}
              className="rounded-md border border-slate-700 px-2.5 py-1 text-xs hover:border-slate-500"
            >
              ⬇ Subtitles (.srt)
            </a>
          </>
        ) : (
          <button
            onClick={() => setPreview((p) => !p)}
            className="rounded-md bg-rose-500 px-3 py-1 text-xs font-semibold hover:bg-rose-400"
          >
            {preview ? 'Hide preview' : '▶ Preview on YouTube'}
          </button>
        )}
        <button
          onClick={() => setDetails((d) => !d)}
          className="rounded-md border border-slate-700 px-2.5 py-1 text-xs hover:border-slate-500"
          aria-expanded={details}
        >
          {details ? 'Hide details' : 'Why this clip?'}
        </button>
        <CopyButton text={caption} />
      </div>

      {details && (
        <div className="mt-3 space-y-3 border-t border-slate-800 pt-3">
          <p className="text-sm text-slate-300"><span className="text-slate-500">Hook: </span>{clip.hook}</p>
          <p className="text-sm text-slate-300"><span className="text-slate-500">Why: </span>{clip.reason}</p>
          <div className="space-y-1">
            {CRITERIA.map((c) => <ScoreBar key={c} label={c} value={clip.scores[c] ?? 0} />)}
          </div>
          <blockquote className="border-l-2 border-slate-700 pl-3 text-sm italic text-slate-400">
            {clip.transcript_text}
          </blockquote>
          <p className="text-xs text-slate-500">
            Sentences S{clip.first_sentence}–S{clip.last_sentence}
            {clip.layout && ` · Framing: ${clip.layout === 'track' ? 'follows the speaker' : 'full frame on blurred background'}`}
          </p>
        </div>
      )}
      </div>
    </li>
  )
}

/** Highlights chosen for a job, best first. `refreshKey` changes when the list should reload. */
export default function ClipsPanel({ jobId, videoId, refreshKey }) {
  const [state, setState] = useState({ clips: null, error: null })

  // Start empty for a new job...
  useEffect(() => {
    setState({ clips: null, error: null })
  }, [jobId])

  // ...but on refreshes (a clip finished rendering) keep showing the current list until the
  // new one arrives, so playing videos aren't interrupted by a "Loading" flash.
  useEffect(() => {
    const controller = new AbortController()
    listClips(jobId, controller.signal)
      .then((clips) => setState({ clips, error: null }))
      .catch((err) => {
        if (err.name !== 'AbortError') setState((prev) => ({ ...prev, error: err }))
      })
    return () => controller.abort()
  }, [jobId, refreshKey])

  if (state.error) return <p className="text-sm text-rose-300">Could not load clips: {state.error.message}</p>
  if (!state.clips) return <p className="text-sm text-slate-400">Loading clips…</p>
  if (state.clips.length === 0) return null

  return (
    <section>
      <h2 className="text-lg font-semibold">Highlights <span className="text-slate-500">({state.clips.length})</span></h2>
      <p className="mt-1 text-sm text-slate-400">
        Chosen by AI, ranked by viral potential. Vertical 1080×1920 clips with captions, ready to post.
      </p>
      <ul className="mt-4 grid gap-4 lg:grid-cols-2">
        {state.clips.map((clip) => <ClipCard key={clip.id} clip={clip} videoId={videoId} />)}
      </ul>
    </section>
  )
}

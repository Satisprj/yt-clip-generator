import { useEffect, useMemo, useState } from 'react'
import { getTranscript } from '../api/jobs.js'
import { formatDuration } from '../utils/format.js'

const LANGUAGE_NAMES = new Intl.DisplayNames(undefined, { type: 'language' })

function languageName(code) {
  try {
    return LANGUAGE_NAMES.of(code) ?? code
  } catch {
    return code
  }
}

/** Shows the job's sentence-level transcript. Timestamps open YouTube at that moment. */
export default function TranscriptPanel({ jobId, youtubeUrl }) {
  const [state, setState] = useState({ transcript: null, error: null, loading: true })
  const [query, setQuery] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setState({ transcript: null, error: null, loading: true })
    getTranscript(jobId, controller.signal)
      .then((transcript) => setState({ transcript, error: null, loading: false }))
      .catch((err) => {
        if (err.name !== 'AbortError') setState({ transcript: null, error: err, loading: false })
      })
    return () => controller.abort()
  }, [jobId])

  const { transcript, error, loading } = state
  const sentences = useMemo(() => {
    const all = transcript?.sentences ?? []
    const q = query.trim().toLowerCase()
    return q ? all.filter((s) => s.text.toLowerCase().includes(q)) : all
  }, [transcript, query])

  if (loading) return <p className="text-sm text-slate-400">Loading transcript…</p>
  if (error) {
    // No transcript is normal for jobs that failed before transcription.
    if (error.code === 'transcript_not_ready') return null
    return <p className="text-sm text-rose-300">Could not load the transcript: {error.message}</p>
  }

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/50">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 p-4">
        <div>
          <h2 className="font-semibold">Transcript</h2>
          <p className="text-xs text-slate-400">
            {languageName(transcript.language)} · {transcript.sentence_count} sentences ·{' '}
            {transcript.word_count.toLocaleString()} words · model {transcript.model}
          </p>
        </div>
        <label className="w-full sm:w-56">
          <span className="sr-only">Search transcript</span>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search…"
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm outline-none focus:border-rose-400"
          />
        </label>
      </header>
      <ol className="max-h-[28rem] divide-y divide-slate-800/60 overflow-y-auto">
        {sentences.map((s) => (
          <li key={s.id} className="flex gap-3 px-4 py-2 text-sm">
            <a
              href={`${youtubeUrl}&t=${Math.floor(s.start)}s`}
              target="_blank"
              rel="noreferrer"
              title="Open on YouTube at this moment"
              className="shrink-0 pt-0.5 font-mono text-xs tabular-nums text-rose-300 hover:underline"
            >
              {formatDuration(s.start)}
            </a>
            <span className="text-slate-200">{s.text}</span>
            <span className="ml-auto shrink-0 pt-0.5 font-mono text-[10px] text-slate-600" title="Sentence ID">
              S{s.id}
            </span>
          </li>
        ))}
        {sentences.length === 0 && (
          <li className="px-4 py-6 text-center text-sm text-slate-400">No sentences match “{query}”.</li>
        )}
      </ol>
    </section>
  )
}

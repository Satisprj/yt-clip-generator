import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createJob } from '../api/jobs.js'
import { isValidYouTubeUrl } from '../utils/youtube.js'

export default function HomePage() {
  const navigate = useNavigate()
  const [url, setUrl] = useState('')
  const [touched, setTouched] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState(null)

  const valid = isValidYouTubeUrl(url)
  const showInvalid = touched && url.trim() !== '' && !valid

  async function handleSubmit(event) {
    event.preventDefault()
    setTouched(true)
    if (!valid || submitting) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const job = await createJob(url.trim())
      navigate(`/jobs/${job.id}`)
    } catch (err) {
      if (err.code === 'duplicate_job' && err.details?.job_id) {
        navigate(`/jobs/${err.details.job_id}`)  // already processing: show that job
        return
      }
      setSubmitError(err.message)
      setSubmitting(false)
    }
  }

  const help = submitError ?? (showInvalid ? 'Enter a valid YouTube video URL.' : '')

  return (
    <section className="mx-auto max-w-2xl text-center">
      <h1 className="text-3xl font-bold tracking-tight sm:text-5xl">
        Turn long videos into <span className="text-rose-400">viral shorts</span>
      </h1>
      <p className="mt-4 text-slate-400">
        Paste a YouTube link. AI finds the most engaging moments and creates vertical
        20–30 second clips with captions.
      </p>

      <form onSubmit={handleSubmit} className="mt-10 flex flex-col gap-3 sm:flex-row" noValidate>
        <label htmlFor="youtube-url" className="sr-only">YouTube URL</label>
        <input
          id="youtube-url"
          type="url"
          inputMode="url"
          autoComplete="off"
          placeholder="https://www.youtube.com/watch?v=…"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value)
            setSubmitError(null)
          }}
          onBlur={() => setTouched(true)}
          aria-invalid={showInvalid || Boolean(submitError)}
          aria-describedby="url-help"
          disabled={submitting}
          className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-base outline-none placeholder:text-slate-500 focus:border-rose-400 focus:ring-2 focus:ring-rose-400/30 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={!valid || submitting}
          className="rounded-lg bg-rose-500 px-6 py-3 font-semibold transition-colors hover:bg-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? 'Submitting…' : 'Generate clips'}
        </button>
      </form>
      <p id="url-help" role={help ? 'alert' : undefined} className="mt-2 min-h-5 text-left text-sm text-rose-400">
        {help}
      </p>
    </section>
  )
}

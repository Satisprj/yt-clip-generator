import { useState } from 'react'
import { isValidYouTubeUrl } from '../utils/youtube.js'

export default function HomePage() {
  const [url, setUrl] = useState('')
  const [touched, setTouched] = useState(false)

  const valid = isValidYouTubeUrl(url)
  const showError = touched && url.trim() !== '' && !valid

  function handleSubmit(event) {
    event.preventDefault()
    setTouched(true)
    // Job submission (POST /api/jobs) is implemented in Phase 1.
  }

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
          placeholder="https://www.youtube.com/watch?v=…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onBlur={() => setTouched(true)}
          aria-invalid={showError}
          aria-describedby="url-help"
          className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-base outline-none placeholder:text-slate-500 focus:border-rose-400 focus:ring-2 focus:ring-rose-400/30"
        />
        <button
          type="submit"
          disabled
          title="Available in the next development phase"
          className="rounded-lg bg-rose-500 px-6 py-3 font-semibold transition-colors hover:bg-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Generate clips
        </button>
      </form>
      <p id="url-help" className={`mt-2 min-h-5 text-left text-sm ${showError ? 'text-rose-400' : 'text-slate-500'}`}>
        {showError
          ? 'Enter a valid YouTube video URL.'
          : valid
            ? 'Valid YouTube URL. Processing will be enabled in Phase 1.'
            : ''}
      </p>
    </section>
  )
}

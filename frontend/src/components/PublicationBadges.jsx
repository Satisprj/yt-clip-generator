import { useState } from 'react'
import { PLATFORM_LABELS, retryPublication } from '../api/publishing.js'

function Badge({ publication, onChanged }) {
  const [busy, setBusy] = useState(false)
  const label = PLATFORM_LABELS[publication.platform]

  async function retry() {
    setBusy(true)
    try {
      await retryPublication(publication.id)
    } finally {
      setBusy(false)
      onChanged()
    }
  }

  if (publication.status === 'published') {
    return (
      <a href={publication.url} target="_blank" rel="noreferrer"
        className="rounded-md bg-emerald-950 px-2 py-0.5 text-xs text-emerald-300 ring-1 ring-emerald-800 hover:underline">
        ✓ {label} ({publication.privacy}) ↗
      </a>
    )
  }
  if (publication.status === 'failed') {
    return (
      <span className="inline-flex items-center gap-1 rounded-md bg-rose-950 px-2 py-0.5 text-xs text-rose-300 ring-1 ring-rose-800"
        title={publication.error_message ?? ''}>
        ✗ {label} failed
        <button onClick={retry} disabled={busy} className="ml-1 underline disabled:opacity-50">
          {busy ? '…' : 'retry'}
        </button>
      </span>
    )
  }
  return (
    <span className="rounded-md bg-sky-950 px-2 py-0.5 text-xs text-sky-300 ring-1 ring-sky-800">
      ⏳ {label}: {publication.status === 'queued' ? 'waiting' : `uploading ${publication.progress}%`}
    </span>
  )
}

/** Status of each platform a clip was (or is being) posted to. Latest attempt per platform. */
export default function PublicationBadges({ publications = [], onChanged }) {
  const latest = Object.values(
    publications.reduce((acc, p) => {
      const current = acc[p.platform]
      if (!current || current.created_at < p.created_at) acc[p.platform] = p
      return acc
    }, {}),
  )
  if (latest.length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {latest.map((p) => <Badge key={p.id} publication={p} onChanged={onChanged} />)}
      {latest.some((p) => p.status === 'failed') && (
        <p className="w-full text-xs text-rose-300/80">
          {latest.find((p) => p.status === 'failed').error_message}
        </p>
      )}
    </div>
  )
}

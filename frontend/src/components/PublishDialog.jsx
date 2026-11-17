import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { PLATFORM_LABELS, listIntegrations, publishClip, publishJob } from '../api/publishing.js'

/**
 * Publish one clip (`clip`) or every rendered clip of a job (`jobId`).
 * For a single clip the title and caption are editable and the same text goes to every platform.
 */
export default function PublishDialog({ clip, jobId, clipCount, onClose, onPublished }) {
  const dialogRef = useRef(null)
  const [integrations, setIntegrations] = useState(null)
  const [selected, setSelected] = useState([])
  const [privacy, setPrivacy] = useState('private')
  const [title, setTitle] = useState(clip?.title ?? '')
  const [caption, setCaption] = useState(
    clip ? [clip.description, clip.hashtags.join(' ')].filter(Boolean).join('\n\n') : '',
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    dialogRef.current?.showModal()
    listIntegrations()
      .then((items) => {
        setIntegrations(items)
        setSelected(items.filter((i) => i.supported && i.account).map((i) => i.platform))
      })
      .catch((err) => setError(err.message))
  }, [])

  function toggle(platform) {
    setSelected((s) => (s.includes(platform) ? s.filter((p) => p !== platform) : [...s, platform]))
  }

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const result = clip
        ? await publishClip(clip.id, { platforms: selected, privacy, title, caption })
        : await publishJob(jobId, { platforms: selected, privacy })
      onPublished(result)
      onClose()
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  const anyConnected = integrations?.some((i) => i.supported && i.account)

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-xl border border-slate-700 bg-slate-900 p-0 text-slate-100 backdrop:bg-black/70"
    >
      <form onSubmit={submit} className="p-5">
        <h2 className="text-lg font-semibold">
          {clip ? `Publish clip #${clip.rank}` : `Publish all ${clipCount} clips`}
        </h2>

        <fieldset className="mt-4">
          <legend className="text-sm text-slate-400">Platforms</legend>
          {!integrations && <p className="mt-2 text-sm text-slate-400">Loading…</p>}
          <div className="mt-2 grid grid-cols-2 gap-2">
            {integrations?.map((i) => {
              const usable = i.supported && i.account
              return (
                <label key={i.platform}
                  className={`flex items-center gap-2 rounded-lg border p-2 text-sm ${usable ? 'border-slate-700' : 'border-slate-800 text-slate-500'}`}
                  title={usable ? i.account.name : 'Not connected: see Settings'}>
                  <input type="checkbox" disabled={!usable} checked={selected.includes(i.platform)}
                    onChange={() => toggle(i.platform)} className="accent-rose-500" />
                  <span>{PLATFORM_LABELS[i.platform]}</span>
                  {!usable && <span className="ml-auto text-[10px]">not connected</span>}
                </label>
              )
            })}
          </div>
          {integrations && !anyConnected && (
            <p className="mt-2 text-sm text-amber-300">
              No account connected yet. <Link to="/settings" className="underline">Connect one in Settings</Link>.
            </p>
          )}
        </fieldset>

        <label className="mt-4 block text-sm">
          <span className="text-slate-400">Visibility</span>
          <select value={privacy} onChange={(e) => setPrivacy(e.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5">
            <option value="private">Private</option>
            <option value="unlisted">Unlisted</option>
            <option value="public">Public</option>
          </select>
          <span className="mt-1 block text-xs text-slate-500">
            YouTube and TikTok post privately until their API audits are approved, whatever you pick.
            Facebook: Private saves the Reel as a draft. Instagram Reels are always public.
          </span>
        </label>

        {clip ? (
          <>
            <label className="mt-4 block text-sm">
              <span className="text-slate-400">Title (same on every platform)</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} required
                className="mt-1 block w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5" />
            </label>
            <label className="mt-3 block text-sm">
              <span className="text-slate-400">Caption with hashtags (same on every platform)</span>
              <textarea value={caption} onChange={(e) => setCaption(e.target.value)} rows={5} maxLength={5000}
                className="mt-1 block w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5" />
            </label>
          </>
        ) : (
          <p className="mt-4 text-sm text-slate-400">
            Each clip is posted with its own AI-written title, description and hashtags. Clips already
            posted to a platform are skipped.
          </p>
        )}

        {error && <p role="alert" className="mt-3 text-sm text-rose-300">{error}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-slate-700 px-4 py-2 text-sm">
            Cancel
          </button>
          <button type="submit" disabled={busy || selected.length === 0}
            className="rounded-lg bg-rose-500 px-4 py-2 text-sm font-semibold hover:bg-rose-400 disabled:opacity-50">
            {busy ? 'Queuing…' : `📤 Publish to ${selected.length || 0} platform${selected.length === 1 ? '' : 's'}`}
          </button>
        </div>
      </form>
    </dialog>
  )
}

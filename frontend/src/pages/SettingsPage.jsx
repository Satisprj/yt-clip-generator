import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  PLATFORM_LABELS,
  disconnectPlatform,
  getConnectUrl,
  listIntegrations,
} from '../api/publishing.js'
import { formatDateTime } from '../utils/format.js'

function IntegrationCard({ integration, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const { platform, supported, configured, account, note, connect_group: group } = integration
  const provider = { youtube: 'Google', meta: 'Facebook', tiktok: 'TikTok' }[group] ?? group
  const envKeys = {
    youtube: 'GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET',
    meta: 'META_APP_ID and META_APP_SECRET',
    tiktok: 'TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET',
  }[group]

  async function connect() {
    setBusy(true)
    setError(null)
    try {
      window.location.assign(await getConnectUrl(group)) // leaves the app to sign in
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  async function disconnect() {
    if (!window.confirm(`Disconnect ${account.name}? Already published videos stay online.`)) return
    setBusy(true)
    setError(null)
    try {
      await disconnectPlatform(platform)
      onChanged()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <div className="flex flex-wrap items-center gap-3">
        {account?.avatar_url ? (
          <img src={account.avatar_url} alt="" referrerPolicy="no-referrer" className="h-10 w-10 rounded-full" />
        ) : (
          <div className="grid h-10 w-10 place-items-center rounded-full bg-slate-800 text-lg" aria-hidden="true">
            {platform === 'youtube' ? '▶' : platform === 'tiktok' ? '♪' : platform === 'instagram' ? '◎' : 'f'}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold">{PLATFORM_LABELS[platform]}</h2>
          <p className="text-sm text-slate-400">
            {account
              ? <>Connected: <span className="text-slate-200">{account.name}</span> · since {formatDateTime(account.connected_at)}</>
              : configured ? 'Not connected' : 'Needs setup (see below)'}
          </p>
        </div>
        {supported && !account && (
          <button onClick={connect} disabled={busy || !configured}
            className="rounded-lg bg-rose-500 px-4 py-2 text-sm font-semibold hover:bg-rose-400 disabled:opacity-50">
            {busy ? `Opening ${provider}…` : group === 'meta' ? 'Connect with Facebook' : 'Connect'}
          </button>
        )}
        {account && (
          <button onClick={disconnect} disabled={busy}
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm hover:border-rose-700 hover:text-rose-300 disabled:opacity-50">
            Disconnect
          </button>
        )}
      </div>
      {note && <p className="mt-3 text-xs text-slate-500">{note}</p>}
      {group === 'meta' && (
        <p className="mt-1 text-xs text-slate-500">One Facebook sign-in connects both your Page and its Instagram account.</p>
      )}
      {supported && !configured && (
        <p className="mt-3 text-xs text-amber-300">
          Add {envKeys} to <code>backend\.env</code> and restart the API.
          Step-by-step guide: <code>docs/PUBLISHING.md</code>.
        </p>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-rose-300">{error}</p>}
    </li>
  )
}

export default function SettingsPage() {
  const [params, setParams] = useSearchParams()
  const [state, setState] = useState({ items: null, error: null })
  const connected = params.get('connected')
  const failed = params.get('error')

  const load = useCallback(async () => {
    try {
      setState({ items: await listIntegrations(), error: null })
    } catch (err) {
      setState({ items: null, error: err.message })
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return (
    <section className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-bold tracking-tight">Connected accounts</h1>
      <p className="mt-1 text-sm text-slate-400">Connect once; then publish any clip to all connected platforms with one click.</p>

      {connected && (
        <p role="status" className="mt-4 flex justify-between gap-3 rounded-lg border border-emerald-900 bg-emerald-950/40 p-3 text-sm text-emerald-200">
          <span>Connected <b>{connected}</b>.</span>
          <button onClick={() => setParams({})} className="text-emerald-400 hover:underline">Dismiss</button>
        </p>
      )}
      {failed && (
        <p role="alert" className="mt-4 flex justify-between gap-3 rounded-lg border border-rose-900 bg-rose-950/50 p-3 text-sm text-rose-200">
          <span>{failed}</span>
          <button onClick={() => setParams({})} className="text-rose-300 hover:underline">Dismiss</button>
        </p>
      )}
      {state.error && <p role="alert" className="mt-4 text-sm text-rose-300">{state.error}</p>}
      {!state.items && !state.error && <p className="mt-6 text-slate-400">Loading…</p>}

      <ul className="mt-6 space-y-3">
        {state.items?.map((item) => <IntegrationCard key={item.platform} integration={item} onChanged={load} />)}
      </ul>
    </section>
  )
}

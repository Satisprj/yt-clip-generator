import { useBackendHealth } from '../hooks/useBackendHealth.js'

const STYLES = {
  loading: { dot: 'bg-slate-400 animate-pulse', label: 'Checking…' },
  ok: { dot: 'bg-emerald-400', label: 'All systems ready' },
  degraded: { dot: 'bg-amber-400', label: 'Some services down' },
  offline: { dot: 'bg-rose-500', label: 'Backend offline' },
}

function describe({ status, checks, error }) {
  if (status === 'offline') return error
  if (!checks) return ''
  return Object.entries(checks)
    .map(([name, c]) => `${name}: ${c.status === 'ok' ? 'ok' : c.error}`)
    .join('\n')
}

export default function BackendStatus() {
  const health = useBackendHealth()
  const style = STYLES[health.status] ?? STYLES.offline

  return (
    <div
      className="flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900 px-3 py-1 text-xs text-slate-300"
      title={describe(health)}
      role="status"
    >
      <span className={`h-2 w-2 rounded-full ${style.dot}`} aria-hidden="true" />
      <span className="hidden sm:inline">{style.label}</span>
    </div>
  )
}

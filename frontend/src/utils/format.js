/** 75 -> "1:15", 3725 -> "1:02:05". Returns "–" for missing values. */
export function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return '–'
  const total = Math.round(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
const UNITS = [
  ['year', 31536000],
  ['month', 2592000],
  ['day', 86400],
  ['hour', 3600],
  ['minute', 60],
]

/** ISO timestamp -> "5 minutes ago", "yesterday", ... */
export function formatRelativeTime(iso) {
  if (!iso) return ''
  const diffSeconds = (new Date(iso).getTime() - Date.now()) / 1000
  for (const [unit, size] of UNITS) {
    if (Math.abs(diffSeconds) >= size) return relative.format(Math.round(diffSeconds / size), unit)
  }
  return 'just now'
}

/** 1536000 -> "1.5 MB" */
export function formatBytes(bytes) {
  if (!bytes) return '0 MB'
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(bytes >= 1e8 ? 0 : 1)} MB`
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`
}

export function formatDateTime(iso) {
  return iso ? new Date(iso).toLocaleString() : ''
}

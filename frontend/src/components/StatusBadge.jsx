import { isTerminal, statusInfo } from '../utils/jobStatus.js'

const TONES = {
  slate: 'bg-slate-800 text-slate-300 ring-slate-700',
  sky: 'bg-sky-950 text-sky-300 ring-sky-800',
  violet: 'bg-violet-950 text-violet-300 ring-violet-800',
  amber: 'bg-amber-950 text-amber-300 ring-amber-800',
  emerald: 'bg-emerald-950 text-emerald-300 ring-emerald-800',
  rose: 'bg-rose-950 text-rose-300 ring-rose-800',
}

export default function StatusBadge({ status }) {
  const { label, tone } = statusInfo(status)
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {!isTerminal(status) && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" aria-hidden="true" />}
      {label}
    </span>
  )
}

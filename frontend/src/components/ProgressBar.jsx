export default function ProgressBar({ value, failed = false }) {
  const pct = Math.max(0, Math.min(100, value ?? 0))
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-slate-800"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
    >
      <div
        className={`h-full rounded-full transition-all duration-500 ${failed ? 'bg-rose-500' : 'bg-rose-400'}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

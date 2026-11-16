// Pipeline order; mirrors backend app/worker/pipeline.py STAGES.
const STEPS = [
  { status: 'fetching_info', label: 'Video info' },
  { status: 'downloading', label: 'Download' },
  { status: 'extracting_audio', label: 'Audio' },
  { status: 'transcribing', label: 'Transcribe' },
  { status: 'analyzing', label: 'Highlights' },
  { status: 'rendering', label: 'Render' },
]

function stepState(index, currentIndex, jobStatus) {
  if (jobStatus === 'completed') return 'done'
  if (jobStatus === 'queued') return 'todo'
  if (index < currentIndex) return 'done'
  if (index === currentIndex) return jobStatus === 'failed' ? 'failed' : 'active'
  return 'todo'
}

const DOT = {
  done: 'bg-emerald-500 text-white',
  active: 'bg-rose-500 text-white animate-pulse',
  failed: 'bg-rose-700 text-white',
  todo: 'bg-slate-800 text-slate-500',
}

/**
 * Shows where a job is in the pipeline. For failed jobs `failedStage` is the stage the job
 * was in when it failed (stored by the backend).
 */
export default function StageStepper({ status, failedStage }) {
  const current = status === 'failed' ? failedStage : status
  const currentIndex = STEPS.findIndex((s) => s.status === current)

  return (
    <ol className="flex flex-wrap items-center gap-x-1 gap-y-2 text-xs" aria-label="Processing steps">
      {STEPS.map((step, i) => {
        const state = stepState(i, currentIndex, status)
        return (
          <li key={step.status} className="flex items-center gap-1">
            <span
              className={`grid h-5 w-5 place-items-center rounded-full text-[10px] font-bold ${DOT[state]}`}
              aria-hidden="true"
            >
              {state === 'done' ? '✓' : state === 'failed' ? '!' : i + 1}
            </span>
            <span className={state === 'todo' ? 'text-slate-500' : 'text-slate-200'}>
              {step.label}
              <span className="sr-only"> ({state})</span>
            </span>
            {i < STEPS.length - 1 && <span className="mx-1 h-px w-4 bg-slate-700" aria-hidden="true" />}
          </li>
        )
      })}
    </ol>
  )
}

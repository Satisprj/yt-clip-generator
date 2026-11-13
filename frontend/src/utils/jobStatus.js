// Mirrors backend app/models/job.py JobStatus.
const STATUS = {
  queued: { label: 'Queued', tone: 'slate' },
  fetching_info: { label: 'Fetching info', tone: 'sky' },
  downloading: { label: 'Downloading', tone: 'sky' },
  extracting_audio: { label: 'Extracting audio', tone: 'sky' },
  transcribing: { label: 'Transcribing', tone: 'violet' },
  analyzing: { label: 'Finding highlights', tone: 'violet' },
  rendering: { label: 'Rendering clips', tone: 'amber' },
  completed: { label: 'Completed', tone: 'emerald' },
  failed: { label: 'Failed', tone: 'rose' },
}

export function statusInfo(status) {
  return STATUS[status] ?? { label: status, tone: 'slate' }
}

export function isTerminal(status) {
  return status === 'completed' || status === 'failed'
}

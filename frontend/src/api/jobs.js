import { API_BASE_URL, apiRequest } from './client.js'

/** URL of a rendered clip file: kind is "video", "thumbnail" or "subtitles". */
export function clipFileUrl(clipId, kind, { download = false } = {}) {
  const url = `${API_BASE_URL}/clips/${encodeURIComponent(clipId)}/${kind}`
  return download ? `${url}?download=true` : url
}

export function createJob(url) {
  return apiRequest('/jobs', { method: 'POST', body: { url } })
}

export function getJob(jobId, signal) {
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}`, { signal })
}

export function getTranscript(jobId, signal) {
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}/transcript`, { signal })
}

export function listClips(jobId, signal) {
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}/clips`, { signal })
}

export function clipsZipUrl(jobId) {
  return `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/clips.zip`
}

export function deleteJob(jobId) {
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
}

/** startFrom: "highlights" (new AI picks) or "render" (re-render the same clips). */
export function reprocessJob(jobId, startFrom) {
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}/reprocess`, {
    method: 'POST',
    body: { start_from: startFrom },
  })
}

export function freeSpace(jobId) {
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}/free-space`, { method: 'POST' })
}

export function retryJob(jobId) {
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST' })
}

export function listJobs({ limit = 20, offset = 0 } = {}, signal) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return apiRequest(`/jobs?${params}`, { signal })
}

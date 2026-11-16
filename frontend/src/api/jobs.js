import { apiRequest } from './client.js'

export function createJob(url) {
  return apiRequest('/jobs', { method: 'POST', body: { url } })
}

export function getJob(jobId, signal) {
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}`, { signal })
}

export function retryJob(jobId) {
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST' })
}

export function listJobs({ limit = 20, offset = 0 } = {}, signal) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return apiRequest(`/jobs?${params}`, { signal })
}

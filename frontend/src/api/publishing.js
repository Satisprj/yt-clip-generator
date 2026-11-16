import { apiRequest } from './client.js'

export function listIntegrations(signal) {
  return apiRequest('/integrations', { signal })
}

/** Returns the Google sign-in URL; the caller navigates the browser there. */
export function getYouTubeConnectUrl() {
  return apiRequest('/integrations/youtube/connect').then((r) => r.auth_url)
}

export function disconnectPlatform(platform) {
  return apiRequest(`/integrations/${platform}`, { method: 'DELETE' })
}

/** body: {platforms, privacy?, title?, caption?} */
export function publishClip(clipId, body) {
  return apiRequest(`/clips/${encodeURIComponent(clipId)}/publish`, { method: 'POST', body })
}

/** body: {platforms, privacy?}; each clip keeps its own AI-written caption. */
export function publishJob(jobId, body) {
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}/publish`, { method: 'POST', body })
}

export function listJobPublications(jobId, signal) {
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}/publications`, { signal })
}

export function retryPublication(publicationId) {
  return apiRequest(`/publications/${encodeURIComponent(publicationId)}/retry`, { method: 'POST' })
}

export const PLATFORM_LABELS = {
  youtube: 'YouTube Shorts',
  instagram: 'Instagram Reels',
  facebook: 'Facebook Reels',
  tiktok: 'TikTok',
}

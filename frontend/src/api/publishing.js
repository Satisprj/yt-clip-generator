import { apiRequest } from './client.js'

export function listIntegrations(signal) {
  return apiRequest('/integrations', { signal })
}

/** Sign-in URL for a connect group: "youtube", "meta" (Facebook + Instagram) or "tiktok". */
export function getConnectUrl(group) {
  return apiRequest(`/integrations/${encodeURIComponent(group)}/connect`).then((r) => r.auth_url)
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

/** After posting a TikTok inbox draft in the app, look up the post's link. */
export function findPublicationLink(publicationId) {
  return apiRequest(`/publications/${encodeURIComponent(publicationId)}/find-link`, { method: 'POST' })
}

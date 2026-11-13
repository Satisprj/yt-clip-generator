const VIDEO_ID_PATTERN = /^[A-Za-z0-9_-]{11}$/
const YOUTUBE_HOSTS = new Set(['youtube.com', 'youtube-nocookie.com'])

/**
 * Extract the 11-character video ID from common YouTube URL formats:
 * watch?v=, youtu.be/, /shorts/, /embed/, /live/, /v/. Returns null if not a video URL.
 */
export function extractYouTubeVideoId(input) {
  let url
  try {
    url = new URL(String(input).trim())
  } catch {
    return null
  }
  if (!['http:', 'https:'].includes(url.protocol)) return null

  const host = url.hostname.toLowerCase().replace(/^(www\.|m\.|music\.)/, '')
  let id = null

  if (host === 'youtu.be') {
    id = url.pathname.split('/')[1]
  } else if (YOUTUBE_HOSTS.has(host)) {
    if (url.pathname === '/watch') {
      id = url.searchParams.get('v')
    } else {
      const match = url.pathname.match(/^\/(?:shorts|embed|live|v)\/([^/]+)/)
      id = match?.[1] ?? null
    }
  }

  return id && VIDEO_ID_PATTERN.test(id) ? id : null
}

export function isValidYouTubeUrl(input) {
  return extractYouTubeVideoId(input) !== null
}

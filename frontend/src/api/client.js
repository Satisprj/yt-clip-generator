export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '')

/** Error thrown for any failed API call. `status` is 0 when the server was unreachable. */
export class ApiError extends Error {
  constructor(message, { status = 0, code = 'unknown_error', details = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

async function readJson(response) {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/**
 * Call the backend API.
 * Errors follow the backend shape {"error": {"code", "message", "details"}}.
 * `acceptStatuses` lists non-2xx statuses whose body should be returned instead of thrown.
 */
export async function apiRequest(path, { method = 'GET', body, signal, acceptStatuses = [] } = {}) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      signal,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch (err) {
    if (err.name === 'AbortError') throw err
    throw new ApiError('Cannot reach the server. Is the backend running?', { code: 'network_error' })
  }

  const data = await readJson(response)
  if (!response.ok && !acceptStatuses.includes(response.status)) {
    const error = data?.error
    throw new ApiError(error?.message ?? `Request failed with status ${response.status}`, {
      status: response.status,
      code: error?.code,
      details: error?.details,
    })
  }
  return data
}

import { apiRequest } from './client.js'

export function getHealth(signal) {
  return apiRequest('/health', { signal })
}

/** Returns {status: "ok" | "degraded", checks: {...}}. A 503 still carries the check details. */
export function getReadiness(signal) {
  return apiRequest('/health/ready', { signal, acceptStatuses: [503] })
}

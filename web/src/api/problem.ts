import type { components } from './schema.gen'

export type Problem = components['schemas']['Problem']

/** A failed API call, carrying the RFC 9457 problem+json body (spec 8.1) so
 * callers can branch on the stable `code` instead of parsing prose. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string
  readonly retryAfterSeconds?: number

  constructor(problem: Problem, status: number, retryAfterSeconds?: number) {
    super(problem.detail)
    this.name = 'ApiError'
    this.status = status
    this.code = problem.code
    this.requestId = problem.request_id
    this.retryAfterSeconds = retryAfterSeconds
  }
}

/** A request never reached the server (offline, DNS, CORS, aborted). */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super('Could not reach the server. Check your connection and try again.')
    this.name = 'NetworkError'
    this.cause = cause
  }
}

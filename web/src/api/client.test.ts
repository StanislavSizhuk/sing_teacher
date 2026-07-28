import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// client.ts creates its openapi-fetch instance at import time, capturing
// whatever `globalThis.fetch` is at that moment -- so fetch must be stubbed
// *before* the module is (dynamically) imported, not just before each test.
let client: typeof import('./client')
let sessionStore: typeof import('./sessionStore')
const fetchMock = vi.fn()

beforeAll(async () => {
  vi.stubGlobal('fetch', fetchMock)
  client = await import('./client')
  sessionStore = await import('./sessionStore')
})

beforeEach(() => {
  fetchMock.mockReset()
  sessionStore.setSession(null)
})

function jsonResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

function problemResponse(
  status: number,
  code: string,
  headers: Record<string, string> = {},
): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'x',
      status,
      detail: 'x',
      code,
      request_id: 'req-1',
    }),
    { status, headers: { 'Content-Type': 'application/problem+json', ...headers } },
  )
}

const analysisPayload = {
  id: 'a1',
  song_id: 's1',
  status: 'queued',
  created_at: '2026-01-01T00:00:00Z',
}

describe('login', () => {
  it('stores the session on success', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: 'tok', expires_in_seconds: 900 }),
    )

    const session = await client.login('a@b.com', 'password1234')

    expect(session.accessToken).toBe('tok')
    expect(client.currentAccessToken()).toBe('tok')
  })

  it('throws ApiError carrying the problem code on failure', async () => {
    fetchMock.mockResolvedValueOnce(problemResponse(401, 'INVALID_CREDENTIALS'))

    await expect(client.login('a@b.com', 'wrong')).rejects.toMatchObject({
      code: 'INVALID_CREDENTIALS',
      status: 401,
    })
  })
})

describe('401 handling', () => {
  it('refreshes once and retries the original request', async () => {
    sessionStore.setSession({ accessToken: 'stale', expiresAt: Date.now() + 1000 })
    fetchMock
      .mockResolvedValueOnce(problemResponse(401, 'UNAUTHORIZED')) // original GET
      .mockResolvedValueOnce(jsonResponse(200, { access_token: 'fresh', expires_in_seconds: 900 })) // refresh
      .mockResolvedValueOnce(jsonResponse(200, analysisPayload)) // retried GET

    const analysis = await client.getAnalysis('a1')

    expect(analysis.status).toBe('queued')
    expect(client.currentAccessToken()).toBe('fresh')
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('clears the session and surfaces the error when refresh also fails', async () => {
    sessionStore.setSession({ accessToken: 'stale', expiresAt: Date.now() + 1000 })
    fetchMock
      .mockResolvedValueOnce(problemResponse(401, 'UNAUTHORIZED'))
      .mockResolvedValueOnce(problemResponse(401, 'REFRESH_TOKEN_INVALID'))

    await expect(client.getAnalysis('a1')).rejects.toMatchObject({ code: 'UNAUTHORIZED' })
    expect(client.currentAccessToken()).toBeNull()
  })

  it('does not attempt a refresh when there was no session to begin with', async () => {
    fetchMock.mockResolvedValueOnce(problemResponse(401, 'UNAUTHORIZED'))

    await expect(client.getAnalysis('a1')).rejects.toMatchObject({ code: 'UNAUTHORIZED' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('error mapping', () => {
  it('surfaces Retry-After as retryAfterSeconds', async () => {
    fetchMock.mockResolvedValueOnce(problemResponse(429, 'QUEUE_FULL', { 'Retry-After': '30' }))

    await expect(client.getAnalysis('a1')).rejects.toMatchObject({
      code: 'QUEUE_FULL',
      retryAfterSeconds: 30,
    })
  })

  it('wraps a fetch rejection in NetworkError', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('network down'))

    await expect(client.getAnalysis('a1')).rejects.toThrow('Could not reach the server')
  })
})

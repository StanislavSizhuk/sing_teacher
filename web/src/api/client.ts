import createClient, { type Middleware } from 'openapi-fetch'

import type { paths } from './schema.gen'
import { apiBaseUrl } from './env'
import { ApiError, NetworkError, type Problem } from './problem'
import { getSession, setSession } from './sessionStore'

const raw = createClient<paths>({ baseUrl: apiBaseUrl, credentials: 'include' })

const authMiddleware: Middleware = {
  onRequest({ request }) {
    const session = getSession()
    if (session) {
      request.headers.set('Authorization', `Bearer ${session.accessToken}`)
    }
    return request
  },
}
raw.use(authMiddleware)

/** openapi-typescript has no "binary" TS primitive, so multipart schemas
 * type file fields as `string`; openapi-fetch passes a real FormData body
 * through untouched (its defaultBodySerializer special-cases `instanceof
 * FormData`), so this cast just bridges that documented gap. The type
 * argument must always be given explicitly -- inferring it from the
 * (optional) `body` position would widen it to include `undefined`. */
function asMultipartBody<T extends object>(formData: FormData): T {
  return formData as unknown as T
}

type AddSongBody = NonNullable<
  paths['/songs']['post']['requestBody']
>['content']['multipart/form-data']
type EnqueueAnalysisBody = NonNullable<
  paths['/analyses']['post']['requestBody']
>['content']['multipart/form-data']

type CallResult<Data> = { data?: Data; error?: Problem; response: Response }

let refreshInFlight: Promise<boolean> | null = null

/** Rotates the refresh cookie and mints a fresh access token. Concurrent
 * 401s share one in-flight refresh instead of each racing their own. */
function refreshSession(): Promise<boolean> {
  refreshInFlight ??= raw
    .POST('/auth/refresh')
    .then(({ data }) => {
      if (!data) {
        setSession(null)
        return false
      }
      setSession({
        accessToken: data.access_token,
        expiresAt: Date.now() + data.expires_in_seconds * 1000,
      })
      return true
    })
    .catch(() => {
      setSession(null)
      return false
    })
    .finally(() => {
      refreshInFlight = null
    })
  return refreshInFlight
}

/** Runs one API call, retrying once after a silent token refresh on 401,
 * and turns the RFC 9457 error body into a typed {@link ApiError}. This is
 * the only place in the app that touches the raw openapi-fetch client. */
async function withAuth<Data>(call: () => Promise<CallResult<Data>>): Promise<Data> {
  let result: CallResult<Data>
  try {
    result = await call()
  } catch (cause) {
    throw new NetworkError(cause)
  }

  if (result.response.status === 401 && getSession() !== null && (await refreshSession())) {
    try {
      result = await call()
    } catch (cause) {
      throw new NetworkError(cause)
    }
  }

  if (result.error !== undefined) {
    const retryAfter = result.response.headers.get('Retry-After')
    throw new ApiError(
      result.error,
      result.response.status,
      retryAfter ? Number(retryAfter) : undefined,
    )
  }
  return result.data as Data
}

export interface RegisterInput {
  email: string
  password: string
  displayName: string
}

export function register(input: RegisterInput): Promise<{ message: string }> {
  return withAuth(() =>
    raw.POST('/auth/register', {
      body: { email: input.email, password: input.password, display_name: input.displayName },
    }),
  )
}

export function verifyEmail(email: string, code: string): Promise<{ message: string }> {
  return withAuth(() => raw.POST('/auth/verify', { body: { email, code } }))
}

export function resendVerification(email: string): Promise<{ message: string }> {
  return withAuth(() => raw.POST('/auth/verify/resend', { body: { email } }))
}

export interface Session {
  accessToken: string
  expiresIn: number
}

export async function login(email: string, password: string): Promise<Session> {
  const data = await withAuth(() => raw.POST('/auth/login', { body: { email, password } }))
  setSession({
    accessToken: data.access_token,
    expiresAt: Date.now() + data.expires_in_seconds * 1000,
  })
  return { accessToken: data.access_token, expiresIn: data.expires_in_seconds }
}

/** Mints a first access token from the refresh cookie on app load (spec 8.3:
 * the Google OAuth callback never puts the token in the URL). Unlike the
 * internal 401 refresh, a failure here is expected -- it just means the
 * visitor has no session -- so it never throws. */
export async function restoreSession(): Promise<boolean> {
  return refreshSession()
}

export async function logout(): Promise<void> {
  await withAuth(() => raw.POST('/auth/logout'))
  setSession(null)
}

export interface Me {
  id: string
  email: string
  displayName: string
  emailVerified: boolean
}

export async function getMe(): Promise<Me> {
  const data = await withAuth(() => raw.GET('/me'))
  return {
    id: data.id,
    email: data.email,
    displayName: data.display_name,
    emailVerified: data.email_verified,
  }
}

export type SongSourceType = 'upload' | 'youtube'

export interface Song {
  id: string
  sourceType: SongSourceType
  title: string
  artist?: string
  durationSec: number
  vocalStemProcessed: boolean
  reused: boolean
}

function toSong(
  data: paths['/songs']['post']['responses']['201']['content']['application/json'],
): Song {
  return {
    id: data.id,
    sourceType: data.source_type,
    title: data.title,
    artist: data.artist,
    durationSec: data.duration_sec,
    vocalStemProcessed: data.vocal_stem_processed,
    reused: data.reused,
  }
}

export interface AddSongByUpload {
  sourceType: 'upload'
  title: string
  artist?: string
  file: File
}

export interface AddSongByYouTube {
  sourceType: 'youtube'
  title?: string
  youtubeUrl: string
}

export async function addSong(input: AddSongByUpload | AddSongByYouTube): Promise<Song> {
  const formData = new FormData()
  formData.set('source_type', input.sourceType)
  if (input.title) formData.set('title', input.title)
  if (input.sourceType === 'upload') {
    formData.set('file', input.file)
    if (input.artist) formData.set('artist', input.artist)
  } else {
    formData.set('youtube_url', input.youtubeUrl)
  }

  const data = await withAuth(() =>
    raw.POST('/songs', { body: asMultipartBody<AddSongBody>(formData) }),
  )
  return toSong(data)
}

export type AnalysisStatus = 'queued' | 'processing' | 'done' | 'failed' | 'canceled'

/** FR-31 piano-roll overlay data: the user's and reference's pitch curves,
 * already resampled onto the same (the user's) time grid frame for frame,
 * plus a precomputed cents deviation and off-pitch flag per frame -- the
 * client never re-derives the DTW alignment or the cents formula. */
export interface PianoRoll {
  hopSeconds: number
  userHz: (number | null)[]
  referenceHz: (number | null)[]
  deviationCents: (number | null)[]
  offPitch: boolean[]
}

export interface AspectScores {
  pitch?: number
  rhythm?: number
  vibrato?: number
  breath?: number
  dynamics?: number
  timbre?: number
}

export interface Analysis {
  id: string
  songId: string
  status: AnalysisStatus
  queuePosition?: number
  currentStage?: string
  errorCode?: string
  aspectScores: AspectScores
  overallScore?: number
  feedbackText?: string
  scoringVersion?: string
  pianoRoll?: PianoRoll
}

function toAnalysis(
  data: paths['/analyses']['post']['responses']['202']['content']['application/json'],
): Analysis {
  return {
    id: data.id,
    songId: data.song_id,
    status: data.status,
    queuePosition: data.queue_position,
    currentStage: data.current_stage,
    errorCode: data.error_code,
    aspectScores: {
      pitch: data.pitch_score,
      rhythm: data.rhythm_score,
      vibrato: data.vibrato_score,
      breath: data.breath_score,
      dynamics: data.dynamics_score,
      timbre: data.timbre_score,
    },
    overallScore: data.overall_score,
    feedbackText: data.feedback_text,
    scoringVersion: data.scoring_version,
    pianoRoll: data.piano_roll && {
      hopSeconds: data.piano_roll.hop_seconds,
      userHz: data.piano_roll.user_hz,
      referenceHz: data.piano_roll.reference_hz,
      deviationCents: data.piano_roll.deviation_cents,
      offPitch: data.piano_roll.off_pitch,
    },
  }
}

export async function enqueueAnalysis(songId: string, recording: File | Blob): Promise<Analysis> {
  const formData = new FormData()
  formData.set('song_id', songId)
  formData.set(
    'recording',
    recording,
    recording instanceof File ? recording.name : 'recording.webm',
  )

  const data = await withAuth(() =>
    raw.POST('/analyses', { body: asMultipartBody<EnqueueAnalysisBody>(formData) }),
  )
  return toAnalysis(data)
}

export async function getAnalysis(id: string): Promise<Analysis> {
  const data = await withAuth(() => raw.GET('/analyses/{id}', { params: { path: { id } } }))
  return toAnalysis(data)
}

export async function cancelAnalysis(id: string): Promise<Analysis> {
  const data = await withAuth(() => raw.POST('/analyses/{id}/cancel', { params: { path: { id } } }))
  return toAnalysis(data)
}

export async function retryAnalysis(id: string): Promise<Analysis> {
  const data = await withAuth(() => raw.POST('/analyses/{id}/retry', { params: { path: { id } } }))
  return toAnalysis(data)
}

export function currentAccessToken(): string | null {
  return getSession()?.accessToken ?? null
}

/** FR-35 progress-chart point: one completed analysis's overall_score,
 * dated. Returned oldest first. */
export interface ProgressPoint {
  analysisId: string
  overallScore: number
  createdAt: string
}

export async function getProgress(): Promise<ProgressPoint[]> {
  const data = await withAuth(() => raw.GET('/progress'))
  return data.map((point) => ({
    analysisId: point.analysis_id,
    overallScore: point.overall_score,
    createdAt: point.created_at,
  }))
}

import { ApiError, NetworkError } from '../api/problem'

interface ErrorAlertProps {
  error: unknown
}

// Problem.code -> a wording a non-technical user can act on (spec 8.1: code
// is stable and machine-readable, detail is for humans but not localized).
const FRIENDLY_MESSAGES: Record<string, string> = {
  QUEUE_FULL: 'The analysis queue is full right now. Please try again in a few minutes.',
  ANALYSIS_RATE_LIMITED: "You've reached the hourly analysis limit. Try again later.",
  ANALYSIS_NOT_QUEUED: 'This analysis can no longer be canceled.',
  ANALYSIS_NOT_FAILED: 'Only a failed analysis can be retried.',
  YOUTUBE_IMPORT_DISABLED: 'YouTube import is currently disabled.',
  INVALID_YOUTUBE_URL: 'That does not look like a valid YouTube link.',
  YOUTUBE_VIDEO_TOO_LONG: 'That video is longer than the allowed limit.',
  UNSUPPORTED_AUDIO_FORMAT: 'Unsupported audio format. Use mp3, wav, m4a, flac or ogg.',
  AUDIO_TOO_LARGE: 'That file is larger than the upload limit.',
  AUDIO_TOO_LONG: 'That recording is longer than the allowed limit.',
}

export function ErrorAlert({ error }: ErrorAlertProps) {
  if (!error) return null

  let message: string
  let retryAfterSeconds: number | undefined
  if (error instanceof ApiError) {
    message = FRIENDLY_MESSAGES[error.code] ?? error.message
    retryAfterSeconds = error.retryAfterSeconds
  } else if (error instanceof NetworkError) {
    message = error.message
  } else {
    message = 'Something went wrong. Please try again.'
  }

  return (
    <div
      role="alert"
      className="border-danger bg-danger-bg text-danger rounded border px-3 py-2 text-sm"
    >
      {message}
      {retryAfterSeconds !== undefined && <> Try again in {retryAfterSeconds}s.</>}
    </div>
  )
}

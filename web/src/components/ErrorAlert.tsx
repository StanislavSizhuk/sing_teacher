import { ApiError, NetworkError } from '../api/problem'
import { useTranslation } from '../i18n/useTranslation'
import type { Translations } from '../i18n/translations/en'

interface ErrorAlertProps {
  error: unknown
}

// Problem.code -> a wording a non-technical user can act on (spec 8.1: code
// is stable and machine-readable, detail is for humans but not localized).
function friendlyMessages(t: Translations): Record<string, string> {
  return {
    QUEUE_FULL: t.errorAlert.queueFull,
    ANALYSIS_RATE_LIMITED: t.errorAlert.analysisRateLimited,
    ANALYSIS_NOT_QUEUED: t.errorAlert.analysisNotQueued,
    ANALYSIS_NOT_FAILED: t.errorAlert.analysisNotFailed,
    YOUTUBE_IMPORT_DISABLED: t.errorAlert.youtubeImportDisabled,
    INVALID_YOUTUBE_URL: t.errorAlert.invalidYoutubeUrl,
    YOUTUBE_VIDEO_TOO_LONG: t.errorAlert.youtubeVideoTooLong,
    UNSUPPORTED_AUDIO_FORMAT: t.errorAlert.unsupportedAudioFormat,
    AUDIO_TOO_LARGE: t.errorAlert.audioTooLarge,
    AUDIO_TOO_LONG: t.errorAlert.audioTooLong,
    REFERENCE_PREP_FAILED: t.errorAlert.referencePrepFailed,
  }
}

export function ErrorAlert({ error }: ErrorAlertProps) {
  const t = useTranslation()
  if (!error) return null

  let message: string
  let retryAfterSeconds: number | undefined
  if (error instanceof ApiError) {
    message = friendlyMessages(t)[error.code] ?? error.message
    retryAfterSeconds = error.retryAfterSeconds
  } else if (error instanceof NetworkError) {
    message = error.message
  } else {
    message = t.errorAlert.generic
  }

  return (
    <div
      role="alert"
      className="border-danger bg-danger-bg text-danger rounded border px-3 py-2 text-sm"
    >
      {message}
      {retryAfterSeconds !== undefined && t.errorAlert.retryAfter(retryAfterSeconds)}
    </div>
  )
}

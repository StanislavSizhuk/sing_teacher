import { useMutation } from '@tanstack/react-query'

import { prepareSong, type Song } from '../../api/client'
import { Button } from '../../components/Button'
import { ErrorAlert } from '../../components/ErrorAlert'
import { useTranslation } from '../../i18n/useTranslation'

interface SongPrepFailedNoticeProps {
  song: Song
  onRetried: (song: Song) => void
}

/** FR-17: a song whose cold path already failed needs an explicit restart
 * (POST /songs/{id}/prepare) -- re-uploading the same audio never
 * re-triggers it, since upload.go's own content-hash dedup just hands back
 * the same dead row (`reused: true`). Surfaced here, at the point the user
 * would otherwise record their take for nothing, rather than only at
 * analysis-submit time (spec 8.1's REFERENCE_PREP_FAILED). */
export function SongPrepFailedNotice({ song, onRetried }: SongPrepFailedNoticeProps) {
  const t = useTranslation()
  const retry = useMutation({
    mutationFn: () => prepareSong(song.id),
    onSuccess: onRetried,
  })

  return (
    <div className="flex w-full max-w-md flex-col gap-3">
      <p className="border-danger bg-danger-bg text-danger rounded border px-3 py-2 text-sm">
        {t.songPrepFailed.body}
      </p>
      <ErrorAlert error={retry.error} />
      <Button onClick={() => retry.mutate()} disabled={retry.isPending}>
        {retry.isPending ? t.songPrepFailed.retryPending : t.songPrepFailed.retry}
      </Button>
    </div>
  )
}

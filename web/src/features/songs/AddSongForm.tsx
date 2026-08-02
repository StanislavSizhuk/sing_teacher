import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import { addSong, type AddSongByUpload, type AddSongByYouTube, type Song } from '../../api/client'
import { Button } from '../../components/Button'
import { ErrorAlert } from '../../components/ErrorAlert'
import { SegmentedControl } from '../../components/SegmentedControl'
import { TextField } from '../../components/TextField'
import { useTranslation } from '../../i18n/useTranslation'

interface AddSongFormProps {
  onAdded: (song: Song) => void
}

const ACCEPTED_AUDIO = '.mp3,.wav,.m4a,.flac,.ogg,audio/*'

export function AddSongForm({ onAdded }: AddSongFormProps) {
  const t = useTranslation()
  const [sourceType, setSourceType] = useState<'upload' | 'youtube'>('upload')
  const [title, setTitle] = useState('')
  const [artist, setArtist] = useState('')
  const [youtubeUrl, setYoutubeUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)

  const mutation = useMutation({
    mutationFn: (input: AddSongByUpload | AddSongByYouTube) => addSong(input),
    onSuccess: onAdded,
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (sourceType === 'upload') {
      if (!file) return
      mutation.mutate({ sourceType: 'upload', title, artist: artist || undefined, file })
    } else {
      mutation.mutate({ sourceType: 'youtube', title: title || undefined, youtubeUrl })
    }
  }

  const canSubmit =
    sourceType === 'upload' ? title.trim() !== '' && file !== null : youtubeUrl.trim() !== ''

  return (
    <form onSubmit={handleSubmit} className="flex w-full max-w-md flex-col gap-4">
      <h1 className="text-ink-950 text-lg font-semibold">{t.addSong.heading}</h1>

      <SegmentedControl
        label={t.addSong.sourceLabel}
        value={sourceType}
        onChange={setSourceType}
        options={[
          { value: 'upload', label: t.addSong.sourceUpload },
          { value: 'youtube', label: t.addSong.sourceYoutube },
        ]}
      />

      {sourceType === 'upload' ? (
        <>
          <TextField
            label={t.addSong.title}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={200}
          />
          <TextField
            label={t.addSong.artist}
            value={artist}
            onChange={(e) => setArtist(e.target.value)}
            maxLength={200}
          />
          <div className="flex flex-col gap-1">
            <label htmlFor="song-file" className="text-ink-700 text-sm font-medium">
              {t.addSong.audioFile}
            </label>
            <input
              id="song-file"
              type="file"
              accept={ACCEPTED_AUDIO}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              required
              className="text-ink-700 text-sm file:mr-3 file:cursor-pointer file:rounded file:border file:border-ink-950 file:bg-ink-950 file:px-4 file:py-2 file:text-sm file:font-medium file:text-ink-0 file:transition-colors hover:file:bg-ink-700 hover:file:border-ink-700"
            />
            <p className="text-ink-500 text-xs">{t.addSong.audioFileHint}</p>
          </div>
        </>
      ) : (
        <>
          <p className="border-ink-300 bg-ink-100 text-ink-700 rounded border px-3 py-2 text-xs">
            {t.addSong.youtubeDisclaimer}
          </p>
          <TextField
            label={t.addSong.youtubeUrl}
            type="url"
            value={youtubeUrl}
            onChange={(e) => setYoutubeUrl(e.target.value)}
            required
            maxLength={2048}
            placeholder={t.addSong.youtubeUrlPlaceholder}
          />
          <TextField
            label={t.addSong.titleOverride}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={200}
          />
        </>
      )}

      <ErrorAlert error={mutation.error} />
      <Button type="submit" disabled={!canSubmit || mutation.isPending}>
        {mutation.isPending ? t.addSong.submitPending : t.addSong.submit}
      </Button>
      {!canSubmit && (
        <p className="text-ink-500 text-xs">
          {sourceType === 'upload' ? t.addSong.hintUpload : t.addSong.hintYoutube}
        </p>
      )}
    </form>
  )
}

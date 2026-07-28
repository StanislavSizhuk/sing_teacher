import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import { addSong, type AddSongByUpload, type AddSongByYouTube, type Song } from '../../api/client'
import { Button } from '../../components/Button'
import { ErrorAlert } from '../../components/ErrorAlert'
import { TextField } from '../../components/TextField'

interface AddSongFormProps {
  onAdded: (song: Song) => void
}

const ACCEPTED_AUDIO = '.mp3,.wav,.m4a,.flac,.ogg,audio/*'

export function AddSongForm({ onAdded }: AddSongFormProps) {
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
      <h1 className="text-ink-950 text-lg font-semibold">Add a song</h1>

      <div role="radiogroup" aria-label="Song source" className="flex gap-2">
        <button
          type="button"
          role="radio"
          aria-checked={sourceType === 'upload'}
          onClick={() => setSourceType('upload')}
          className={`flex-1 rounded border px-3 py-2 text-sm ${
            sourceType === 'upload'
              ? 'bg-ink-950 border-ink-950 text-ink-0'
              : 'border-ink-300 text-ink-700'
          }`}
        >
          Upload file
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={sourceType === 'youtube'}
          onClick={() => setSourceType('youtube')}
          className={`flex-1 rounded border px-3 py-2 text-sm ${
            sourceType === 'youtube'
              ? 'bg-ink-950 border-ink-950 text-ink-0'
              : 'border-ink-300 text-ink-700'
          }`}
        >
          YouTube link
        </button>
      </div>

      {sourceType === 'upload' ? (
        <>
          <TextField
            label="Title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={200}
          />
          <TextField
            label="Artist (optional)"
            value={artist}
            onChange={(e) => setArtist(e.target.value)}
            maxLength={200}
          />
          <div className="flex flex-col gap-1">
            <label htmlFor="song-file" className="text-ink-700 text-sm font-medium">
              Audio file
            </label>
            <input
              id="song-file"
              type="file"
              accept={ACCEPTED_AUDIO}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              required
              className="text-ink-700 text-sm"
            />
            <p className="text-ink-500 text-xs">
              mp3, wav, m4a, flac or ogg. Up to 15 MB / 6 minutes.
            </p>
          </div>
        </>
      ) : (
        <>
          <TextField
            label="YouTube URL"
            type="url"
            value={youtubeUrl}
            onChange={(e) => setYoutubeUrl(e.target.value)}
            required
            maxLength={2048}
            placeholder="https://www.youtube.com/watch?v=..."
          />
          <TextField
            label="Title override (optional)"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={200}
          />
        </>
      )}

      <ErrorAlert error={mutation.error} />
      <Button type="submit" disabled={!canSubmit || mutation.isPending}>
        {mutation.isPending ? 'Adding song…' : 'Add song'}
      </Button>
    </form>
  )
}

import { useCallback, useEffect, useRef, useState } from 'react'

import { useObjectUrl } from '../../hooks/useObjectUrl'

export type RecorderState = 'idle' | 'requesting' | 'recording' | 'recorded' | 'error'

interface UseMediaRecorderResult {
  state: RecorderState
  audioUrl: string | null
  blob: Blob | null
  error: string | null
  start: () => Promise<void>
  stop: () => void
  reset: () => void
}

// Safari (as of iOS 16, spec NFR-12's baseline) only supports mp4/aac, so
// this list is tried in order rather than hardcoded to one type.
const PREFERRED_MIME_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg']

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined
  return PREFERRED_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type))
}

/** Wraps the browser MediaRecorder API for FR-20: record, preview, and
 * re-record. The recording never leaves the browser until the caller
 * explicitly submits it. */
export function useMediaRecorder(): UseMediaRecorderResult {
  const [state, setState] = useState<RecorderState>('idle')
  const [blob, setBlob] = useState<Blob | null>(null)
  const [error, setError] = useState<string | null>(null)
  const audioUrl = useObjectUrl(blob)

  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const releaseStream = useCallback(() => {
    for (const track of streamRef.current?.getTracks() ?? []) track.stop()
    streamRef.current = null
  }, [])

  const start = useCallback(async () => {
    setError(null)
    setState('requesting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      chunksRef.current = []
      const recorder = new MediaRecorder(stream, { mimeType: pickMimeType() })
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        setBlob(new Blob(chunksRef.current, { type: recorder.mimeType }))
        setState('recorded')
        releaseStream()
      }
      recorderRef.current = recorder
      recorder.start()
      setState('recording')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not access the microphone.')
      setState('error')
      releaseStream()
    }
  }, [releaseStream])

  const stop = useCallback(() => {
    recorderRef.current?.stop()
  }, [])

  const reset = useCallback(() => {
    setBlob(null)
    setError(null)
    setState('idle')
  }, [])

  useEffect(() => releaseStream, [releaseStream])

  return { state, audioUrl, blob, error, start, stop, reset }
}

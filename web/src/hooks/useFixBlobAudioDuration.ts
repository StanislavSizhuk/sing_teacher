import { useEffect, type RefObject } from 'react'

/** Chromium writes a `MediaRecorder` WebM blob without a Duration element --
 * it can't know the final length while it's still streaming chunks -- which
 * makes the resulting `<audio>` report `duration === Infinity` and display
 * "0:00 / 0:00" until played through once. Seeking near the end forces the
 * browser to scan the blob and compute the real duration immediately; the
 * `timeupdate` that seek fires is the signal to snap back to the start. A
 * file with a proper duration in its container (upload, any non-WebM
 * format) never hits `duration === Infinity`, so this is a no-op for it. */
export function useFixBlobAudioDuration(
  audioRef: RefObject<HTMLAudioElement | null>,
  src: string | null,
): void {
  useEffect(() => {
    const audio = audioRef.current
    if (!audio || !src) return

    function onLoadedMetadata() {
      if (audio.duration !== Infinity) return
      function onTimeUpdate() {
        audio.removeEventListener('timeupdate', onTimeUpdate)
        audio.currentTime = 0
      }
      audio.addEventListener('timeupdate', onTimeUpdate)
      audio.currentTime = Number.MAX_SAFE_INTEGER
    }

    audio.addEventListener('loadedmetadata', onLoadedMetadata)
    return () => audio.removeEventListener('loadedmetadata', onLoadedMetadata)
  }, [audioRef, src])
}

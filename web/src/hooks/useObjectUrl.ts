import { useEffect, useMemo } from 'react'

// A <input type="file">-selected File's `.type` is whatever the OS's mime
// database infers from its extension -- empty on systems without one
// registered for it. An object URL with no type set gets no content-type
// at all, and <audio>/<video> refuse to play it (MEDIA_ERR_SRC_NOT_SUPPORTED)
// instead of sniffing the bytes. Fall back to the extension ourselves for
// the formats this app already accepts.
const EXTENSION_MIME_TYPES: Record<string, string> = {
  mp3: 'audio/mpeg',
  wav: 'audio/wav',
  m4a: 'audio/mp4',
  flac: 'audio/flac',
  ogg: 'audio/ogg',
  webm: 'audio/webm',
}

function withMimeType(source: File | Blob): File | Blob {
  if (source.type || !(source instanceof File)) return source
  const extension = source.name.split('.').pop()?.toLowerCase()
  const type = extension && EXTENSION_MIME_TYPES[extension]
  return type ? new Blob([source], { type }) : source
}

/** Creates an object URL for a File/Blob, derived synchronously during
 * render, and revokes the previous one whenever it's replaced or on
 * unmount. */
export function useObjectUrl(source: File | Blob | null): string | null {
  const url = useMemo(
    () => (source ? URL.createObjectURL(withMimeType(source)) : null),
    [source],
  )

  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url)
    }
  }, [url])

  return url
}

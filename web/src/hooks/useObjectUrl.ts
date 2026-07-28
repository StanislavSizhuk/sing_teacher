import { useEffect, useMemo } from 'react'

/** Creates an object URL for a File/Blob, derived synchronously during
 * render, and revokes the previous one whenever it's replaced or on
 * unmount. */
export function useObjectUrl(source: File | Blob | null): string | null {
  const url = useMemo(() => (source ? URL.createObjectURL(source) : null), [source])

  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url)
    }
  }, [url])

  return url
}

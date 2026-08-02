import { act, renderHook } from '@testing-library/react'
import { createRef } from 'react'
import { describe, expect, it } from 'vitest'

import { useFixBlobAudioDuration } from './useFixBlobAudioDuration'

function audioWithDuration(duration: number): HTMLAudioElement {
  const audio = document.createElement('audio')
  Object.defineProperty(audio, 'duration', { value: duration, configurable: true })
  return audio
}

describe('useFixBlobAudioDuration', () => {
  it('seeks to the end and back to force a real duration on an Infinity-duration blob', () => {
    const audio = audioWithDuration(Infinity)
    const ref = createRef<HTMLAudioElement | null>()
    ref.current = audio

    renderHook(() => useFixBlobAudioDuration(ref, 'blob:fake'))
    act(() => {
      audio.dispatchEvent(new Event('loadedmetadata'))
    })
    expect(audio.currentTime).toBe(Number.MAX_SAFE_INTEGER)

    act(() => {
      audio.dispatchEvent(new Event('timeupdate'))
    })
    expect(audio.currentTime).toBe(0)
  })

  it('leaves a file with a real duration untouched', () => {
    const audio = audioWithDuration(12.5)
    const ref = createRef<HTMLAudioElement | null>()
    ref.current = audio

    renderHook(() => useFixBlobAudioDuration(ref, 'blob:fake'))
    act(() => {
      audio.dispatchEvent(new Event('loadedmetadata'))
    })

    expect(audio.currentTime).toBe(0)
  })

  it('does nothing without a src', () => {
    const audio = audioWithDuration(Infinity)
    const ref = createRef<HTMLAudioElement | null>()
    ref.current = audio

    renderHook(() => useFixBlobAudioDuration(ref, null))
    act(() => {
      audio.dispatchEvent(new Event('loadedmetadata'))
    })

    expect(audio.currentTime).toBe(0)
  })
})

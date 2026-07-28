import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useMediaRecorder } from './useMediaRecorder'

class FakeMediaRecorder {
  static isTypeSupported = () => true
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  mimeType = 'audio/webm'

  start() {
    // no-op: recording state is driven by the hook itself
  }

  stop() {
    this.ondataavailable?.({ data: new Blob(['chunk'], { type: this.mimeType }) })
    this.onstop?.()
  }
}

function fakeStream(): MediaStream {
  return { getTracks: () => [{ stop: vi.fn() }] } as unknown as MediaStream
}

let getUserMedia: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  getUserMedia = vi.fn().mockResolvedValue(fakeStream())
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia },
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useMediaRecorder', () => {
  it('goes idle -> recording -> recorded, producing a blob and a preview url', async () => {
    const { result } = renderHook(() => useMediaRecorder())
    expect(result.current.state).toBe('idle')

    await act(async () => {
      await result.current.start()
    })
    expect(result.current.state).toBe('recording')

    act(() => result.current.stop())

    await waitFor(() => expect(result.current.state).toBe('recorded'))
    expect(result.current.blob).not.toBeNull()
    expect(result.current.audioUrl).toMatch(/^blob:/)
  })

  it('surfaces a permission error instead of throwing', async () => {
    getUserMedia.mockRejectedValueOnce(new Error('Permission denied'))
    const { result } = renderHook(() => useMediaRecorder())

    await act(async () => {
      await result.current.start()
    })

    expect(result.current.state).toBe('error')
    expect(result.current.error).toMatch(/permission denied/i)
  })

  it('reset clears the recording back to idle', async () => {
    const { result } = renderHook(() => useMediaRecorder())
    await act(async () => {
      await result.current.start()
    })
    act(() => result.current.stop())
    await waitFor(() => expect(result.current.state).toBe('recorded'))

    act(() => result.current.reset())

    expect(result.current.state).toBe('idle')
    expect(result.current.blob).toBeNull()
    expect(result.current.audioUrl).toBeNull()
  })
})

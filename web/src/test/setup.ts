import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// No global test framework auto-registers RTL's cleanup here (globals:
// false in vite.config.ts, deliberately, to keep test files explicit about
// their imports), so it has to be wired up once, by hand.
afterEach(cleanup)

// jsdom has no Blob URL registry; every feature that previews a recording
// (useObjectUrl, useMediaRecorder, <audio src>) needs these to exist.
if (!URL.createObjectURL) {
  let counter = 0
  URL.createObjectURL = () => `blob:mock-${String(counter++)}`
  URL.revokeObjectURL = () => {}
}

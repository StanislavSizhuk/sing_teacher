export type Language = 'en' | 'uk'

const STORAGE_KEY = 'language'

function detectDefault(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'en' || stored === 'uk') return stored
  } catch {
    // Storage blocked (private mode, policy) -- fall through to detection.
  }
  return navigator.language.toLowerCase().startsWith('uk') ? 'uk' : 'en'
}

let language: Language = detectDefault()
const listeners = new Set<() => void>()

export function getLanguage(): Language {
  return language
}

export function setLanguage(next: Language): void {
  language = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // Not persisted this session; the in-memory switch below still applies.
  }
  for (const listener of listeners) listener()
}

export function subscribeLanguage(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

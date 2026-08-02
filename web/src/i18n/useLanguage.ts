import { useSyncExternalStore } from 'react'

import { getLanguage, setLanguage, subscribeLanguage, type Language } from './language'

/** Reactive read of the current UI language; re-renders on switch. */
export function useLanguage(): [Language, (next: Language) => void] {
  const language = useSyncExternalStore(subscribeLanguage, getLanguage)
  return [language, setLanguage]
}

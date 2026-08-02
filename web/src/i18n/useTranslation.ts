import { dictionaries, type Translations } from './translations'
import { useLanguage } from './useLanguage'

/** The current language's dictionary -- static keys read directly
 * (`t.app.title`), templated ones called as functions
 * (`t.queueStatus.numberInQueue(5)`). Both dictionaries share one type
 * (`Translations = typeof en`), so every key exists and is shaped
 * identically in every language; there is no missing-key fallback path to
 * reason about. */
export function useTranslation(): Translations {
  const [language] = useLanguage()
  return dictionaries[language]
}

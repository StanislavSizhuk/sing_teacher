import type { Language } from '../language'
import { en, type Translations } from './en'
import { uk } from './uk'

export type { Translations }

export const dictionaries: Record<Language, Translations> = { en, uk }

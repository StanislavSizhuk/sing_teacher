/** Ukrainian has three count-based plural categories (one/few/many, e.g.
 * "1 нота", "2 ноти", "5 нот") where English only has two -- a ternary
 * (`n === 1 ? '' : 's'`) that works for English text silently produces the
 * wrong word for every other count in Ukrainian. `Intl.PluralRules` picks
 * the grammatically correct category for the given locale and count; each
 * language's dictionary supplies its own `forms`. */
export interface PluralForms {
  one?: string
  few?: string
  many?: string
  other: string
}

export function pluralize(locale: string, count: number, forms: PluralForms): string {
  const category = new Intl.PluralRules(locale).select(count)
  return forms[category as keyof PluralForms] ?? forms.other
}

import { describe, expect, it } from 'vitest'

import { pluralize } from './plural'

describe('pluralize', () => {
  it('picks English one/other', () => {
    const forms = { one: 'note', other: 'notes' }
    expect(pluralize('en', 1, forms)).toBe('note')
    expect(pluralize('en', 0, forms)).toBe('notes')
    expect(pluralize('en', 2, forms)).toBe('notes')
    expect(pluralize('en', 21, forms)).toBe('notes')
  })

  it('picks Ukrainian one/few/many for whole numbers', () => {
    const forms = { one: 'сесія', few: 'сесії', many: 'сесій', other: 'сесії' }
    // 1, 21, 31... -> one; 2-4, 22-24... -> few; 0, 5-20, 25-30... -> many.
    expect(pluralize('uk', 1, forms)).toBe('сесія')
    expect(pluralize('uk', 21, forms)).toBe('сесія')
    expect(pluralize('uk', 2, forms)).toBe('сесії')
    expect(pluralize('uk', 3, forms)).toBe('сесії')
    expect(pluralize('uk', 4, forms)).toBe('сесії')
    expect(pluralize('uk', 22, forms)).toBe('сесії')
    expect(pluralize('uk', 0, forms)).toBe('сесій')
    expect(pluralize('uk', 5, forms)).toBe('сесій')
    expect(pluralize('uk', 11, forms)).toBe('сесій')
    expect(pluralize('uk', 20, forms)).toBe('сесій')
  })

  it('falls back to `other` for a category not present in the given forms', () => {
    // No `few`/`many` supplied -- 2 in Ukrainian is `few`, which isn't here.
    expect(pluralize('uk', 2, { other: 'фолбек' })).toBe('фолбек')
  })
})

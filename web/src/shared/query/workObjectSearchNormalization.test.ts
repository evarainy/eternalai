import { describe, expect, it } from 'vitest';
import contract from '../../../../tests/contracts/work_object_search_normalization.json';
import { normalizeSearchQuery, normalizeSearchValue } from './workObjectSearchNormalization';

describe('shared normalization vectors', () => {
  it.each(contract.normalization)('$id', ({ input, expected }) => {
    if (input !== null) {
      expect(normalizeSearchValue(input)).toBe(expected);
    }
    expect(normalizeSearchQuery(input)).toBe(expected || null);
  });

  it.each(contract.matching)('matches $field: $query', (vector) => {
    const field = normalizeSearchValue(vector.input);
    const query = normalizeSearchValue(vector.query);
    expect(vector.field === 'title' ? field.includes(query) : field === query)
      .toBe(vector.expected);
  });
});

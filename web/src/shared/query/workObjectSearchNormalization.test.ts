import { describe, expect, it, vi } from 'vitest';
import contract from '../../../../tests/contracts/work_object_search_normalization.json';
import { normalizeSearchQuery, normalizeSearchValue } from './workObjectSearchNormalization';

describe('shared normalization vectors', () => {
  it('uses ordinary toLowerCase without locale-dependent casing', () => {
    // Output vectors alone cannot distinguish zh-CN casing from ordinary lower.
    // Observe the actual operation without replacing either implementation.
    const ordinaryLower = vi.spyOn(String.prototype, 'toLowerCase');
    const localeLower = vi.spyOn(String.prototype, 'toLocaleLowerCase');
    let result: string;
    let ordinaryCalls: unknown[][];
    let localeCalls: unknown[][];
    try {
      result = normalizeSearchValue(' OA REF ');
      ordinaryCalls = [...ordinaryLower.mock.calls];
      localeCalls = [...localeLower.mock.calls];
    } finally {
      ordinaryLower.mockRestore();
      localeLower.mockRestore();
    }

    expect(result).toBe('oa ref');
    expect(ordinaryCalls).toEqual([[]]);
    expect(localeCalls).toEqual([]);
  });

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

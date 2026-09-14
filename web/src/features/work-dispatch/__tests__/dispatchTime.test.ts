import { describe, expect, it } from 'vitest';
import { localTime, timeChoices } from '../dispatchTime';

describe('browser time contract', () => {
  it.each([
    ['2026-09-11T01:30', 'Asia/Tokyo', '2026-09-10T16:30:00Z', 'UTC+09:00'],
    ['2026-09-10T23:30', 'America/Los_Angeles', '2026-09-11T06:30:00Z', 'UTC-07:00'],
    ['2026-01-01T00:15', 'Pacific/Kiritimati', '2025-12-31T10:15:00Z', 'UTC+14:00'],
  ])('T1 converts_browser_zone_vectors %s %s', (input, zone, instant, offset) => {
    expect(timeChoices(input, zone)).toEqual([{ instant, offset }]);
    expect(localTime(instant, zone)).toBe(input);
  });
  it('T4 rejects_gaps_and_requires_overlap_choice', () => {
    expect(timeChoices('2026-03-08T02:30', 'America/New_York')).toEqual([]);
    expect(timeChoices('2026-11-01T01:30', 'America/New_York')).toEqual([
      { instant: '2026-11-01T05:30:00Z', offset: 'UTC-04:00' },
      { instant: '2026-11-01T06:30:00Z', offset: 'UTC-05:00' },
    ]);
    expect(localTime('2026-09-10T16:30:00Z', 'Asia/Shanghai')).toBe('2026-09-11T00:30');
    expect(localTime('2026-09-10T16:30:00Z', 'Asia/Tokyo')).toBe('2026-09-11T01:30');
  });
  it.each(['', '2026-02-30T10:00', '2026-01-01T24:30', 'bad'])('T4 rejects invalid calendar input %s', (input) => {
    expect(timeChoices(input, 'Asia/Tokyo')).toEqual([]);
  });
  it('fails closed for unknown browser zone', () => {
    expect(timeChoices('2026-01-01T01:30', 'Invalid/Zone')).toEqual([]);
  });
});

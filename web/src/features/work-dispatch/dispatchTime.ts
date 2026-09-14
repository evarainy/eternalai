export interface TimeChoice { instant: string; offset: string }

export function browserZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function calendar(instant: number, zone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(instant);
  const get = (type: string) => parts.find((part) => part.type === type)?.value;
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`;
}

export function localTime(instant: string, zone: string): string {
  return calendar(Date.parse(instant), zone);
}

/** Enumerate every minute offset in the civil UTC -14..+14 range and round-trip
 * through the requested IANA zone. No Date normalization or overlap default. */
export function timeChoices(value: string, zone: string): TimeChoice[] {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return [];
  const wall = Date.parse(`${value}:00Z`);
  if (!Number.isFinite(wall) || new Date(wall).toISOString().slice(0, 16) !== value) return [];
  const choices: TimeChoice[] = [];
  try {
    // Reuse the formatter for all candidates; offsets are for this date, never now.
    const formatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    });
    for (let offset = 840; offset >= -840; offset -= 1) {
      const instant = wall - offset * 60_000;
      const parts = formatter.formatToParts(instant);
      const get = (type: string) => parts.find((part) => part.type === type)?.value;
      if (`${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}` !== value) continue;
      const absolute = Math.abs(offset);
      choices.push({ instant: new Date(instant).toISOString().replace('.000Z', 'Z'),
        offset: `UTC${offset < 0 ? '-' : '+'}${String(Math.floor(absolute / 60)).padStart(2, '0')}:${String(absolute % 60).padStart(2, '0')}` });
    }
  } catch { return []; }
  return choices;
}

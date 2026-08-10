const promptStructuralCharacters = new Set(['\\', '`', '|', '<', '>', '{', '}', '[', ']']);
const nonPrintableCharacterPattern = /[\p{C}\p{Z}]/u;
const asciiPattern = /^\p{ASCII}*$/u;
const intentTagPattern = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/u;

export const promptSafeLimits = {
  name: 120,
  owner: 120,
  short_description: 500,
} as const;

const intentTagMaxItems = 32;
const intentTagMaxLength = 64;

function normalizedLength(value: string): number {
  return Array.from(value).length;
}

function containsNonPrintableCharacter(value: string): boolean {
  return Array.from(value).some(
    (character) => character !== ' ' && nonPrintableCharacterPattern.test(character),
  );
}

export function normalizePromptSafeText(
  value: string,
  label: string,
  maxLength: number,
): string {
  const normalized = value.normalize('NFKC');
  if (containsNonPrintableCharacter(normalized)) {
    throw new Error(`${label} 不能包含换行、控制字符或不可打印字符`);
  }
  if (Array.from(normalized).some((character) => promptStructuralCharacters.has(character))) {
    throw new Error(`${label} 不能包含 prompt 结构字符`);
  }

  const canonical = normalized.trim();
  if (!canonical) {
    throw new Error(`${label} 去除首尾空格后不能为空`);
  }
  if (normalizedLength(canonical) > maxLength) {
    throw new Error(`${label} 最多 ${maxLength} 个字符`);
  }
  return canonical;
}

function normalizeIntentTag(value: string): string {
  const normalized = value.normalize('NFKC');
  if (containsNonPrintableCharacter(normalized)) {
    throw new Error('Intent Tag 不能包含换行、控制字符或不可打印字符');
  }
  if (!asciiPattern.test(normalized)) {
    throw new Error('Intent Tag 只能使用 ASCII 字母、数字及 ._-');
  }

  const canonical = normalized.trim().toLowerCase();
  if (normalizedLength(canonical) > intentTagMaxLength) {
    throw new Error(`Intent Tag 每项最多 ${intentTagMaxLength} 个字符`);
  }
  if (!intentTagPattern.test(canonical)) {
    throw new Error('Intent Tag 必须匹配 a-z、0-9 及单个 ._- 分隔的 slug 形态');
  }
  return canonical;
}

export function normalizeIntentTags(values: string[]): string[] {
  if (values.length > intentTagMaxItems) {
    throw new Error(`Intent Tags 最多 ${intentTagMaxItems} 项`);
  }
  return values.map(normalizeIntentTag);
}

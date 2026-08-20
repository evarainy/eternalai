import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { relative, resolve } from 'node:path';

const webRoot = process.cwd();
const sourceRoot = resolve(webRoot, 'src');
const vitestEntry = resolve(webRoot, 'node_modules/vitest/vitest.mjs');

function findComponentTests(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolutePath = resolve(directory, entry.name);
    if (entry.isDirectory()) {
      return findComponentTests(absolutePath);
    }
    return entry.name.endsWith('.test.tsx') ? [absolutePath] : [];
  });
}

const componentTests = findComponentTests(sourceRoot).sort();
if (componentTests.length === 0) {
  throw new Error('No React component tests were found under src.');
}

for (const testFile of componentTests) {
  const relativeTestFile = relative(webRoot, testFile).replaceAll('\\', '/');
  const result = spawnSync(process.execPath, [vitestEntry, '--run', relativeTestFile], {
    cwd: webRoot,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    windowsHide: true,
  });
  process.stdout.write(result.stdout ?? '');
  process.stderr.write(result.stderr ?? '');
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const generatedDir = resolve(__dirname, '..', 'src', 'generated');

function* walkSync(dir) {
  for (const entry of readdirSync(dir)) {
    const full = resolve(dir, entry);
    if (statSync(full).isDirectory()) {
      yield* walkSync(full);
    } else if (entry.endsWith('.ts')) {
      yield full;
    }
  }
}

for (const file of walkSync(generatedDir)) {
  const content = readFileSync(file, 'utf-8');
  const lines = content.split('\n');
  const cleaned = lines.map((line) => line.replace(/[ \t]+$/g, ''));
  while (cleaned.length > 0 && cleaned[cleaned.length - 1] === '') {
    cleaned.pop();
  }
  writeFileSync(file, cleaned.join('\n') + '\n', 'utf-8');
  console.log('normalized:', file);
}

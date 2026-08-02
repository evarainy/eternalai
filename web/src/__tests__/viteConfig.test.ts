import { execFileSync } from 'node:child_process';
import { describe, expect, it } from 'vitest';

describe('Vite API proxy', () => {
  it('uses the configured backend while preserving the /api/v1 path', () => {
    const probe = `
      import { loadConfigFromFile } from 'vite';
      const loaded = await loadConfigFromFile(
        { command: 'serve', isPreview: false, isSsrBuild: false, mode: 'test' },
        undefined,
        process.cwd(),
      );
      if (!loaded) throw new Error('Vite config was not loaded');
      const proxy = loaded.config.server?.proxy?.['/api/v1'];
      const result = {
        fileParallelism: loaded.config.test?.fileParallelism,
        target: typeof proxy === 'string' ? proxy : proxy?.target,
        hasRewrite:
          typeof proxy === 'object' &&
          proxy !== null &&
          Object.prototype.hasOwnProperty.call(proxy, 'rewrite'),
      };
      process.stdout.write('VITE_PROXY_PROBE=' + JSON.stringify(result));
    `;
    const output = execFileSync(
      process.execPath,
      ['--input-type=module', '--eval', probe],
      {
        cwd: process.cwd(),
        encoding: 'utf8',
        env: {
          ...process.env,
          ETERNALAI_BACKEND_URL: 'http://127.0.0.1:18000',
        },
      },
    );
    const result = JSON.parse(output.split('VITE_PROXY_PROBE=').at(-1) ?? '{}');

    expect(result).toEqual({
      fileParallelism: false,
      hasRewrite: false,
      target: 'http://127.0.0.1:18000',
    });
  }, 30_000);
});

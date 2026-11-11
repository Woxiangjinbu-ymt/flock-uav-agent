import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

type PackageJson = {
  scripts: Record<string, string>;
};

const packageJson = JSON.parse(readFileSync('package.json', 'utf8')) as PackageJson;

for (const [script, dataSource] of [
  ['dev', 'backend'],
  ['dev:backend', 'backend'],
  ['dev:demo', 'demo']
] as const) {
  test(`npm run ${script} explicitly selects ${dataSource} across platforms`, () => {
    assert.deepEqual(packageJson.scripts[script].trim().split(/\s+/), [
      'cross-env',
      `VITE_VIEWER_DATA_SOURCE=${dataSource}`,
      'vite',
      '--host', '127.0.0.1',
      '--port', '5173'
    ]);
  });
}

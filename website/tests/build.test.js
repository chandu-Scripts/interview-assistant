import { test } from 'node:test';
import assert from 'node:assert/strict';
import { access, mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { buildAdmin } from '../build-admin.mjs';

test('configured artifact pins CSP to its exact public project and disables admin caching', async () => {
  await mkdir('test-results', { recursive: true });
  const folder = await mkdtemp(path.resolve('test-results/admin-build-'));
  const configFile = path.join(folder, 'config.json');
  const output = path.join(folder, 'dist');
  const config = { url: 'https://fixture.supabase.co', publishableKey: 'sb_publishable_fixture' };
  await writeFile(configFile, JSON.stringify(config));
  await buildAdmin({ output, configFile });
  assert.deepEqual(JSON.parse(await readFile(path.join(output, 'admin/config.json'), 'utf8')), config);
  const deployment = JSON.parse(await readFile(path.join(output, 'vercel.json'), 'utf8'));
  const headers = deployment.headers[0].headers;
  const csp = headers.find(header => header.key === 'Content-Security-Policy').value;
  assert.match(csp, /connect-src 'self' https:\/\/fixture\.supabase\.co;/);
  assert.match(csp, /script-src 'self';/);
  assert.match(csp, /frame-ancestors 'none';/);
  assert.doesNotMatch(csp, /unsafe-|https:\/\/\*/);
  assert.deepEqual(deployment.headers.at(-1), { source: '/admin(.*)', headers: [{ key: 'Cache-Control', value: 'no-store' }] });
  const bundle = await readFile(path.join(output, 'admin/admin.js'), 'utf8');
  assert.ok(bundle.length > 1000);
});

test('invalid configuration is rejected before creating a public artifact', async () => {
  await mkdir('test-results', { recursive: true });
  const folder = await mkdtemp(path.resolve('test-results/admin-rejected-'));
  for (const [index, config] of [
    { url: 'https://fixture.supabase.co', publishableKey: 'sb_secret_never_publish' },
    { url: '', publishableKey: '', serviceRole: 'synthetic-secret' },
    { url: 'https://fixture.supabase.co', publishableKey: 'sb_publishable_fixture', unexpected: true },
  ].entries()) {
    const configFile = path.join(folder, `config-${index}.json`);
    const output = path.join(folder, `dist-${index}`);
    await writeFile(configFile, JSON.stringify(config));
    await assert.rejects(buildAdmin({ output, configFile }));
    await assert.rejects(access(output));
  }
});

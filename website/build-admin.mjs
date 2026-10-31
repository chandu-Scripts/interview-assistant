import { build } from 'esbuild';
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateConfig } from './admin/session.js';

const root = path.dirname(fileURLToPath(import.meta.url));

export async function readAdminConfig(configFile = path.join(root, 'public/admin/config.json')) {
  const raw = JSON.parse(await readFile(configFile, 'utf8'));
  const config = validateConfig(raw);
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Invalid admin configuration.');
  if ((raw.url || raw.publishableKey) && !config) throw new Error('Admin config must contain a hosted Supabase URL and public publishable key only.');
  if (Object.keys(raw).some(key => !['url', 'publishableKey'].includes(key))) throw new Error('Unexpected admin configuration field.');
  return config;
}

export async function buildAdmin({ output = path.join(root, 'dist'), configFile } = {}) {
  const config = await readAdminConfig(configFile);
  await mkdir(path.join(output, 'admin'), { recursive: true });
  await cp(path.join(root, 'public/admin'), path.join(output, 'admin'), { recursive: true });
  await writeFile(path.join(output, 'admin/config.json'), JSON.stringify(config ?? { url: '', publishableKey: '' }, null, 2) + '\n');
  await cp(path.join(root, 'public/assets'), path.join(output, 'assets'), { recursive: true });
  await build({ entryPoints: [path.join(root, 'admin/main.js')], bundle: true, format: 'esm',
    outfile: path.join(output, 'admin/admin.js'), target: ['es2022'], minify: true, sourcemap: false });
  const headers = JSON.parse(await readFile(path.join(root, 'vercel.json'), 'utf8'));
  for (const rule of headers.headers) {
    for (const header of rule.headers) {
      if (header.key === 'Content-Security-Policy') header.value += `; connect-src 'self'${config ? ` ${config.url}` : ''}; form-action 'self'`;
    }
  }
  headers.headers.push({ source: '/admin(.*)', headers: [{ key: 'Cache-Control', value: 'no-store' }] });
  await writeFile(path.join(output, 'vercel.json'), JSON.stringify(headers, null, 2) + '\n');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await buildAdmin();
  console.log('Built local admin page. No deployment performed.');
}

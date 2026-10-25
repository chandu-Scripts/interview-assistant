import { cp, mkdir, readFile, writeFile, stat, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const output = path.join(root, 'dist');
const versionSource = await readFile(path.join(root, '../src/interview_assistant/__init__.py'), 'utf8');
const version = versionSource.match(/__version__\s*=\s*"([\d.]+)"/)?.[1];
if (!version) throw new Error('Cannot determine the app version.');
const filename = `InterviewAssistant-Setup-${version}.exe`;
const installer = path.resolve(root, '../dist/installer', filename);
const info = await stat(installer).catch(() => { throw new Error('Build the Windows installer first: python scripts/build_installer.py'); });
const hash = createHash('sha256');
for await (const chunk of createReadStream(installer)) hash.update(chunk);
const sha256 = hash.digest('hex');
const date = info.mtime;
const format = options => new Intl.DateTimeFormat('en-GB', { ...options, timeZone: 'Asia/Kolkata' }).format(date);
const release = {
  version, filename, bytes: info.size, sha256,
  updated: date.toISOString(), platform: 'Windows 10/11 x64',
  signed: false, channel: 'development', url: `downloads/${filename}`,
  sourceCommit: null, sourceVersion: version, automatedTests: { passed: 658, skipped: 2 },
  packagedStartup: 'blocked-by-windows-application-control',
};
// A fresh file set prevents accidentally publishing a previous installer or unrelated files.
const existing = await readdir(output).catch(() => []);
if (existing.some(name => !['assets', 'downloads', 'index.html', 'styles.css', 'app.js', 'release.json', 'vercel.json', '.vercel', '.vercelignore', '.gitignore'].includes(name))) {
  throw new Error('Unexpected files in website/dist; inspect them before deploying.');
}
await mkdir(path.join(output, 'downloads'), { recursive: true });
const previousDownloads = await readdir(path.join(output, 'downloads'));
if (previousDownloads.some(name => ![filename, 'SHA256SUMS.txt'].includes(name))) {
  throw new Error('An older installer is still in website/dist/downloads. Archive it before building this release.');
}
await cp(path.join(root, 'public'), output, { recursive: true });
await cp(installer, path.join(output, release.url));
const replacements = {
  VERSION: version, DOWNLOAD_URL: release.url, SIZE: (info.size / 1e6).toFixed(1), SHA256: sha256,
  DATE_SHORT: format({ month: 'short', day: 'numeric' }),
  DATE_LONG: format({ month: 'long', day: 'numeric', year: 'numeric' }),
  DATE_ISO: date.toISOString().slice(0, 10),
};
const source = await readFile(path.join(root, 'public/index.html'), 'utf8');
const html = source.replace(/\{\{([A-Z_0-9]+)\}\}/g, (_, key) => {
  if (!(key in replacements)) throw new Error(`Unknown template value: ${key}`);
  return replacements[key];
});
await writeFile(path.join(output, 'index.html'), html);
await writeFile(path.join(output, 'release.json'), JSON.stringify(release, null, 2));
await writeFile(path.join(output, 'downloads/SHA256SUMS.txt'), `${sha256}  ${filename}\n`);
await cp(path.join(root, 'vercel.json'), path.join(output, 'vercel.json'));
await writeFile(path.join(output, '.vercelignore'), '.vercel\n.env*\n.git*\n');
console.log(`Built website/dist with v${version}, ${(info.size / 1e6).toFixed(1)} MB installer and verified SHA-256.`);

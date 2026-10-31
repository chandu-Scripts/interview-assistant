// Actual Postgres/RLS execution with synthetic Supabase Auth tables and claims.
// PGLITE_MODULE points to pinned @electric-sql/pglite@0.5.14/dist/index.js.
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';

const { PGlite } = await import(pathToFileURL(process.env.PGLITE_MODULE).href);
const db = new PGlite();
const here = dirname(fileURLToPath(import.meta.url));
const admin = '00000000-0000-4000-8000-000000000001';
const a = '00000000-0000-4000-8000-000000000002';
const b = '00000000-0000-4000-8000-000000000003';
const session = id => id.replace('4000', '4001');
let assertions = 0;
function check(value, expected) { assert.deepEqual(value, expected); assertions++; }
async function scalar(sql, params = []) { return (await db.query(sql, params)).rows[0].value; }
async function asUser(user, operation, extra = {}, role = 'authenticated') {
  await db.exec(`set role ${role}`);
  const claims = { sub: user, session_id: session(user), role: 'authenticated', exp: Math.floor(Date.now()/1000)+3600, ...extra };
  await db.query("select set_config('request.jwt.claims', $1, false)", [JSON.stringify(claims)]);
  try { return await operation(); } finally { await db.exec('reset role'); }
}
async function denied(operation, code = '42501') {
  await assert.rejects(operation, error => error.code === code);
  assertions++;
}
async function snapshot(user) {
  return asUser(user, () => scalar('select public.customer_access_snapshot() as value'));
}
async function grant(user, capability, version, enabled = true) {
  return asUser(admin, () => scalar(`select public.customer_admin_set_grant($1, $2, now() + interval '1 day', $3, 'manual', $4) as value`, [user, capability, enabled, version]));
}
async function block(user, value, version) {
  return asUser(admin, () => scalar(`select public.customer_admin_set_block($1, $2, 'support', $3) as value`, [user, value, version]));
}

try {
  await db.exec(`
    create role anon nologin;
    create role authenticated nologin;
    create schema auth;
    create table auth.users(id uuid primary key, created_at timestamptz default now(), banned_until timestamptz);
    create table auth.sessions(id uuid primary key, user_id uuid references auth.users(id), not_after timestamptz);
    create function auth.jwt() returns jsonb language sql stable as $$ select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $$;
    create function auth.uid() returns uuid language sql stable as $$ select (auth.jwt()->>'sub')::uuid $$;
    grant usage on schema auth to authenticated, anon;
    grant execute on function auth.uid(), auth.jwt() to authenticated, anon;
  `);
  for (const id of [admin, a, b]) {
    await db.query('insert into auth.users(id) values ($1)', [id]);
    await db.query('insert into auth.sessions(id,user_id) values ($1,$2)', [session(id), id]);
  }
  const migrations = (await readdir(resolve(here, '../migrations'))).filter(name => name.endsWith('_customer_entitlements.sql'));
  check(migrations.length, 1);
  await db.exec(await readFile(resolve(here, '../migrations', migrations[0]), 'utf8'));
  check(await scalar("select count(*)::integer as value from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='customer_private' and c.relkind='r' and c.relrowsecurity"), 4);
  check(await scalar("select count(*)::integer as value from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname like 'customer_%' and p.prosecdef"), 0);
  check(await scalar("select count(*)::integer as value from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='customer_private' and has_function_privilege('anon',p.oid,'execute')"), 0);
  await db.query('insert into customer_private.admins(user_id) values ($1)', [admin]);

  check((await snapshot(a)).reason, 'no_grant');
  await grant(a, 'desktop_access', 0);
  const approved = await snapshot(a);
  check(approved.allowed, true);
  check(approved.user_id, a);
  check(approved.lease_seconds > 0 && approved.lease_seconds <= 60, true);
  const deniedB = await snapshot(b);
  check(deniedB.allowed, false);
  check(await asUser(a, () => scalar("select public.customer_require_access('desktop_access') as value")), true);
  await denied(() => asUser(b, () => scalar("select public.customer_require_access('desktop_access') as value")));
  await denied(() => asUser(a, () => scalar("select public.customer_require_access('cloud_backup') as value")));
  await denied(() => asUser(a, () => scalar('select public.customer_admin_list() as value'), {user_metadata: {admin: true}, app_metadata: {admin: true}}));
  await denied(() => asUser(a, () => scalar(`select public.customer_admin_set_block($1, true, 'policy', 0) as value`, [b])));
  await denied(() => asUser(a, () => scalar(`select public.customer_admin_set_grant($1,'desktop_access',now()+interval '1 day',true,'manual',0) as value`, [b])));
  await denied(() => asUser(a, () => scalar('select public.customer_admin_audit() as value')));
  await denied(() => asUser(a, () => scalar('select public.customer_access_snapshot() as value'), {}, 'anon'));
  await denied(() => asUser(a, () => scalar('select public.customer_access_snapshot() as value'), {is_anonymous: true}));
  await denied(() => asUser(a, () => db.query('select * from customer_private.grants')));
  await denied(() => asUser(a, () => db.query("update customer_private.accounts set blocked=false")));
  await denied(() => asUser(a, () => db.query('insert into customer_private.admins(user_id) values ($1)', [a])));
  await denied(() => asUser(a, () => db.query('delete from customer_private.audit')));

  // Even an accidental SELECT grant must reveal no rows without a matching policy.
  await db.exec('grant select on all tables in schema customer_private to authenticated');
  for (const table of ['accounts', 'admins', 'grants', 'audit']) {
    check((await asUser(a, () => db.query(`select * from customer_private.${table}`))).rows, []);
  }
  await db.exec('revoke select on all tables in schema customer_private from authenticated');

  await block(a, true, 1);
  const blockedA = await snapshot(a);
  check(blockedA.reason, 'blocked');
  await denied(() => asUser(a, () => scalar("select public.customer_require_access('desktop_access') as value")));
  await denied(() => block(a, false, 1), '40001');
  await block(a, false, 2);
  check((await snapshot(a)).allowed, true);
  await db.query("update auth.sessions set not_after=now()+interval '5 seconds' where user_id=$1", [a]);
  const shortSession = await snapshot(a);
  check(shortSession.lease_seconds > 0 && shortSession.lease_seconds <= 5, true);
  await db.query('update auth.sessions set not_after=null where user_id=$1', [a]);
  await grant(a, 'desktop_access', 3, false);
  check((await snapshot(a)).reason, 'no_grant');
  await grant(a, 'desktop_access', 4);
  await db.query("update customer_private.grants set expires_at=now()-interval '1 second' where user_id=$1", [a]);
  check((await snapshot(a)).reason, 'expired');
  await denied(() => asUser(a, () => scalar("select public.customer_require_access('desktop_access') as value")));

  const rows = await asUser(admin, () => scalar('select public.customer_admin_list(null, 2) as value'));
  check(rows.map(row => row.user_id), [admin, a]);
  check(Object.keys(rows[0]).sort(), ['blocked', 'created_at', 'grants', 'is_admin', 'last_seen_at', 'user_id', 'version'].sort());
  check((await asUser(admin, () => scalar('select public.customer_admin_list($1, 2) as value', [a]))).map(row => row.user_id), [b]);
  const audit = await asUser(admin, () => scalar('select public.customer_admin_audit() as value'));
  check(audit.length, 5);
  check(audit.map(row => row.action), ['grant', 'block', 'unblock', 'revoke', 'grant']);
  await denied(() => block(admin, true, 0), '22023');
  await denied(() => asUser(admin, () => scalar('select public.customer_admin_list(null, 1000) as value')), '22023');

  await db.query('delete from auth.sessions where user_id=$1', [b]);
  await denied(() => snapshot(b));
  await denied(() => asUser(a, () => scalar('select public.customer_access_snapshot() as value'), {exp: 1}));
  await denied(() => asUser(a, () => scalar('select public.customer_access_snapshot() as value'), {session_id: session(admin)}));
  await db.query("update auth.sessions set not_after=now()-interval '1 second' where user_id=$1", [a]);
  await denied(() => snapshot(a));
  console.log(JSON.stringify({passed: assertions, engine: 'PGlite 0.5.14 (Postgres)', hosted: false,
    snapshots: {approved, deniedB, blockedA}}));
} catch (error) {
  console.error(JSON.stringify({message: error.message, code: error.code, detail: error.detail, where: error.where}));
  process.exitCode = 1;
} finally {
  await db.close();
}

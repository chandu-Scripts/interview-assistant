import { test } from 'node:test';
import assert from 'node:assert/strict';
import { memoryStore, predecessor, validateAudit, validateConfig, validateUsers } from '../admin/session.js';

test('revoked in-memory store refuses late SDK writes and cannot restore old tokens', () => {
  const storage = memoryStore();
  storage.setItem('session', 'synthetic-token');
  assert.equal(storage.getItem('session'), 'synthetic-token');
  storage.revoke();
  storage.setItem('session', 'late-rotated-token');
  assert.equal(storage.getItem('session'), null);
});

test('only configured HTTPS hosted projects and public publishable keys are accepted', () => {
  const valid = { url: 'https://fixture.supabase.co', publishableKey: 'sb_publishable_public' };
  assert.deepEqual(validateConfig(valid), valid);
  for (const url of ['http://fixture.supabase.co', 'https://attacker.example', 'https://fixture.supabase.co.evil.test', 'https://fixture.supabase.co/path']) {
    assert.equal(validateConfig({ ...valid, url }), null);
  }
  for (const publishableKey of ['', 'sb_secret_private', 'eyJ_legacy_unclassified']) {
    assert.equal(validateConfig({ ...valid, publishableKey }), null);
  }
});

test('UUID keyset search handles zero, carry and exact ordering', () => {
  assert.equal(predecessor('00000000-0000-0000-0000-000000000000'), null);
  assert.equal(predecessor('00000000-0000-0000-0001-000000000000'), '00000000-0000-0000-0000-ffffffffffff');
  assert.equal(predecessor('ffffffff-ffff-ffff-ffff-ffffffffffff'), 'ffffffff-ffff-ffff-ffff-fffffffffffe');
});

test('unsafe versions, malformed grants and unknown audit fields fail closed', () => {
  const row = { user_id: '00000000-0000-0000-0000-000000000001', version: 0, blocked: false, is_admin: false, grants: [] };
  assert.deepEqual(validateUsers([row]), [row]);
  for (const version of [-1, NaN, '0', Number.MAX_SAFE_INTEGER + 1]) assert.throws(() => validateUsers([{ ...row, version }]));
  assert.throws(() => validateUsers([{ ...row, grants: [{ capability: 'owner', enabled: true, expires_at: '2035-01-01' }] }]));
  assert.throws(() => validateUsers([row, row]));
  assert.throws(() => validateAudit([{ id: Number.MAX_SAFE_INTEGER + 1 }]));
  assert.throws(() => validateAudit([{ id: 1, actor_id: row.user_id, target_user_id: row.user_id, action: '<script>', version: 1, reason_code: 'manual', capability: null }]));
});

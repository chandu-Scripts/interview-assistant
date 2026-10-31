import { createClient } from '@supabase/supabase-js';

export const PAGE_SIZE = 25;
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const CAPABILITIES = ['desktop_access', 'cloud_backup'];
export const REASONS = ['support', 'policy', 'owner_request', 'trial', 'manual', 'correction'];
export const isVersion = value => Number.isSafeInteger(value) && value >= 0;

export function validateConfig(config) {
  if (!config || typeof config.url !== 'string' || typeof config.publishableKey !== 'string') return null;
  if (Object.keys(config).some(key => !['url', 'publishableKey'].includes(key))) return null;
  // Only a public key for the explicitly configured hosted project is accepted.
  if (!/^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(config.url)
      || !/^sb_publishable_[A-Za-z0-9_-]+$/.test(config.publishableKey)) return null;
  return config;
}

export function predecessor(uuid) {
  const number = BigInt(`0x${uuid.replaceAll('-', '')}`);
  if (number === 0n) return null;
  const hex = (number - 1n).toString(16).padStart(32, '0');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function validateUsers(rows) {
  if (!Array.isArray(rows) || rows.length > PAGE_SIZE) throw new Error('Invalid response');
  let previous = '';
  for (const row of rows) {
    if (!UUID.test(row?.user_id) || row.user_id <= previous || !isVersion(row.version)
        || typeof row.blocked !== 'boolean' || typeof row.is_admin !== 'boolean'
        || !Array.isArray(row.grants) || row.grants.length > 2) throw new Error('Invalid response');
    previous = row.user_id;
    const seen = new Set();
    for (const grant of row.grants) {
      if (!CAPABILITIES.includes(grant.capability) || seen.has(grant.capability)
          || typeof grant.enabled !== 'boolean' || !Number.isFinite(Date.parse(grant.expires_at))) {
        throw new Error('Invalid response');
      }
      seen.add(grant.capability);
    }
  }
  return rows;
}

export function validateAudit(rows) {
  if (!Array.isArray(rows) || rows.length > PAGE_SIZE) throw new Error('Invalid response');
  let previous = 0;
  for (const row of rows) {
    if (!Number.isSafeInteger(row.id) || row.id <= previous || !UUID.test(row.actor_id)
        || !UUID.test(row.target_user_id) || !['block', 'unblock', 'grant', 'revoke'].includes(row.action)
        || !REASONS.includes(row.reason_code) || !isVersion(row.version)
        || !(row.capability === null || CAPABILITIES.includes(row.capability))) throw new Error('Invalid response');
    previous = row.id;
  }
  return rows;
}

// Supabase honors a custom storage adapter only with persistSession enabled.
// This store has no browser persistence and rejects all writes after teardown.
export function memoryStore() {
  const values = new Map();
  let alive = true;
  return {
    getItem: key => alive ? values.get(key) ?? null : null,
    setItem: (key, value) => { if (alive) values.set(key, value); },
    removeItem: key => values.delete(key),
    revoke: () => { alive = false; values.clear(); },
  };
}

export function createOwner(config, onExpired) {
  const storage = memoryStore();
  const controller = new AbortController();
  const owner = {
    client: null, controller, storage, alive: true, userId: null, token: null,
    deadline: 0, wallExpiry: 0, timer: null, subscription: null,
  };
  owner.client = createClient(config.url, config.publishableKey, {
    auth: {
      storage, storageKey: `chandoo-admin-${crypto.randomUUID()}`,
      persistSession: true, autoRefreshToken: false, detectSessionInUrl: false,
    },
    global: {
      fetch: (input, init = {}) => {
        const isLogout = String(input).startsWith(`${config.url}/auth/v1/logout?`);
        const signals = [AbortSignal.timeout(isLogout ? 5000 : 15000)];
        if (!isLogout) signals.push(controller.signal);
        if (init.signal) signals.push(init.signal);
        return fetch(input, { ...init, signal: AbortSignal.any(signals), cache: 'no-store' });
      },
    },
  });
  owner.subscription = owner.client.auth.onAuthStateChange((event, session) => {
    if (!owner.alive) return;
    if (event === 'SIGNED_OUT') { onExpired(owner); return; }
    if (!session) return;
    if (!UUID.test(session.user?.id) || !Number.isFinite(session.expires_at)) {
      onExpired(owner); return;
    }
    if (owner.userId && session.user.id !== owner.userId) { onExpired(owner); return; }
    owner.userId = session.user.id;
    owner.token = session.access_token;
    // Refresh never extends the original administration window.
    const ttl = Math.max(0, Math.min(15 * 60 * 1000, session.expires_at * 1000 - Date.now()));
    owner.deadline = Math.min(owner.deadline || Infinity, performance.now() + ttl);
    owner.wallExpiry = Math.min(owner.wallExpiry || Infinity, session.expires_at * 1000);
    clearTimeout(owner.timer);
    owner.timer = setTimeout(() => onExpired(owner), Math.max(0, owner.deadline - performance.now()));
  }).data.subscription;
  return owner;
}

export function current(owner) {
  return owner?.alive && owner.userId && performance.now() < owner.deadline && Date.now() < owner.wallExpiry;
}

export function disposeOwner(owner, revoke = true) {
  if (!owner?.alive) return;
  owner.alive = false;
  clearTimeout(owner.timer);
  owner.subscription?.unsubscribe();
  const token = owner.token;
  owner.token = null;
  owner.userId = null;
  owner.storage.revoke();
  owner.controller.abort();
  // This endpoint receives only this session's JWT, never a service/admin key.
  // Start revocation after local authority is gone; a failed network call cannot restore it.
  const logout = revoke && token ? owner.client.auth.admin.signOut(token, 'local') : Promise.resolve();
  void logout.catch(() => {}).finally(() => owner.client.auth.dispose());
}

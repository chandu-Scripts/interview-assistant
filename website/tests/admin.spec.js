import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const API = 'https://fixture.supabase.co';
const ACTOR = '00000000-0000-4000-8000-000000000001';
const TARGET = '00000000-0000-4000-8000-000000000030';
const EXPIRY = '2035-01-01T00:00:00.000Z';
const user = (id = TARGET, extra = {}) => ({ user_id: id, blocked: false, version: 0,
  is_admin: id === ACTOR, created_at: '2026-01-01T00:00:00Z', last_seen_at: null, grants: [], ...extra });
const event = id => ({ id, actor_id: ACTOR, target_user_id: TARGET, action: 'grant',
  capability: 'desktop_access', reason_code: 'trial', version: id, created_at: '2026-01-01T00:00:00Z', expires_at: EXPIRY });

async function fixture(page, options = {}) {
  const state = { users: [user(ACTOR), user()], events: [], requests: [], writes: [], fail: null, ...options };
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin === 'http://127.0.0.1:4187') {
      if (url.pathname === '/admin/config.json' && !state.unconfigured) {
        await route.fulfill({ json: { url: API, publishableKey: 'sb_publishable_fixture_public' } });
      } else if (['/admin/', '/admin'].includes(url.pathname)) {
        const response = await route.fetch();
        await route.fulfill({ response, headers: { ...response.headers(),
          'content-security-policy': `default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' ${API}; object-src 'none'; base-uri 'self'; form-action 'self'` } });
      } else await route.continue();
      return;
    }
    if (url.origin !== API) { await route.abort(); return; }
    state.requests.push({ path: url.pathname, params: url.search, body: route.request().postDataJSON() });
    if (url.pathname === '/auth/v1/token') {
      if (state.badPassword) { await route.fulfill({ status: 400, json: { error: 'invalid_grant', error_description: '<script>private server error</script>' } }); return; }
      const expires = Math.floor(Date.now() / 1000) + (state.ttl ?? 3600);
      const part = value => Buffer.from(JSON.stringify(value)).toString('base64url');
      const token = `${part({ alg: 'HS256', typ: 'JWT' })}.${part({ sub: ACTOR, exp: expires, role: 'authenticated' })}.fixture`;
      await route.fulfill({ json: { access_token: token, refresh_token: 'fake-refresh-secret', token_type: 'bearer', expires_in: state.ttl ?? 3600, expires_at: expires,
        user: { id: ACTOR, aud: 'authenticated', role: 'authenticated', email: 'admin@example.test',
          app_metadata: {}, user_metadata: { is_admin: true, role: 'admin' }, created_at: '2026-01-01T00:00:00Z' } } });
      return;
    }
    if (url.pathname === '/auth/v1/logout') { await route.fulfill({ status: state.logoutFailure ? 500 : 204 }); return; }
    if (!url.pathname.startsWith('/rest/v1/rpc/')) { await route.abort(); return; }
    const name = url.pathname.split('/').at(-1);
    const body = route.request().postDataJSON();
    if (state.deny) { await route.fulfill({ status: 403, json: { code: '42501', message: 'SECRET backend detail' } }); return; }
    if (state.delay && name === 'customer_admin_list') await state.delay;
    if (state.fail) { await route.fulfill({ status: 500, json: { code: 'XX000', message: '<img src=x onerror=alert(1)>SECRET' } }); return; }
    if (name === 'customer_admin_list') {
      await route.fulfill({ json: state.malformed ?? state.users.filter(item => !body.after_user_id || item.user_id > body.after_user_id).slice(0, body.page_size) });
    } else if (name === 'customer_admin_audit') {
      await route.fulfill({ json: state.events.filter(item => !body.after_id || item.id > body.after_id).slice(0, body.page_size) });
    } else {
      state.writes.push({ name, body });
      const item = state.users.find(candidate => candidate.user_id === body.target_user_id);
      if (state.conflict || body.expected_version !== item.version) {
        state.conflict = false;
        item.version += 1;
        await route.fulfill({ status: 409, json: { code: '40001', message: 'Stale version' } });
        return;
      }
      item.version += 1;
      if (name === 'customer_admin_set_block') item.blocked = body.blocked;
      else item.grants = [{ capability: body.capability, enabled: body.enabled, expires_at: body.expires_at }];
      await route.fulfill({ json: { user_id: item.user_id, version: item.version, ...body } });
    }
  });
  await page.goto('/admin/');
  return state;
}

async function login(page) {
  await page.getByLabel('Email', { exact: true }).fill('admin@example.test');
  await page.getByLabel('Password', { exact: true }).fill('fake-password-never-store');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

async function ready(page) {
  await login(page);
  await expect(page.getByText('Administrator access verified.', { exact: false })).toBeVisible();
}

const targetRow = page => page.locator('#users tr').filter({ hasText: TARGET });

async function confirm(page) {
  await page.getByLabel('Reason', { exact: true }).selectOption('support');
  await page.getByLabel('Type the exact user ID to confirm').fill(TARGET);
  await page.getByRole('button', { name: 'Confirm change' }).click();
}

test('unconfigured page fails closed without authentication calls', async ({ page }) => {
  const state = await fixture(page, { unconfigured: true });
  await expect(page.getByText('Admin setup is not configured for this website.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeDisabled();
  expect(state.requests).toHaveLength(0);
});

test('login, block/unblock and exact target confirmation use optimistic version', async ({ page }) => {
  const state = await fixture(page);
  await ready(page);
  await mkdir('output/playwright', { recursive: true });
  await page.screenshot({ path: 'output/playwright/customer-admin-desktop.png', fullPage: true, animations: 'disabled' });
  await expect(page.locator('#users tr').filter({ hasText: ACTOR }).getByRole('button', { name: 'Block', exact: true })).toBeDisabled();
  await targetRow(page).getByRole('button', { name: 'Block', exact: true }).click();
  await page.getByLabel('Reason', { exact: true }).selectOption('policy');
  await page.getByLabel('Type the exact user ID to confirm').fill(ACTOR);
  await page.getByRole('button', { name: 'Confirm change' }).click();
  await expect(page.getByText('Choose a reason and enter the exact customer user ID.')).toBeVisible();
  expect(state.writes).toHaveLength(0);
  await confirm(page);
  await expect(targetRow(page).getByRole('button', { name: 'Unblock', exact: true })).toBeVisible();
  expect(state.writes[0].body).toEqual({ target_user_id: TARGET, expected_version: 0, reason_code: 'support', blocked: true });
  await targetRow(page).getByRole('button', { name: 'Unblock', exact: true }).click();
  await confirm(page);
  await expect(targetRow(page).getByRole('button', { name: 'Block', exact: true })).toBeVisible();
  expect(state.writes[1].body.expected_version).toBe(1);
});

test('grant requires future expiry, revoke preserves existing expiry', async ({ page }) => {
  const state = await fixture(page);
  await ready(page);
  await targetRow(page).getByRole('button', { name: 'Grant Desktop', exact: true }).click();
  await page.getByLabel('Access expiry (your local time)').fill('2020-01-01T00:00');
  await confirm(page);
  await expect(page.getByText('Choose a future expiry.')).toBeVisible();
  expect(state.writes).toHaveLength(0);
  await page.getByLabel('Access expiry (your local time)').fill('2035-01-01T00:00');
  await confirm(page);
  await expect(targetRow(page).getByRole('button', { name: 'Revoke Desktop' })).toBeVisible();
  const expiry = state.writes[0].body.expires_at;
  await targetRow(page).getByRole('button', { name: 'Revoke Desktop' }).click();
  await confirm(page);
  await expect(targetRow(page).getByRole('button', { name: 'Revoke Desktop' })).toHaveCount(0);
  expect(state.writes[1].body).toEqual({ target_user_id: TARGET, expected_version: 1, reason_code: 'support', capability: 'desktop_access', expires_at: expiry, enabled: false });
});

test('version conflict refreshes and requires new explicit confirmation', async ({ page }) => {
  const state = await fixture(page, { conflict: true });
  await ready(page);
  await targetRow(page).getByRole('button', { name: 'Block', exact: true }).click();
  await confirm(page);
  await expect(page.getByText('This account changed.', { exact: false })).toBeVisible();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  expect(state.writes).toHaveLength(1);
  await targetRow(page).getByRole('button', { name: 'Block', exact: true }).click();
  await expect(page.getByLabel('Type the exact user ID to confirm')).toHaveValue('');
  await confirm(page);
  await expect(targetRow(page).getByRole('button', { name: 'Unblock', exact: true })).toBeVisible();
  expect(state.writes[1].body.expected_version).toBe(1);
});

test('ordinary user with forged admin metadata is denied and data is cleared', async ({ page }) => {
  const state = await fixture(page, { deny: true });
  await login(page);
  await expect(page.getByText('Administrator access is unavailable.', { exact: false })).toBeVisible();
  await expect(page.locator('#workspace')).not.toBeVisible();
  await expect(page.locator('#users')).toBeEmpty();
  await expect(page.getByText('SECRET backend detail')).toHaveCount(0);
  expect(state.writes).toHaveLength(0);
});

test('logout clears data, dialog, password and all browser storage even if remote logout fails', async ({ page }) => {
  await fixture(page, { logoutFailure: true });
  await ready(page);
  await targetRow(page).getByRole('button', { name: 'Block', exact: true }).click();
  // Keyboard Escape dismisses the modal before the always-available session control.
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByText('Signed out. This tab has been cleared.')).toBeVisible();
  await expect(page.locator('#users')).toBeEmpty();
  await expect(page.locator('#action-target')).toBeEmpty();
  await expect(page.getByLabel('Password', { exact: true })).toHaveValue('');
  expect(await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }))).toEqual({ local: {}, session: {} });
  await page.reload();
  await expect(page.locator('#workspace')).not.toBeVisible();
});

test('delayed response after logout cannot restore table or authority', async ({ page }) => {
  const state = await fixture(page);
  await ready(page);
  let release;
  state.delay = new Promise(resolve => { release = resolve; });
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  release();
  await expect(page.locator('#workspace')).not.toBeVisible();
  await expect(page.locator('#users')).toBeEmpty();
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
});

test('session expiry clears open dialog and stops further protected requests', async ({ page }) => {
  await page.clock.install();
  const state = await fixture(page, { ttl: 120 });
  await ready(page);
  await targetRow(page).getByRole('button', { name: 'Block', exact: true }).click();
  await page.clock.fastForward(121 * 1000);
  await expect(page.getByText('Your session has expired. Sign in again.')).toBeVisible();
  await expect(page.locator('#users')).toBeEmpty();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  expect(state.writes).toHaveLength(0);
});

test('credentials remain memory-only during an active session; logout is scoped to this session', async ({ page }) => {
  const state = await fixture(page);
  await ready(page);
  expect(await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage }, cookies: document.cookie })))
    .toEqual({ local: {}, session: {}, cookies: '' });
  await expect(page.getByLabel('Password', { exact: true })).toHaveValue('');
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect.poll(() => state.requests.some(item => item.path === '/auth/v1/logout')).toBe(true);
  expect(state.requests.find(item => item.path === '/auth/v1/logout').params).toBe('?scope=local');
});

test('unavailable refresh clears stale records and does not expose server errors', async ({ page }) => {
  const state = await fixture(page);
  await ready(page);
  state.fail = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText('Unable to load this view.', { exact: false })).toBeVisible();
  await expect(page.locator('#users')).toBeEmpty();
  await expect(page.getByText('SECRET', { exact: false })).toHaveCount(0);
});

test('server session revocation on a later request clears previously loaded data', async ({ page }) => {
  const state = await fixture(page);
  await ready(page);
  state.deny = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText('Administrator access is unavailable.', { exact: false })).toBeVisible();
  await expect(page.locator('#users')).toBeEmpty();
});

test('keyset pages, bounded exact UUID search, and audit pagination', async ({ page }) => {
  const ids = Array.from({ length: 27 }, (_, index) => `00000000-0000-4000-8000-${(index + 1).toString(16).padStart(12, '0')}`);
  const state = await fixture(page, { users: ids.map(id => user(id)), events: Array.from({ length: 26 }, (_, index) => event(index + 1)) });
  await ready(page);
  await expect(page.locator('#users tr')).toHaveCount(25);
  await page.locator('#next-users').click();
  await expect(page.locator('#users tr')).toHaveCount(2);
  await page.locator('#previous-users').click();
  await expect(page.locator('#users tr')).toHaveCount(25);
  const before = state.requests.length;
  await page.getByLabel('User ID (UUID)').fill(ids[26]);
  await page.getByRole('button', { name: 'Find', exact: true }).click();
  await expect(page.locator('#users tr')).toHaveCount(1);
  expect(state.requests.length - before).toBe(1);
  expect(state.requests.at(-1).body.after_user_id).toBe(ids[25]);
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await expect(page.locator('#audit tr')).toHaveCount(25);
  await page.locator('#next-audit').click();
  await expect(page.locator('#audit tr')).toHaveCount(1);
  expect(state.requests.at(-1).body.after_id).toBe(25);
});

test('malformed server data and raw errors never render privileged or unsafe markup', async ({ page }) => {
  await fixture(page, { malformed: [user('<img src=x onerror=alert(1)>')] });
  await login(page);
  await expect(page.getByText('Sign in failed or administrator access is unavailable.')).toBeVisible();
  await expect(page.locator('#users img')).toHaveCount(0);
  await expect(page.locator('#workspace')).not.toBeVisible();
});

test('incorrect password is generic, mobile page has no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, { badPassword: true });
  await login(page);
  await expect(page.getByText('Sign in failed or administrator access is unavailable.')).toBeVisible();
  await expect(page.getByText('private server error')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('mobile administration remains within the viewport with keyboard-accessible actions', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await fixture(page);
  await ready(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await targetRow(page).getByRole('button', { name: 'Grant Cloud backup', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await mkdir('output/playwright', { recursive: true });
  await page.screenshot({ path: 'output/playwright/customer-admin-mobile.png', animations: 'disabled' });
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  expect(errors).toEqual([]);
});

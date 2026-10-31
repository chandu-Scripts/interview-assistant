import {
  CAPABILITIES, PAGE_SIZE, REASONS, UUID, createOwner, current, disposeOwner,
  isVersion, predecessor, validateAudit, validateConfig, validateUsers,
} from './session.js';

const $ = id => document.getElementById(id);
let config = null;
let owner = null;
let authorized = false;
let busy = false;
let users = [];
let audit = [];
let userCursors = [null];
let auditCursors = [null];
let search = '';
let selection = null;

function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
}

function clearWorkspace() {
  authorized = false;
  busy = false;
  selection = null;
  users = [];
  audit = [];
  userCursors = [null];
  auditCursors = [null];
  search = '';
  $('users').replaceChildren();
  $('audit').replaceChildren();
  $('search').value = '';
  $('password').value = '';
  $('email').value = '';
  $('action-form').reset();
  $('action-target').textContent = '';
  $('action-summary').textContent = '';
  $('action-error').textContent = '';
  $('cancel-action').disabled = false;
  $('confirm-action').disabled = false;
  $('action-dialog').close();
  $('workspace').hidden = true;
  $('login-panel').hidden = false;
  $('login-fields').disabled = !config;
  $('logout').hidden = true;
}

function endSession(message = 'Signed out. This tab has been cleared.') {
  const previous = owner;
  owner = null;
  clearWorkspace();
  disposeOwner(previous);
  notice(message);
}

function alive(expected = owner) {
  if (expected !== owner || !current(expected)) {
    if (expected === owner && owner) endSession('Your session has expired. Sign in again.');
    return false;
  }
  return true;
}

function controls() {
  for (const control of $('workspace').querySelectorAll('button, input')) control.disabled = busy;
  $('previous-users').disabled = busy || userCursors.length === 1 || !!search;
  $('next-users').disabled = busy || users.length < PAGE_SIZE || !!search;
  $('previous-audit').disabled = busy || auditCursors.length === 1;
  $('next-audit').disabled = busy || audit.length < PAGE_SIZE;
  $('users-page').textContent = search ? 'Exact user ID result' : `Page ${userCursors.length}`;
  $('audit-page').textContent = `Page ${auditCursors.length}`;
  // The backend also rejects self-block; keep the accidental path unavailable here.
  for (const button of document.querySelectorAll('[data-self-block]')) button.disabled = true;
}

async function rpc(name, args, expected) {
  if (!alive(expected)) throw new Error('Session ended');
  const result = await expected.client.rpc(name, args).abortSignal(expected.controller.signal);
  if (!alive(expected)) throw new Error('Session ended');
  if (result.error) {
    if (result.status === 401 || result.status === 403 || result.error.code === '42501') {
      endSession('Administrator access is unavailable. Sign in again.');
    }
    throw result.error;
  }
  return result.data;
}

function cell(row, text, className = '') {
  const element = document.createElement('td');
  element.textContent = text;
  element.className = className;
  row.append(element);
  return element;
}

function line(parent, text, className = 'small') {
  const element = document.createElement('span');
  element.textContent = text;
  element.className = className;
  parent.append(element);
}

function date(value) {
  return value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : 'Not recorded';
}

const capabilityName = value => value === 'desktop_access' ? 'Desktop' : 'Cloud backup';

function renderUsers() {
  $('users').replaceChildren();
  for (const user of users) {
    const row = document.createElement('tr');
    const identity = cell(row, user.user_id, 'uuid');
    line(identity, user.is_admin ? 'Administrator' : 'Customer');
    line(identity, `Created ${date(user.created_at)}`);
    cell(row, user.blocked ? 'Blocked' : 'Unblocked', user.blocked ? 'blocked' : 'tag');
    const grants = cell(row, '');
    for (const capability of CAPABILITIES) {
      const grant = user.grants.find(item => item.capability === capability);
      const active = grant?.enabled && Date.parse(grant.expires_at) > Date.now();
      line(grants, `${capabilityName(capability)}: ${active ? 'Granted' : 'No active grant'}`, active ? 'tag' : 'small');
      if (grant) line(grants, `Until ${date(grant.expires_at)}`);
    }
    cell(row, date(user.last_seen_at));
    const actions = cell(row, '', 'actions');
    const actionButton = (text, action, capability = null) => {
      const button = document.createElement('button');
      button.textContent = text;
      if (action === 'block' && user.user_id === owner?.userId) button.dataset.selfBlock = 'true';
      button.addEventListener('click', () => openAction(user, action, capability));
      actions.append(button);
    };
    actionButton(user.blocked ? 'Unblock' : 'Block', 'block');
    for (const capability of CAPABILITIES) {
      actionButton(`Grant ${capabilityName(capability)}`, 'grant', capability);
      if (user.grants.some(grant => grant.capability === capability && grant.enabled)) {
        actionButton(`Revoke ${capabilityName(capability)}`, 'revoke', capability);
      }
    }
    $('users').append(row);
  }
  if (!users.length) { const row = document.createElement('tr'); cell(row, 'No customers found.').colSpan = 5; $('users').append(row); }
}

function renderAudit() {
  $('audit').replaceChildren();
  for (const event of audit) {
    const row = document.createElement('tr');
    const stamp = cell(row, date(event.created_at));
    line(stamp, `Event ${event.id} / version ${event.version}`);
    cell(row, event.actor_id, 'uuid');
    cell(row, event.target_user_id, 'uuid');
    const action = cell(row, `${event.action}${event.capability ? `: ${capabilityName(event.capability)}` : ''}`);
    if (event.expires_at) line(action, `Until ${date(event.expires_at)}`);
    cell(row, event.reason_code.replaceAll('_', ' '));
    $('audit').append(row);
  }
  if (!audit.length) { const row = document.createElement('tr'); cell(row, 'No activity found.').colSpan = 5; $('audit').append(row); }
}

async function loadUsers(expected = owner) {
  users = [];
  $('users').replaceChildren();
  const cursor = search ? predecessor(search) : userCursors.at(-1);
  const data = validateUsers(await rpc('customer_admin_list', { after_user_id: cursor, page_size: PAGE_SIZE }, expected));
  if (cursor && data.some(user => user.user_id <= cursor)) throw new Error('Invalid cursor');
  users = search ? data.filter(user => user.user_id === search) : data;
  renderUsers();
}

async function loadAudit(expected = owner) {
  audit = [];
  $('audit').replaceChildren();
  const cursor = auditCursors.at(-1);
  const data = validateAudit(await rpc('customer_admin_audit', { after_id: cursor, page_size: PAGE_SIZE }, expected));
  if (cursor && data.some(event => event.id <= cursor)) throw new Error('Invalid cursor');
  audit = data;
  renderAudit();
}

async function operation(action) {
  if (busy || !authorized || !alive()) return;
  const expected = owner;
  busy = true;
  controls();
  try {
    await action(expected);
  } catch {
    if (alive(expected)) notice('Unable to load this view. Check your connection and refresh.', true);
  } finally {
    if (expected === owner) { busy = false; controls(); }
  }
}

function showView(name) {
  $('users-panel').hidden = name !== 'users';
  $('audit-panel').hidden = name !== 'audit';
  $('users-tab').setAttribute('aria-pressed', String(name === 'users'));
  $('audit-tab').setAttribute('aria-pressed', String(name === 'audit'));
}

function openAction(user, action, capability) {
  if (busy || !authorized || !alive()) return;
  const grant = user.grants.find(item => item.capability === capability);
  selection = { userId: user.user_id, version: user.version, action, capability, blocked: !user.blocked, expiry: grant?.expires_at };
  $('action-form').reset();
  $('action-error').textContent = '';
  $('action-target').textContent = user.user_id;
  const title = action === 'block' ? (user.blocked ? 'Unblock account' : 'Block account')
    : `${action === 'grant' ? 'Grant' : 'Revoke'} ${capabilityName(capability)} access`;
  $('action-title').textContent = title;
  $('action-summary').textContent = action === 'block'
    ? (user.blocked ? 'Existing grants still control access after unblocking.' : 'New protected operations will be denied. A running desktop locks when its short access lease expires.')
    : (action === 'grant' ? 'Access is granted until the expiry you choose. Blocking still takes priority.' : `Remove this grant, currently ending ${date(grant?.expires_at)}.`);
  $('expiry-field').hidden = action !== 'grant';
  $('expiry').disabled = action !== 'grant';
  $('confirm-action').disabled = false;
  $('cancel-action').disabled = false;
  $('action-dialog').showModal();
}

$('login-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!config || owner) return;
  const email = $('email').value.trim();
  const password = $('password').value;
  $('password').value = '';
  $('login-fields').disabled = true;
  $('logout').hidden = false;
  const expected = createOwner(config, expired => {
    if (expired === owner) endSession('Your session has expired. Sign in again.');
  });
  owner = expected;
  notice('Checking administrator access...');
  try {
    const { error } = await expected.client.auth.signInWithPassword({ email, password });
    if (error || !alive(expected)) throw new Error('Sign in failed');
    await loadUsers(expected);
    if (!alive(expected)) return;
    authorized = true;
    $('login-panel').hidden = true;
    $('workspace').hidden = false;
    $('email').value = '';
    showView('users');
    controls();
    notice('Administrator access verified. Changes require confirmation.');
  } catch {
    if (expected === owner) endSession('Sign in failed or administrator access is unavailable.');
  }
});

$('logout').addEventListener('click', () => endSession());
$('users-tab').addEventListener('click', () => { if (!busy && alive()) showView('users'); });
$('audit-tab').addEventListener('click', () => operation(async expected => { await loadAudit(expected); showView('audit'); }));
$('refresh-users').addEventListener('click', () => operation(loadUsers));
$('refresh-audit').addEventListener('click', () => operation(loadAudit));
$('search-form').addEventListener('submit', event => {
  event.preventDefault();
  const query = $('search').value.trim().toLowerCase();
  if (query && !UUID.test(query)) { notice('Enter a complete user ID in UUID format.', true); return; }
  void operation(async expected => { search = query; userCursors = [null]; await loadUsers(expected); });
});
$('clear-search').addEventListener('click', () => operation(async expected => { search = ''; $('search').value = ''; userCursors = [null]; await loadUsers(expected); }));
for (const kind of ['users', 'audit']) {
  for (const direction of ['previous', 'next']) {
    $(`${direction}-${kind}`).addEventListener('click', () => operation(async expected => {
      const cursors = kind === 'users' ? userCursors : auditCursors;
      const old = [...cursors];
      if (direction === 'previous') cursors.pop();
      else cursors.push(kind === 'users' ? users.at(-1).user_id : audit.at(-1).id);
      try { await (kind === 'users' ? loadUsers(expected) : loadAudit(expected)); }
      catch (error) { if (kind === 'users') userCursors = old; else auditCursors = old; throw error; }
    }));
  }
}
$('cancel-action').addEventListener('click', () => { if (!busy) { selection = null; $('action-dialog').close(); } });
$('action-dialog').addEventListener('cancel', event => { if (busy) event.preventDefault(); else selection = null; });
$('action-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!selection || busy || !authorized || !alive()) return;
  if ($('confirm-target').value !== selection.userId || !REASONS.includes($('reason').value)) {
    $('action-error').textContent = 'Choose a reason and enter the exact customer user ID.';
    return;
  }
  let expiry = selection.expiry;
  if (selection.action === 'grant') {
    const parsed = Date.parse($('expiry').value);
    if (!Number.isFinite(parsed) || parsed <= Date.now()) { $('action-error').textContent = 'Choose a future expiry.'; return; }
    expiry = new Date(parsed).toISOString();
  }
  const expected = owner;
  const target = selection;
  const params = { target_user_id: target.userId, expected_version: target.version, reason_code: $('reason').value };
  const name = target.action === 'block' ? 'customer_admin_set_block' : 'customer_admin_set_grant';
  if (target.action === 'block') params.blocked = target.blocked;
  else Object.assign(params, { capability: target.capability, expires_at: expiry, enabled: target.action === 'grant' });
  busy = true;
  $('confirm-action').disabled = true;
  $('cancel-action').disabled = true;
  let message = 'Change saved.';
  let failed = false;
  try {
    const result = await rpc(name, params, expected);
    if (result?.user_id !== target.userId || !isVersion(result.version) || result.version !== target.version + 1) throw new Error('Invalid response');
  } catch (error) {
    failed = true;
    message = error.code === '40001'
      ? 'This account changed. Review the refreshed details and confirm again.'
      : 'The change could not be confirmed. Refresh and check the account before trying again.';
  } finally {
    if (expected === owner) {
      selection = null;
      $('action-dialog').close();
      $('cancel-action').disabled = false;
      users = [];
      audit = [];
      $('users').replaceChildren();
      $('audit').replaceChildren();
      try { await loadUsers(expected); }
      catch { message += ' Refresh is currently unavailable.'; failed = true; }
      if (expected === owner) { busy = false; controls(); notice(message, failed); }
    }
  }
});

document.addEventListener('visibilitychange', () => { if (!document.hidden && owner?.userId) alive(); });
window.addEventListener('pagehide', () => endSession());
window.addEventListener('pageshow', event => { if (event.persisted) endSession('Sign in again to continue.'); });

try {
  const response = await fetch('/admin/config.json', { cache: 'no-store', signal: AbortSignal.timeout(5000) });
  if (response.ok) config = validateConfig(await response.json());
} catch { /* Unconfigured and unavailable deployments stay closed. */ }
clearWorkspace();
notice(config ? 'Sign in with an approved administrator account.' : 'Admin setup is not configured for this website.');

-- Local preparation only. No grants are seeded and no existing Auth data is changed.
create schema if not exists customer_private;
revoke all on schema customer_private from public, anon, authenticated;
grant usage on schema customer_private to authenticated;

create table customer_private.accounts (
    user_id uuid primary key references auth.users(id) on delete cascade,
    blocked boolean not null default false,
    version bigint not null default 0 check (version >= 0),
    last_seen_at timestamptz,
    created_at timestamptz not null default now()
);
create table customer_private.admins (
    user_id uuid primary key references auth.users(id) on delete cascade,
    created_at timestamptz not null default now()
);
create table customer_private.grants (
    user_id uuid not null references auth.users(id) on delete cascade,
    capability text not null check (capability in ('desktop_access', 'cloud_backup')),
    expires_at timestamptz not null check (isfinite(expires_at)),
    enabled boolean not null default false,
    primary key (user_id, capability)
);
create table customer_private.audit (
    id bigint generated always as identity primary key,
    actor_id uuid not null,
    target_user_id uuid not null,
    action text not null check (action in ('block', 'unblock', 'grant', 'revoke')),
    capability text check (capability in ('desktop_access', 'cloud_backup')),
    expires_at timestamptz,
    reason_code text not null check (reason_code in ('support', 'policy', 'owner_request', 'trial', 'manual', 'correction')),
    version bigint not null,
    created_at timestamptz not null default now()
);
alter table customer_private.accounts enable row level security;
alter table customer_private.admins enable row level security;
alter table customer_private.grants enable row level security;
alter table customer_private.audit enable row level security;
revoke all on all tables in schema customer_private from public, anon, authenticated;
revoke all on all sequences in schema customer_private from public, anon, authenticated;

-- Private helpers are not exposed by PostgREST. Every definer pins search_path and
-- checks the current caller. Public invoker wrappers are the only HTTP API surface.
create function customer_private.require_identity() returns uuid
language plpgsql security definer set search_path = '' as $$
declare
    caller uuid := auth.uid();
    claims jsonb := auth.jwt();
begin
    if caller is null or claims->>'role' is distinct from 'authenticated'
        or coalesce((claims->>'is_anonymous')::boolean, false)
        or coalesce((claims->>'exp')::numeric, 0) <= extract(epoch from clock_timestamp())
        or not exists (
            select 1 from auth.sessions s join auth.users u on u.id = s.user_id
            where s.id = (claims->>'session_id')::uuid and s.user_id = caller
                and (s.not_after is null or s.not_after > clock_timestamp())
                and (u.banned_until is null or u.banned_until <= clock_timestamp())
        ) then
        raise exception 'A current sign-in is required.' using errcode = '42501';
    end if;
    return caller;
end $$;

create function customer_private.require_admin() returns uuid
language plpgsql security definer set search_path = '' as $$
declare caller uuid := customer_private.require_identity();
begin
    if not exists (select 1 from customer_private.admins a where a.user_id = caller)
        or exists (select 1 from customer_private.accounts a where a.user_id = caller and a.blocked)
    then
        raise exception 'Administrator access required.' using errcode = '42501';
    end if;
    return caller;
end $$;

create function customer_private.access_snapshot() returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    caller uuid := customer_private.require_identity();
    account_row customer_private.accounts%rowtype;
    grant_row customer_private.grants%rowtype;
    stamp timestamptz := clock_timestamp();
    session_expiry timestamptz;
    reason text;
    ttl numeric := 0;
begin
    insert into customer_private.accounts(user_id, last_seen_at) values (caller, stamp)
        on conflict (user_id) do update set last_seen_at = excluded.last_seen_at;
    select * into account_row from customer_private.accounts where user_id = caller;
    select * into grant_row from customer_private.grants
        where user_id = caller and capability = 'desktop_access';
    reason := case when account_row.blocked then 'blocked'
        when grant_row.user_id is null or not grant_row.enabled then 'no_grant'
        when grant_row.expires_at <= stamp then 'expired' else 'allowed' end;
    if reason = 'allowed' then
        select s.not_after into session_expiry from auth.sessions s
            where s.id = (auth.jwt()->>'session_id')::uuid and s.user_id = caller;
        ttl := least(60, extract(epoch from (grant_row.expires_at - stamp)),
            (auth.jwt()->>'exp')::numeric - extract(epoch from stamp),
            coalesce(extract(epoch from (session_expiry - stamp)), 60));
        if ttl <= 0 then
            reason := 'expired';
        end if;
    end if;
    return jsonb_build_object('user_id', caller, 'allowed', reason = 'allowed',
        'reason', reason, 'lease_seconds', greatest(0, ttl), 'version', account_row.version,
        'server_time', stamp, 'grant_expires_at', grant_row.expires_at,
        'is_admin', exists(select 1 from customer_private.admins where user_id = caller));
end $$;

-- Future protected operations must call this inside their own transaction. A
-- client-side precheck does not authorize a later independent operation.
create function customer_private.require_access(capability text) returns boolean
language plpgsql security definer set search_path = '' as $$
declare caller uuid := customer_private.require_identity();
begin
    if capability not in ('desktop_access', 'cloud_backup') or capability is null
        or exists(select 1 from customer_private.accounts a where a.user_id = caller and a.blocked)
        or not exists(select 1 from customer_private.grants g where g.user_id = caller
            and g.capability = require_access.capability and g.enabled and g.expires_at > clock_timestamp())
    then
        raise exception 'Active access required.' using errcode = '42501';
    end if;
    return true;
end $$;

create function customer_private.admin_list(after_user_id uuid, page_size integer) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
    perform customer_private.require_admin();
    if page_size is null or page_size not between 1 and 100 then
        raise exception 'Page size must be 1 to 100.' using errcode = '22023';
    end if;
    return coalesce((select jsonb_agg(row_data order by id) from (
        select u.id, jsonb_build_object('user_id', u.id, 'created_at', u.created_at,
            'blocked', coalesce(a.blocked, false), 'version', coalesce(a.version, 0),
            'last_seen_at', a.last_seen_at,
            'is_admin', exists(select 1 from customer_private.admins m where m.user_id = u.id),
            'grants', coalesce((select jsonb_agg(jsonb_build_object('capability', g.capability,
                'expires_at', g.expires_at, 'enabled', g.enabled) order by g.capability)
                from customer_private.grants g where g.user_id = u.id), '[]'::jsonb)) as row_data
        from auth.users u left join customer_private.accounts a on a.user_id = u.id
        where after_user_id is null or u.id > after_user_id
        order by u.id limit page_size
    ) rows), '[]'::jsonb);
end $$;

create function customer_private.admin_set_block(
    target_user_id uuid, blocked boolean, reason_code text, expected_version bigint
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    actor uuid := customer_private.require_admin();
    previous_version bigint;
    next_version bigint;
begin
    if target_user_id = actor or blocked is null or expected_version is null
        or reason_code is null or reason_code not in ('support', 'policy', 'owner_request', 'trial', 'manual', 'correction')
    then raise exception 'Invalid account action.' using errcode = '22023'; end if;
    insert into customer_private.accounts(user_id) values (target_user_id) on conflict do nothing;
    select a.version into previous_version from customer_private.accounts a
        where a.user_id = target_user_id for update;
    if previous_version is distinct from expected_version then
        raise exception 'Account changed. Refresh before retrying.' using errcode = '40001';
    end if;
    update customer_private.accounts a set blocked = admin_set_block.blocked, version = a.version + 1
        where a.user_id = target_user_id returning a.version into next_version;
    insert into customer_private.audit(actor_id, target_user_id, action, reason_code, version)
        values (actor, target_user_id, case when blocked then 'block' else 'unblock' end, reason_code, next_version);
    return jsonb_build_object('user_id', target_user_id, 'blocked', blocked, 'version', next_version);
end $$;

create function customer_private.admin_set_grant(
    target_user_id uuid, capability text, expires_at timestamptz, enabled boolean,
    reason_code text, expected_version bigint
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    actor uuid := customer_private.require_admin();
    previous_version bigint;
    next_version bigint;
begin
    if capability is null or capability not in ('desktop_access', 'cloud_backup')
        or expires_at is null or not isfinite(expires_at)
        or (enabled and expires_at <= clock_timestamp()) or enabled is null
        or expected_version is null or reason_code is null
        or reason_code not in ('support', 'policy', 'owner_request', 'trial', 'manual', 'correction')
    then raise exception 'Invalid access grant.' using errcode = '22023'; end if;
    insert into customer_private.accounts(user_id) values (target_user_id) on conflict do nothing;
    select a.version into previous_version from customer_private.accounts a
        where a.user_id = target_user_id for update;
    if previous_version is distinct from expected_version then
        raise exception 'Account changed. Refresh before retrying.' using errcode = '40001';
    end if;
    insert into customer_private.grants(user_id, capability, expires_at, enabled)
        values (target_user_id, capability, expires_at, enabled)
        on conflict on constraint grants_pkey do update
        set expires_at = excluded.expires_at, enabled = excluded.enabled;
    update customer_private.accounts a set version = a.version + 1
        where a.user_id = target_user_id returning a.version into next_version;
    insert into customer_private.audit(actor_id, target_user_id, action, capability, expires_at, reason_code, version)
        values (actor, target_user_id, case when enabled then 'grant' else 'revoke' end,
            capability, expires_at, reason_code, next_version);
    return jsonb_build_object('user_id', target_user_id, 'capability', capability,
        'expires_at', expires_at, 'enabled', enabled, 'version', next_version);
end $$;

create function customer_private.admin_audit(after_id bigint, page_size integer) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
    perform customer_private.require_admin();
    if page_size is null or page_size not between 1 and 100 or after_id < 0 then
        raise exception 'Invalid audit page.' using errcode = '22023';
    end if;
    return coalesce((select jsonb_agg(to_jsonb(rows) order by id) from (
        select * from customer_private.audit a where after_id is null or a.id > after_id
        order by a.id limit page_size
    ) rows), '[]'::jsonb);
end $$;

create function public.customer_access_snapshot() returns jsonb
language sql security invoker set search_path = '' as $$ select customer_private.access_snapshot() $$;
create function public.customer_require_access(capability text) returns boolean
language sql security invoker set search_path = '' as $$ select customer_private.require_access(capability) $$;
create function public.customer_admin_list(after_user_id uuid default null, page_size integer default 50) returns jsonb
language sql security invoker set search_path = '' as $$ select customer_private.admin_list(after_user_id, page_size) $$;
create function public.customer_admin_set_block(target_user_id uuid, blocked boolean, reason_code text, expected_version bigint) returns jsonb
language sql security invoker set search_path = '' as $$ select customer_private.admin_set_block(target_user_id, blocked, reason_code, expected_version) $$;
create function public.customer_admin_set_grant(target_user_id uuid, capability text, expires_at timestamptz, enabled boolean, reason_code text, expected_version bigint) returns jsonb
language sql security invoker set search_path = '' as $$ select customer_private.admin_set_grant(target_user_id, capability, expires_at, enabled, reason_code, expected_version) $$;
create function public.customer_admin_audit(after_id bigint default null, page_size integer default 50) returns jsonb
language sql security invoker set search_path = '' as $$ select customer_private.admin_audit(after_id, page_size) $$;

revoke all on all functions in schema customer_private from public, anon, authenticated;
grant execute on function customer_private.access_snapshot(), customer_private.require_access(text),
    customer_private.admin_list(uuid, integer), customer_private.admin_set_block(uuid, boolean, text, bigint),
    customer_private.admin_set_grant(uuid, text, timestamptz, boolean, text, bigint),
    customer_private.admin_audit(bigint, integer) to authenticated;
revoke all on function public.customer_access_snapshot(), public.customer_require_access(text),
    public.customer_admin_list(uuid, integer), public.customer_admin_set_block(uuid, boolean, text, bigint),
    public.customer_admin_set_grant(uuid, text, timestamptz, boolean, text, bigint),
    public.customer_admin_audit(bigint, integer) from public, anon, authenticated;
grant execute on function public.customer_access_snapshot(), public.customer_require_access(text),
    public.customer_admin_list(uuid, integer), public.customer_admin_set_block(uuid, boolean, text, bigint),
    public.customer_admin_set_grant(uuid, text, timestamptz, boolean, text, bigint),
    public.customer_admin_audit(bigint, integer) to authenticated;

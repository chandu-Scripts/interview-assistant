-- Read-only prerequisite check for the customer access migration.
-- Run only against chandoo-assistant-dev (lctuyznspcufmzzxbjzv).
-- This reads schema/role metadata, never Auth user/session records.
select
    current_database() as database_name,
    current_user as applying_role,
    to_regnamespace('customer_private') is null as customer_schema_absent,
    not exists (
        select 1
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname = any(array[
              'customer_access_snapshot',
              'customer_require_access',
              'customer_admin_list',
              'customer_admin_set_block',
              'customer_admin_set_grant',
              'customer_admin_audit'
          ])
    ) as public_wrapper_names_available,
    to_regclass('supabase_migrations.schema_migrations') is not null
        as migration_history_exists,
    exists (select 1 from pg_roles where rolname = 'anon') as anon_role_exists,
    exists (select 1 from pg_roles where rolname = 'authenticated')
        as authenticated_role_exists,
    to_regprocedure('auth.uid()') is not null as auth_uid_exists,
    to_regprocedure('auth.jwt()') is not null as auth_jwt_exists,
    not exists (
        select 1
        from (values
            ('users', 'id'),
            ('users', 'created_at'),
            ('users', 'banned_until'),
            ('sessions', 'id'),
            ('sessions', 'user_id'),
            ('sessions', 'not_after')
        ) required(table_name, column_name)
        where not exists (
            select 1 from information_schema.columns c
            where c.table_schema = 'auth'
              and c.table_name = required.table_name
              and c.column_name = required.column_name
        )
    ) as required_auth_columns_exist,
    case when to_regnamespace('auth') is not null
        then has_schema_privilege(current_user, 'auth', 'USAGE')
        else false end as applying_role_has_auth_usage,
    case when to_regclass('auth.users') is not null
        then has_table_privilege(current_user, 'auth.users', 'SELECT')
        else false end as applying_role_can_read_users,
    case when to_regclass('auth.sessions') is not null
        then has_table_privilege(current_user, 'auth.sessions', 'SELECT')
        else false end as applying_role_can_read_sessions;

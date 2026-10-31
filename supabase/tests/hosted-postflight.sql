-- Read-only catalog inspection. Does not execute customer RPCs or read user rows.
-- Compare functions[].body_md5 with the locally generated reviewed-migration
-- baseline; catalog_checks_pass alone does not establish matching function bodies.
with
expected_tables(name) as (
    values ('accounts'), ('admins'), ('grants'), ('audit')
),
expected_functions(schema_name, name, argument_types, return_type, authenticated_execute, defaults) as (
    values
        ('customer_private', 'require_identity', '', 'uuid', false, 0),
        ('customer_private', 'require_admin', '', 'uuid', false, 0),
        ('customer_private', 'access_snapshot', '', 'jsonb', true, 0),
        ('customer_private', 'require_access', 'text', 'boolean', true, 0),
        ('customer_private', 'admin_list', 'uuid, integer', 'jsonb', true, 0),
        ('customer_private', 'admin_set_block', 'uuid, boolean, text, bigint', 'jsonb', true, 0),
        ('customer_private', 'admin_set_grant', 'uuid, text, timestamp with time zone, boolean, text, bigint', 'jsonb', true, 0),
        ('customer_private', 'admin_audit', 'bigint, integer', 'jsonb', true, 0),
        ('public', 'customer_access_snapshot', '', 'jsonb', true, 0),
        ('public', 'customer_require_access', 'text', 'boolean', true, 0),
        ('public', 'customer_admin_list', 'uuid, integer', 'jsonb', true, 2),
        ('public', 'customer_admin_set_block', 'uuid, boolean, text, bigint', 'jsonb', true, 0),
        ('public', 'customer_admin_set_grant', 'uuid, text, timestamp with time zone, boolean, text, bigint', 'jsonb', true, 0),
        ('public', 'customer_admin_audit', 'bigint, integer', 'jsonb', true, 2)
),
namespace as (
    select n.oid, pg_get_userbyid(n.nspowner) as owner,
        has_schema_privilege('anon', n.oid, 'USAGE') as anon_usage,
        has_schema_privilege('anon', n.oid, 'CREATE') as anon_create,
        has_schema_privilege('authenticated', n.oid, 'USAGE') as authenticated_usage,
        has_schema_privilege('authenticated', n.oid, 'CREATE') as authenticated_create,
        exists(select 1 from aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a
            where a.grantee = 0) as public_privileges
    from pg_namespace n where n.nspname = 'customer_private'
),
tables as (
    select e.name, c.oid is not null as present, c.relkind::text as kind,
        pg_get_userbyid(c.relowner) as owner,
        c.relrowsecurity as rls, c.relforcerowsecurity as force_rls,
        (select count(*) from pg_policy p where p.polrelid = c.oid) as policy_count,
        has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            or has_any_column_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,REFERENCES') as anon_privileges,
        has_table_privilege('authenticated', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            or has_any_column_privilege('authenticated', c.oid, 'SELECT,INSERT,UPDATE,REFERENCES') as authenticated_privileges,
        exists(select 1 from aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
            where a.grantee = 0) as public_privileges
    from expected_tables e
    left join pg_class c on c.relnamespace = (select oid from namespace) and c.relname = e.name
),
functions as (
    select e.schema_name || '.' || e.name || '(' || e.argument_types || ')' as signature,
        p.oid is not null as present, pg_get_userbyid(p.proowner) as owner,
        p.prokind::text as kind, l.lanname as language, p.prosecdef as security_definer,
        p.proconfig as configuration, format_type(p.prorettype, null) as return_type,
        p.pronargdefaults as defaults,
        has_function_privilege('anon', p.oid, 'EXECUTE') as anon_execute,
        has_function_privilege('authenticated', p.oid, 'EXECUTE') as authenticated_execute,
        has_function_privilege('authenticated', p.oid, 'EXECUTE WITH GRANT OPTION') as authenticated_grant_option,
        exists(select 1 from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
            where a.grantee = 0 and a.privilege_type = 'EXECUTE') as public_execute,
        md5(replace(p.prosrc, chr(13), '')) as body_md5,
        e.schema_name = 'customer_private' as expected_definer,
        case when e.schema_name = 'customer_private' then 'plpgsql' else 'sql' end as expected_language,
        e.return_type as expected_return, e.authenticated_execute as expected_authenticated,
        e.defaults as expected_defaults
    from expected_functions e
    left join pg_namespace n on n.nspname = e.schema_name
    left join pg_proc p on p.pronamespace = n.oid and p.proname = e.name
        and oidvectortypes(p.proargtypes) = e.argument_types
    left join pg_language l on l.oid = p.prolang
),
sequences as (
    select c.relname as name, pg_get_userbyid(c.relowner) as owner,
        has_sequence_privilege('anon', c.oid, 'USAGE,SELECT,UPDATE') as anon_privileges,
        has_sequence_privilege('authenticated', c.oid, 'USAGE,SELECT,UPDATE') as authenticated_privileges,
        exists(select 1 from aclexplode(coalesce(c.relacl, acldefault('s', c.relowner))) a
            where a.grantee = 0) as public_privileges
    from pg_class c where c.relnamespace = (select oid from namespace) and c.relkind = 'S'
),
inventory as (
    select
        (select count(*) from pg_class c where c.relnamespace = (select oid from namespace)
            and c.relkind in ('r', 'p')) as private_table_count,
        (select count(*) from pg_proc p where p.pronamespace = (select oid from namespace)) as private_function_count,
        (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and left(p.proname, 9) = 'customer_') as public_function_count,
        (select count(*) from sequences) as private_sequence_count
),
checks as (
    select
        coalesce((select owner = 'postgres' and not anon_usage and not anon_create
            and authenticated_usage and not authenticated_create and not public_privileges
            from namespace), false) as schema_permissions_match,
        coalesce((select bool_and(present and kind = 'r' and owner = 'postgres'
            and rls and not force_rls and policy_count = 0
            and not anon_privileges and not authenticated_privileges and not public_privileges)
            from tables), false) as tables_match,
        coalesce((select bool_and(coalesce(present and kind = 'f' and owner = 'postgres'
            and language = expected_language and security_definer = expected_definer
            and configuration = array['search_path=""']::text[]
            and return_type = expected_return and defaults = expected_defaults
            and not anon_execute and not public_execute and not authenticated_grant_option
            and authenticated_execute = expected_authenticated, false)) from functions), false) as functions_match,
        coalesce((select bool_and(name = 'audit_id_seq' and owner = 'postgres'
            and not anon_privileges and not authenticated_privileges and not public_privileges)
            from sequences), false) as sequences_match,
        private_table_count = 4 and private_function_count = 8
            and public_function_count = 6 and private_sequence_count = 1 as inventory_matches
    from inventory
)
select jsonb_build_object(
    'catalog_checks_pass', schema_permissions_match and tables_match and functions_match
        and sequences_match and inventory_matches,
    'checks', to_jsonb(checks),
    'inventory', (select to_jsonb(i) from inventory i),
    'schema', (select to_jsonb(n) - 'oid' from namespace n),
    'tables', (select jsonb_agg(to_jsonb(t) order by name) from tables t),
    'functions', (select jsonb_agg(to_jsonb(f) - array['expected_definer', 'expected_language',
        'expected_return', 'expected_authenticated', 'expected_defaults'] order by signature) from functions f),
    'sequences', (select jsonb_agg(to_jsonb(s) order by name) from sequences s)
) as postflight
from checks;

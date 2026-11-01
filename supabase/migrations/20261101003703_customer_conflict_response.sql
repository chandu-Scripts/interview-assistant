-- An optimistic-version conflict is permanent for this request, not a transient
-- serialization failure. PostgREST must return HTTP 409 instead of retrying it.
-- Keep the applied foundation migration immutable. This single statement either
-- changes both exact function bodies or rolls back without changing either one.
do $migration$
declare
    signature text;
    target_oid oid;
    before_attributes jsonb;
    after_attributes jsonb;
    previous_source text;
    actual_source text;
    definition text;
    old_marker constant text := 'using errcode = ''40001''';
    new_marker constant text := 'using errcode = ''PT409''';
begin
    foreach signature in array array[
        'customer_private.admin_set_block(uuid,boolean,text,bigint)',
        'customer_private.admin_set_grant(uuid,text,timestamp with time zone,boolean,text,bigint)'
    ] loop
        target_oid := pg_catalog.to_regprocedure(signature);
        if target_oid is null then
            raise exception 'Customer mutation function is missing.';
        end if;
        select pg_catalog.to_jsonb(p) - 'prosrc', p.prosrc,
            pg_catalog.pg_get_functiondef(p.oid)
        into strict before_attributes, previous_source, definition
        from pg_catalog.pg_proc p where p.oid = target_oid;

        if (pg_catalog.length(previous_source)
                - pg_catalog.length(pg_catalog.replace(previous_source, old_marker, '')))
                / pg_catalog.length(old_marker) <> 1 then
            raise exception 'Customer mutation conflict marker differs from the reviewed source.';
        end if;
        execute pg_catalog.replace(definition, old_marker, new_marker);

        select pg_catalog.to_jsonb(p) - 'prosrc', p.prosrc
        into strict after_attributes, actual_source
        from pg_catalog.pg_proc p where p.oid = target_oid;
        if after_attributes is distinct from before_attributes
            or actual_source is distinct from pg_catalog.replace(previous_source, old_marker, new_marker)
        then
            raise exception 'Customer mutation metadata or source changed unexpectedly.';
        end if;
    end loop;
end
$migration$;

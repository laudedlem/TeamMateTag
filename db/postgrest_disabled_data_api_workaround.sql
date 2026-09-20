-- Supabase keeps PostgREST running when the Data API is disabled and otherwise
-- probes a non-existent placeholder schema, creating repeated harmless errors.
-- Keep an empty schema as PostgREST's only schema while this direct-Postgres
-- application intentionally has the Data API disabled.
CREATE SCHEMA IF NOT EXISTS pgrst_no_exposed_schemas;
ALTER ROLE authenticator SET pgrst.db_schemas = 'pgrst_no_exposed_schemas';
NOTIFY pgrst, 'reload config';

#!/usr/bin/env bash
set -Eeuo pipefail

app_user="${POSTGRES_APP_USER:-oncall_app}"
app_password="${POSTGRES_APP_PASSWORD:-oncall_app_dev_password}"

psql \
  --set=ON_ERROR_STOP=1 \
  --set=app_user="$app_user" \
  --set=app_password="$app_password" \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" <<'SQL'
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
    :'app_user',
    :'app_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = :'app_user'
)
\gexec

SELECT format(
    'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
    :'app_user',
    :'app_password'
)
\gexec

SELECT format(
    'ALTER DATABASE %I OWNER TO %I',
    current_database(),
    :'app_user'
)
\gexec

SELECT format('ALTER SCHEMA public OWNER TO %I', :'app_user')
\gexec

SELECT format(
    'ALTER TABLE %I.%I OWNER TO %I',
    schemaname,
    tablename,
    :'app_user'
)
FROM pg_tables
WHERE schemaname = 'public'
\gexec

SELECT format(
    'ALTER SEQUENCE %I.%I OWNER TO %I',
    sequence_schema,
    sequence_name,
    :'app_user'
)
FROM information_schema.sequences
WHERE sequence_schema = 'public'
\gexec
SQL

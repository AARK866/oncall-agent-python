# Authentication, RBAC, and tenant isolation

This service acts as an OAuth2/OIDC resource server. An identity provider issues
the access token; the OnCall Agent validates the token and never handles a user
password.

## Production configuration

Use asymmetric OIDC validation in production:

```dotenv
AUTH_MODE=jwt
API_AUTH_ENABLED=true
JWT_ISSUER=https://identity.example.com/
JWT_AUDIENCE=oncall-agent
JWT_JWKS_URL=https://identity.example.com/.well-known/jwks.json
JWT_ALGORITHMS=RS256
JWT_TENANT_CLAIM=tenant_id
JWT_ROLES_CLAIM=roles
JWT_PERMISSIONS_CLAIM=permissions
JWT_CLOCK_SKEW_SECONDS=30
DEFAULT_TENANT_ID=default
```

`JWT_SECRET` and `HS256` are supported for automated tests and controlled
internal environments. Prefer `JWKS_URL` and `RS256` for production because the
API only receives public verification keys.

Every JWT must contain:

- `sub`: stable user or service identity
- `exp`: expiration time
- `iss`: configured issuer
- `aud`: configured API audience
- `tenant_id`: tenant boundary used by PostgreSQL RLS
- `roles`: one or more application roles

The optional `permissions` claim grants narrowly scoped permissions in addition
to role permissions.

## Roles

| Role | Effective access |
| --- | --- |
| `viewer` | Read incidents, tasks, knowledge, reviews, tools, and workflows; ask read-only chat questions |
| `oncall` | Viewer access plus alert handling, diagnosis tasks, review decisions, and workflow execution |
| `sre` | On-call access plus knowledge ingestion and workflow authoring/publishing |
| `admin` | All permissions |

`GET /api/auth/me` returns the validated subject, tenant, roles, and effective
permissions. A missing or invalid token returns `401`; an authenticated
principal without the route permission returns `403`.

## Database isolation

Apply the schema with:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

The migration adds `tenant_id` to every business table and enables PostgreSQL
Row-Level Security with both `USING` and `WITH CHECK` policies. Each SQLAlchemy
transaction sets `app.tenant_id`; PostgreSQL then filters reads and validates
writes inside the database.

Celery messages carry the tenant ID explicitly. Workers restore that tenant
before acquiring Redis locks or opening a database transaction, so delayed work
keeps the same isolation boundary as the originating API request. Recovery
scans use a short-lived internal system scope, then switch back to each task's
tenant before resuming it.

Knowledge chunks also carry `tenant_id`. Milvus receives a tenant filter as
part of the vector query, and chunk primary keys are a hash of tenant plus chunk
ID so equal document paths cannot collide across tenants. Re-ingest existing
knowledge after enabling this version because legacy Milvus rows do not contain
the tenant field.

Use a dedicated, non-superuser PostgreSQL application role in production.
PostgreSQL superusers bypass RLS by design.

Docker Compose uses `POSTGRES_USER` only as the database administrator and
connects API, migration, worker, and beat services with
`POSTGRES_APP_USER` (default `oncall_app`). For an existing Docker volume,
mount the current Compose configuration and run the idempotent role bootstrap:

```powershell
docker compose up -d postgres
docker compose exec postgres bash /docker-entrypoint-initdb.d/10-app-role.sh
```

Verify the active PostgreSQL role and policies with a rolled-back test row:

```powershell
.\.venv\Scripts\python.exe scripts\check_tenant_isolation.py `
  --database-url "postgresql+psycopg://user:password@localhost:5432/oncall_agent"
```

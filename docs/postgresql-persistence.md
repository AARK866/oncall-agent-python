# PostgreSQL Persistence

The production data layer uses SQLAlchemy 2, psycopg 3, PostgreSQL 16, and
Alembic. All incident, diagnosis, task, knowledge-ingestion, workflow,
human-review, checkpoint, and audit records can share one managed PostgreSQL
database.

SQLite remains available when `DATABASE_URL` is empty. This fallback is useful
for isolated tests and development, but production configuration validation
requires PostgreSQL-style database configuration and Alembic-managed schemas.

## Configuration

Add the following values to `.env`:

```dotenv
DATABASE_URL=postgresql+psycopg://oncall:replace-me@localhost:5432/oncall_agent
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20
DATABASE_POOL_TIMEOUT_SECONDS=30
DATABASE_POOL_RECYCLE_SECONDS=1800
DATABASE_AUTO_CREATE_SCHEMA=false
```

Use a secret manager for the database password in production. Add
`?sslmode=require` to the URL when the managed database requires TLS.

`DATABASE_AUTO_CREATE_SCHEMA=false` is important in production. It prevents
application processes from changing schema implicitly and makes Alembic the
only schema authority.

## Start PostgreSQL with Docker

The project Compose file includes PostgreSQL 16:

```powershell
docker compose up -d postgres
docker compose ps postgres
```

The default Compose credentials are for development only:

```text
database: oncall_agent
username: oncall
password: oncall_dev_password
port: 5432
```

Override `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` before using
the Compose stack outside a development machine.

## Apply migrations

Install dependencies, then apply all migrations:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\alembic.exe upgrade head
```

Useful migration commands:

```powershell
.\.venv\Scripts\alembic.exe current
.\.venv\Scripts\alembic.exe history
.\.venv\Scripts\alembic.exe downgrade -1
```

Review and back up production data before running any downgrade.

The Docker image automatically runs `alembic upgrade head` before starting
Uvicorn. The API container waits for the PostgreSQL health check first.

## Verify the data layer

Run the isolated migration acceptance check:

```powershell
.\.venv\Scripts\python.exe scripts\check_database_layer.py
```

After configuring and migrating a real database, run:

```powershell
.\.venv\Scripts\python.exe scripts\check_database_layer.py --configured
```

The configured check validates connectivity, the Alembic revision, an incident
repository round trip, a workflow publish round trip, and cleanup of temporary
acceptance records.

Database readiness is also exposed at:

```text
GET /health/database
```

The endpoint reports only availability, SQL dialect, and schema-management
mode. It never returns the database URL or password.

## Design notes

- SQLAlchemy owns engine creation, connection pooling, transaction boundaries,
  health checks, and dialect selection.
- Existing repository method contracts are preserved to avoid changing the
  Agent, API, and task layers at the same time.
- PostgreSQL workflow publication and rollback acquire row locks before
  revision-sensitive writes.
- Alembic revision `ad29048b8972` creates 17 business tables plus the
  `alembic_version` table.
- Legacy class names beginning with `SQLite` remain for compatibility. New
  code can import neutral names such as `IncidentStore`, `TaskStore`, and
  `WorkflowStore` from `app.storage`.

## Production checklist

- Use a managed PostgreSQL service or a highly available PostgreSQL cluster.
- Create a least-privilege application user and a separate migration user.
- Require TLS and rotate credentials through a secret manager.
- Configure automated backups and test point-in-time recovery.
- Monitor pool saturation, connection wait time, transaction duration, slow
  queries, replication lag, storage growth, and migration status.

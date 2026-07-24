from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.config import settings
from app.main import app
from app.storage import SQLiteIncidentStore
from app.storage.database import Database, normalize_database_url


client = TestClient(app)


def test_database_url_normalization_supports_paths_and_postgresql(tmp_path) -> None:
    sqlite_url = normalize_database_url(tmp_path / "database.db")
    postgres_url = normalize_database_url(
        "postgresql://oncall:secret@db.internal:5432/oncall_agent"
    )

    assert sqlite_url.drivername == "sqlite+pysqlite"
    assert Path(sqlite_url.database).is_absolute()
    assert postgres_url.drivername == "postgresql+psycopg"
    assert postgres_url.database == "oncall_agent"


def test_alembic_migration_creates_and_drops_enterprise_schema(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "migration.db"
    monkeypatch.setattr(
        settings,
        "database_url",
        f"sqlite:///{database_path.as_posix()}",
    )
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    database = Database(settings.database_url)
    table_names = set(inspect(database.engine).get_table_names())
    inspector = inspect(database.engine)

    assert {
        "alembic_version",
        "incidents",
        "diagnosis_tasks",
        "knowledge_index_manifest",
        "workflow_applications",
        "workflow_runs",
        "audit_events",
    }.issubset(table_names)
    assert "tenant_id" in {
        column["name"]
        for column in inspector.get_columns("diagnosis_tasks")
    }
    assert {
        "tenant_id",
        "namespace",
        "doc_id",
    } == set(
        inspector.get_pk_constraint(
            "knowledge_index_manifest"
        )["constrained_columns"]
    )

    command.downgrade(config, "base")
    assert set(inspect(database.engine).get_table_names()) == {"alembic_version"}


def test_alembic_migration_renders_for_postgresql(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql+psycopg://oncall:secret@db.internal:5432/oncall_agent",
    )
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)

    command.upgrade(config, "head", sql=True)

    migration_sql = output.getvalue()
    assert "CREATE TABLE workflow_runs" in migration_sql
    assert "CREATE INDEX idx_workflow_runs_app_status" in migration_sql
    assert "ENABLE ROW LEVEL SECURITY" in migration_sql
    assert "FORCE ROW LEVEL SECURITY" in migration_sql
    assert "CREATE POLICY tenant_isolation" in migration_sql
    assert "CREATE TABLE audit_events" in migration_sql


def test_store_uses_migrated_database_without_runtime_schema_creation(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "production-mode.db"
    monkeypatch.setattr(
        settings,
        "database_url",
        f"sqlite:///{database_path.as_posix()}",
    )
    monkeypatch.setattr(settings, "database_auto_create_schema", False)
    command.upgrade(Config("alembic.ini"), "head")

    store = SQLiteIncidentStore.from_settings()
    incident = store.create_incident(
        title="Database acceptance",
        service="payment-api",
        question="Is the production data layer ready?",
        session_id="database-test",
    )

    assert store.get_incident(incident.incident_id) == incident


def test_database_health_endpoint_reports_dialect(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", None)
    monkeypatch.setattr(
        settings,
        "incident_db_path",
        str(tmp_path / "health.db"),
    )

    response = client.get("/health/database")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dialect": "sqlite",
        "schema_management": "auto_create",
    }

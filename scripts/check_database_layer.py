from __future__ import annotations

import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.schemas import WorkflowApplicationCreate
from app.storage import SQLiteIncidentStore, SQLiteWorkflowStore
from app.storage.database import Database, database_from_settings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate SQLAlchemy connectivity and Alembic migrations."
    )
    parser.add_argument(
        "--configured",
        action="store_true",
        help="Check the configured database without changing its schema.",
    )
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL for the configured database check.",
    )
    args = parser.parse_args()

    if args.database_url:
        settings.database_url = args.database_url
    if args.configured or args.database_url:
        return _check_configured_database()
    return _check_isolated_migration()


def _check_configured_database() -> int:
    database = database_from_settings()
    try:
        database.ping()
        with database.engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
        _cleanup_configured_acceptance(database)
        store = SQLiteIncidentStore(
            settings.database_url or settings.incident_db_path,
            auto_create_schema=False,
        )
        incident = store.create_incident(
            title="Configured database acceptance",
            service="payment-api",
            question="Validate configured persistence.",
            session_id="configured-database-acceptance",
        )
        incident_repository_ok = store.get_incident(incident.incident_id) == incident

        workflow_store = SQLiteWorkflowStore(
            settings.database_url or settings.workflow_db_path,
            auto_create_schema=False,
        )
        workflow_app, draft = workflow_store.create_application(
            WorkflowApplicationCreate(
                name="Configured database acceptance",
                description="Temporary PostgreSQL verification workflow.",
            )
        )
        version = workflow_store.publish_draft(
            app_id=workflow_app.app_id,
            expected_revision=draft.revision,
            published_by="database-acceptance",
        )
        workflow_repository_ok = (
            workflow_store.get_version(
                workflow_app.app_id,
                version.version_number,
            )
            == version
        )
        repository_ok = incident_repository_ok and workflow_repository_ok
        _cleanup_configured_acceptance(database)
    except Exception as exc:
        try:
            _cleanup_configured_acceptance(database)
        except Exception:
            pass
        print("Configured database check")
        print("- status: FAIL")
        print(f"- dialect: {database.dialect}")
        print(f"- error: {type(exc).__name__}: {exc}")
        return 1

    print("Configured database check")
    print("- status: PASS")
    print(f"- dialect: {database.dialect}")
    print(f"- migration_revision: {revision or 'none'}")
    print(f"- repository_roundtrip: {'PASS' if repository_ok else 'FAIL'}")
    return 0 if revision and repository_ok else 1


def _cleanup_configured_acceptance(database: Database) -> None:
    with database.connect() as connection:
        connection.execute(
            """
            DELETE FROM workflow_review_requests
            WHERE run_id IN (
                SELECT run_id FROM workflow_runs
                WHERE app_id IN (
                    SELECT app_id FROM workflow_applications WHERE name = ?
                )
            )
            """,
            ("Configured database acceptance",),
        )
        connection.execute(
            """
            DELETE FROM workflow_run_events
            WHERE run_id IN (
                SELECT run_id FROM workflow_runs
                WHERE app_id IN (
                    SELECT app_id FROM workflow_applications WHERE name = ?
                )
            )
            """,
            ("Configured database acceptance",),
        )
        for table_name in (
            "workflow_runs",
            "workflow_audit_events",
            "workflow_versions",
            "workflow_drafts",
        ):
            connection.execute(
                f"""
                DELETE FROM {table_name}
                WHERE app_id IN (
                    SELECT app_id FROM workflow_applications WHERE name = ?
                )
                """,
                ("Configured database acceptance",),
            )
        connection.execute(
            "DELETE FROM workflow_applications WHERE name = ?",
            ("Configured database acceptance",),
        )
        connection.execute(
            "DELETE FROM incidents WHERE title = ?",
            ("Configured database acceptance",),
        )


def _check_isolated_migration() -> int:
    original_database_url = settings.database_url
    original_auto_create = settings.database_auto_create_schema
    checks: list[tuple[str, bool, str]] = []

    try:
        with TemporaryDirectory(prefix="oncall-database-") as temp_dir:
            database_path = Path(temp_dir) / "acceptance.db"
            settings.database_url = f"sqlite:///{database_path.as_posix()}"
            settings.database_auto_create_schema = False
            config = Config(str(PROJECT_ROOT / "alembic.ini"))

            command.upgrade(config, "head")
            database = Database(settings.database_url)
            tables = set(inspect(database.engine).get_table_names())
            checks.append(
                (
                    "migration",
                    "workflow_runs" in tables and "incidents" in tables,
                    f"{len(tables)} tables",
                )
            )

            database.ping()
            checks.append(("connectivity", True, database.dialect))

            store = SQLiteIncidentStore.from_settings()
            incident = store.create_incident(
                title="Database acceptance",
                service="payment-api",
                question="Validate migrated persistence.",
                session_id="database-acceptance",
            )
            checks.append(
                (
                    "repository",
                    store.get_incident(incident.incident_id) == incident,
                    incident.incident_id,
                )
            )

            command.downgrade(config, "base")
            remaining = set(inspect(database.engine).get_table_names())
            checks.append(
                (
                    "downgrade",
                    remaining == {"alembic_version"},
                    f"{len(remaining)} table",
                )
            )
    except Exception as exc:
        checks.append(("unexpected_error", False, f"{type(exc).__name__}: {exc}"))
    finally:
        settings.database_url = original_database_url
        settings.database_auto_create_schema = original_auto_create

    print("Enterprise database acceptance")
    for name, passed, detail in checks:
        print(f"- [{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failures = sum(not passed for _, passed, _ in checks)
    print(f"\nSummary: {len(checks) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

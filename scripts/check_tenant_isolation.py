import argparse
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify PostgreSQL row-level tenant isolation.",
    )
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="PostgreSQL URL. Defaults to DATABASE_URL.",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    engine = create_engine(args.database_url)
    incident_id = f"rls_check_{uuid4().hex}"
    with engine.connect() as connection:
        transaction = connection.begin()
        role_name, is_superuser, bypasses_rls = connection.execute(
            text(
                """
                SELECT current_user, rolsuper, rolbypassrls
                FROM pg_roles
                WHERE rolname = current_user
                """
            )
        ).one()
        try:
            _set_context(connection, tenant_id="tenant-check-a")
            connection.execute(
                text(
                    """
                    INSERT INTO incidents (
                        incident_id, title, service, question, session_id,
                        severity, status, labels_json, created_at, updated_at
                    )
                    VALUES (
                        :incident_id, 'RLS acceptance', 'security-check',
                        'Can another tenant read this?', 'rls-check',
                        'warning', 'open', '{}',
                        '2026-07-23T00:00:00', '2026-07-23T00:00:00'
                    )
                    """
                ),
                {"incident_id": incident_id},
            )

            _set_context(connection, tenant_id="tenant-check-b")
            tenant_b_count = _incident_count(connection, incident_id)

            _set_context(connection, tenant_id="tenant-check-a")
            tenant_a_count = _incident_count(connection, incident_id)
        finally:
            transaction.rollback()

    engine.dispose()
    passed = tenant_a_count == 1 and tenant_b_count == 0
    print("Tenant RLS acceptance")
    print(f"- database role: {role_name}")
    print(f"- superuser: {is_superuser}")
    print(f"- bypasses RLS: {bypasses_rls}")
    print(f"- tenant-check-a rows: {tenant_a_count}")
    print(f"- tenant-check-b rows: {tenant_b_count}")
    print(f"- status: {'PASS' if passed else 'FAIL'}")
    if is_superuser or bypasses_rls:
        print("- fix: use the dedicated POSTGRES_APP_USER role")
    return 0 if passed else 1


def _set_context(connection, *, tenant_id: str) -> None:
    connection.execute(
        text(
            """
            SELECT
                set_config('app.system_access', 'false', true),
                set_config('app.tenant_id', :tenant_id, true)
            """
        ),
        {"tenant_id": tenant_id},
    )


def _incident_count(connection, incident_id: str) -> int:
    return int(
        connection.execute(
            text(
                "SELECT COUNT(*) FROM incidents "
                "WHERE incident_id = :incident_id"
            ),
            {"incident_id": incident_id},
        ).scalar_one()
    )


if __name__ == "__main__":
    raise SystemExit(main())

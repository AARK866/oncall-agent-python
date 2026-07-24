"""add security audit events

Revision ID: e8a1d3f74c20
Revises: c6f4a2e91b7d
Create Date: 2026-07-24 10:30:00

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e8a1d3f74c20"
down_revision: str | Sequence[str] | None = "c6f4a2e91b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column(
            "tenant_id",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'default'"),
        ),
        sa.Column("audit_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text()),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("request_method", sa.Text()),
        sa.Column("request_path", sa.Text()),
        sa.Column("status_code", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("client_ip", sa.Text()),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "audit_id",
            name="pk_audit_events",
        ),
    )
    op.create_index(
        "idx_audit_events_tenant",
        "audit_events",
        ["tenant_id"],
    )
    op.create_index(
        "idx_audit_events_event_created",
        "audit_events",
        ["event_type", sa.literal_column("created_at DESC")],
    )
    op.create_index(
        "idx_audit_events_outcome_created",
        "audit_events",
        ["outcome", sa.literal_column("created_at DESC")],
    )

    if op.get_bind().dialect.name == "postgresql":
        _enable_postgresql_rls()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DROP POLICY IF EXISTS tenant_isolation "
                "ON audit_events"
            )
        )
    op.drop_table("audit_events")


def _enable_postgresql_rls() -> None:
    tenant_expression = (
        "tenant_id = COALESCE("
        "NULLIF(current_setting('app.tenant_id', true), ''), "
        "'default'"
        ")"
    )
    system_expression = (
        "current_setting('app.system_access', true) = 'true'"
    )
    policy_expression = f"({system_expression} OR {tenant_expression})"

    op.execute(
        sa.text(
            "ALTER TABLE audit_events ALTER COLUMN tenant_id "
            "SET DEFAULT COALESCE("
            "NULLIF(current_setting('app.tenant_id', true), ''), "
            "'default'"
            ")"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE audit_events FORCE ROW LEVEL SECURITY"
        )
    )
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation ON audit_events "
            f"USING ({policy_expression}) "
            f"WITH CHECK ({policy_expression})"
        )
    )

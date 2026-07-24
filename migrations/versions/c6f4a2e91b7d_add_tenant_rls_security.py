"""add tenant columns and PostgreSQL row-level security

Revision ID: c6f4a2e91b7d
Revises: ad29048b8972
Create Date: 2026-07-23 22:00:00

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c6f4a2e91b7d"
down_revision: str | Sequence[str] | None = "ad29048b8972"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "incidents",
    "diagnoses",
    "knowledge_index_manifest",
    "knowledge_ingestion_tasks",
    "knowledge_ingestion_attempts",
    "diagnosis_tasks",
    "alert_groups",
    "diagnosis_task_events",
    "ops_graph_checkpoints",
    "human_review_requests",
    "workflow_applications",
    "workflow_drafts",
    "workflow_versions",
    "workflow_runs",
    "workflow_run_events",
    "workflow_review_requests",
    "workflow_audit_events",
)


def upgrade() -> None:
    for table_name in TENANT_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "tenant_id",
                    sa.Text(),
                    nullable=False,
                    server_default=sa.text("'default'"),
                )
            )
            batch_op.create_index(
                f"idx_{table_name}_tenant",
                ["tenant_id"],
                unique=False,
            )

    with op.batch_alter_table("knowledge_index_manifest") as batch_op:
        batch_op.drop_constraint(
            "pk_knowledge_index_manifest",
            type_="primary",
        )
        batch_op.create_primary_key(
            "pk_knowledge_index_manifest",
            ["tenant_id", "namespace", "doc_id"],
        )

    with op.batch_alter_table("alert_groups") as batch_op:
        batch_op.drop_constraint(
            "uq_alert_groups_dedupe_key",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_alert_groups_tenant_dedupe_key",
            ["tenant_id", "dedupe_key"],
        )

    if _dialect_name() == "postgresql":
        _enable_postgresql_rls()


def downgrade() -> None:
    if _dialect_name() == "postgresql":
        for table_name in TENANT_TABLES:
            op.execute(
                sa.text(
                    f'DROP POLICY IF EXISTS tenant_isolation ON "{table_name}"'
                )
            )
            op.execute(
                sa.text(
                    f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'
                )
            )

    with op.batch_alter_table("alert_groups") as batch_op:
        batch_op.drop_constraint(
            "uq_alert_groups_tenant_dedupe_key",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_alert_groups_dedupe_key",
            ["dedupe_key"],
        )

    with op.batch_alter_table("knowledge_index_manifest") as batch_op:
        batch_op.drop_constraint(
            "pk_knowledge_index_manifest",
            type_="primary",
        )
        batch_op.create_primary_key(
            "pk_knowledge_index_manifest",
            ["namespace", "doc_id"],
        )

    for table_name in reversed(TENANT_TABLES):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_index(f"idx_{table_name}_tenant")
            batch_op.drop_column("tenant_id")


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

    for table_name in TENANT_TABLES:
        op.execute(
            sa.text(
                f'ALTER TABLE "{table_name}" ALTER COLUMN tenant_id '
                "SET DEFAULT COALESCE("
                "NULLIF(current_setting('app.tenant_id', true), ''), "
                "'default'"
                ")"
            )
        )
        op.execute(
            sa.text(
                f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'
            )
        )
        op.execute(
            sa.text(
                f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'
            )
        )
        op.execute(
            sa.text(
                f'CREATE POLICY tenant_isolation ON "{table_name}" '
                f"USING ({policy_expression}) "
                f"WITH CHECK ({policy_expression})"
            )
        )


def _dialect_name() -> str:
    return op.get_bind().dialect.name

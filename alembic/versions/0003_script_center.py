"""Script registry, immutable versions, and script task metadata."""

from alembic import op
import sqlalchemy as sa


revision = "0003_script_center"
down_revision = "0002_security"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scripts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "name", name="uq_script_name_workspace"),
    )
    op.create_index("ix_scripts_workspace_id", "scripts", ["workspace_id"])
    op.create_index("ix_scripts_status", "scripts", ["status"])
    op.create_index("ix_scripts_created_by", "scripts", ["created_by"])
    op.create_table(
        "script_versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("script_id", sa.String(32), sa.ForeignKey("scripts.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("params_schema", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("script_id", "version", name="uq_script_version"),
    )
    op.create_index("ix_script_versions_script_id", "script_versions", ["script_id"])
    op.create_index("ix_script_versions_sha256", "script_versions", ["sha256"])
    op.create_index("ix_script_versions_created_by", "script_versions", ["created_by"])
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("script_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("script_version_id", sa.String(32), nullable=True))
        batch.create_foreign_key("fk_tasks_script_id", "scripts", ["script_id"], ["id"])
        batch.create_foreign_key("fk_tasks_script_version_id", "script_versions", ["script_version_id"], ["id"])
    op.create_index("ix_tasks_script_id", "tasks", ["script_id"])
    op.create_index("ix_tasks_script_version_id", "tasks", ["script_version_id"])
    with op.batch_alter_table("activities") as batch:
        batch.add_column(sa.Column("script_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("script_version_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("logs", sa.JSON(), nullable=True))
        batch.create_foreign_key("fk_activities_script_id", "scripts", ["script_id"], ["id"])
        batch.create_foreign_key("fk_activities_script_version_id", "script_versions", ["script_version_id"], ["id"])
    op.create_index("ix_activities_script_id", "activities", ["script_id"])
    op.create_index("ix_activities_script_version_id", "activities", ["script_version_id"])
    for name, length in (("profile_id", 100), ("task_id", 32), ("script_id", 32), ("script_version_id", 32)):
        op.add_column("audit_logs", sa.Column(name, sa.String(length), nullable=True))
        op.create_index(f"ix_audit_logs_{name}", "audit_logs", [name])


def downgrade():
    for name in ("profile_id", "task_id", "script_id", "script_version_id"):
        op.drop_index(f"ix_audit_logs_{name}", table_name="audit_logs")
        op.drop_column("audit_logs", name)
    op.drop_index("ix_activities_script_version_id", table_name="activities")
    op.drop_index("ix_activities_script_id", table_name="activities")
    with op.batch_alter_table("activities") as batch:
        batch.drop_constraint("fk_activities_script_version_id", type_="foreignkey")
        batch.drop_constraint("fk_activities_script_id", type_="foreignkey")
        batch.drop_column("logs")
        batch.drop_column("script_version_id")
        batch.drop_column("script_id")
    op.drop_index("ix_tasks_script_version_id", table_name="tasks")
    op.drop_index("ix_tasks_script_id", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_script_version_id", type_="foreignkey")
        batch.drop_constraint("fk_tasks_script_id", type_="foreignkey")
        batch.drop_column("script_version_id")
        batch.drop_column("script_id")
    op.drop_table("script_versions")
    op.drop_table("scripts")

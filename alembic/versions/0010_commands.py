"""Command channel records for HTTP fallback control."""

from alembic import op
import sqlalchemy as sa


revision = "0010_commands"
down_revision = "0009_ai_task_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commands",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("profile_id", sa.String(length=100), nullable=True),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("command_type", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "idempotency_key", name="uq_command_idempotency_agent"),
    )
    for column in ("workspace_id", "agent_id", "profile_id", "task_id", "command_type", "status", "created_at"):
        op.create_index(f"ix_commands_{column}", "commands", [column], unique=False)


def downgrade() -> None:
    op.drop_table("commands")

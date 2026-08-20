"""Credential capability probe metadata without credential values."""

from alembic import op
import sqlalchemy as sa


revision = "0011_credential_capabilities"
down_revision = "0010_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credential_capabilities",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("profile_id", sa.String(length=100), nullable=False),
        sa.Column("command_id", sa.String(length=32), nullable=False),
        sa.Column("probe_version", sa.String(length=20), nullable=False),
        sa.Column("browser_reachable", sa.Boolean(), nullable=False),
        sa.Column("cookie_read_supported", sa.Boolean(), nullable=False),
        sa.Column("cookie_write_supported", sa.Boolean(), nullable=False),
        sa.Column("credential_snapshot_allowed", sa.Boolean(), nullable=False),
        sa.Column("evidence", sa.String(length=80), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["command_id"], ["commands.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "profile_id", name="uq_credential_capability_agent_profile"),
        sa.UniqueConstraint("command_id"),
    )
    for column in ("workspace_id", "agent_id", "profile_id", "command_id", "checked_at"):
        op.create_index(f"ix_credential_capabilities_{column}", "credential_capabilities", [column], unique=column == "command_id")


def downgrade() -> None:
    op.drop_table("credential_capabilities")

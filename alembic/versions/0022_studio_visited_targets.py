"""Add studio_visited_targets table and studio_token to workspaces for cross-device deduplication."""

from uuid import uuid4
from alembic import op
import sqlalchemy as sa


revision = "0022_studio_visited_targets"
down_revision = "0021_proxy_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add studio_token to workspaces
    op.add_column("workspaces", sa.Column("studio_token", sa.String(length=64), nullable=True))
    op.create_index("ix_workspaces_studio_token", "workspaces", ["studio_token"], unique=True)

    # Backfill studio_token for any existing workspaces
    bind = op.get_bind()
    workspaces = sa.table("workspaces", sa.column("id", sa.String), sa.column("studio_token", sa.String))
    rows = bind.execute(sa.select(workspaces.c.id).where(workspaces.c.studio_token.is_(None))).fetchall()
    for row in rows:
        bind.execute(
            workspaces.update()
            .where(workspaces.c.id == row[0])
            .values(studio_token=f"std_{uuid4().hex[:16]}")
        )

    # 2. Create studio_visited_targets
    op.create_table(
        "studio_visited_targets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(length=32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_handle", sa.String(length=64), nullable=False),
        sa.Column("operator_device", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("account_tag", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("action", sa.String(length=30), nullable=False, server_default="follow"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="CLAIMED"),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("owner_key", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "target_handle", name="uq_workspace_target_handle"),
    )
    op.create_index("ix_studio_targets_workspace_id", "studio_visited_targets", ["workspace_id"])
    op.create_index("ix_studio_targets_handle", "studio_visited_targets", ["target_handle"])
    op.create_index("ix_studio_targets_expires_at", "studio_visited_targets", ["expires_at"])
    op.create_index("ix_studio_targets_workspace_exp", "studio_visited_targets", ["workspace_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_studio_targets_workspace_exp", table_name="studio_visited_targets")
    op.drop_index("ix_studio_targets_expires_at", table_name="studio_visited_targets")
    op.drop_index("ix_studio_targets_handle", table_name="studio_visited_targets")
    op.drop_index("ix_studio_targets_workspace_id", table_name="studio_visited_targets")
    op.drop_table("studio_visited_targets")

    op.drop_index("ix_workspaces_studio_token", table_name="workspaces")
    op.drop_column("workspaces", "studio_token")

"""Bind published Python automation engines to runtime IDs."""

from alembic import op
import sqlalchemy as sa


revision = "0020_agent_engine_assignments"
down_revision = "0019_provider_actual_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("engine_access_mode", sa.String(length=20), nullable=False, server_default="ALL"),
    )
    op.create_table(
        "agent_engine_grants",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("agent_id", sa.String(length=32), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("engine_id", sa.String(length=48), nullable=False),
        sa.Column("granted_by", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_id", "engine_id", name="uq_agent_engine_grant"),
    )
    op.create_index("ix_agent_engine_grants_agent_id", "agent_engine_grants", ["agent_id"])
    op.create_index("ix_agent_engine_grants_engine_id", "agent_engine_grants", ["engine_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_engine_grants_engine_id", table_name="agent_engine_grants")
    op.drop_index("ix_agent_engine_grants_agent_id", table_name="agent_engine_grants")
    op.drop_table("agent_engine_grants")
    op.drop_column("agents", "engine_access_mode")

"""Bind Windows Agents to a device and record connection IP metadata."""

from alembic import op
import sqlalchemy as sa


revision = "0015_agent_device_bindings"
down_revision = "0014_user_ai_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("bound_device_id", sa.String(length=128), nullable=True))
    op.add_column("agents", sa.Column("bound_ip", sa.String(length=64), nullable=True))
    op.add_column("agents", sa.Column("last_ip", sa.String(length=64), nullable=True))
    op.add_column("agents", sa.Column("ip_country", sa.String(length=8), nullable=False, server_default="UNKNOWN"))
    op.add_column("agents", sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agents", sa.Column("registered_by_user_id", sa.String(length=32), nullable=True))
    op.create_index("ix_agents_bound_device_id", "agents", ["bound_device_id"])
    op.create_index("ix_agents_registered_by_user_id", "agents", ["registered_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_agents_registered_by_user_id", table_name="agents")
    op.drop_index("ix_agents_bound_device_id", table_name="agents")
    op.drop_column("agents", "registered_by_user_id")
    op.drop_column("agents", "bound_at")
    op.drop_column("agents", "ip_country")
    op.drop_column("agents", "last_ip")
    op.drop_column("agents", "bound_ip")
    op.drop_column("agents", "bound_device_id")

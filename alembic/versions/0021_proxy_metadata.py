"""Store safe per-profile proxy metadata for account identification."""

from alembic import op
import sqlalchemy as sa


revision = "0021_proxy_metadata"
down_revision = "0020_agent_engine_assignments"
branch_labels = None
depends_on = None


_COLUMNS = (
    ("proxy_id", sa.String(length=120), ""),
    ("proxy_name", sa.String(length=120), ""),
    ("proxy_protocol", sa.String(length=30), ""),
    ("proxy_host", sa.String(length=255), ""),
    ("proxy_port", sa.String(length=10), ""),
    ("proxy_status", sa.String(length=30), "UNKNOWN"),
    ("exit_ip", sa.String(length=64), ""),
)


def upgrade() -> None:
    for table in ("profiles", "accounts"):
        for name, column_type, default in _COLUMNS:
            op.add_column(table, sa.Column(name, column_type, nullable=False, server_default=default))
        op.add_column(table, sa.Column("proxy_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for table in ("accounts", "profiles"):
        op.drop_column(table, "proxy_checked_at")
        for name, _, _ in reversed(_COLUMNS):
            op.drop_column(table, name)

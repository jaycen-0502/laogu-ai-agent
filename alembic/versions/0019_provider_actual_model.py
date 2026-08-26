"""Store the model identifier returned by a real provider request."""

from alembic import op
import sqlalchemy as sa


revision = "0019_provider_actual_model"
down_revision = "0018_web_single_session"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_last_seen_at", "users", ["last_seen_at"], unique=False)
    op.add_column(
        "ai_providers",
        sa.Column("last_actual_model", sa.String(length=160), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("ai_providers", "last_actual_model")
    op.drop_index("ix_users_last_seen_at", table_name="users")
    op.drop_column("users", "last_seen_at")

"""Invalidate older Web sessions when an account logs in again."""

from alembic import op
import sqlalchemy as sa


revision = "0018_web_single_session"
down_revision = "0017_telegram_translation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("web_session_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "web_session_version")

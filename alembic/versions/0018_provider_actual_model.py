"""Store the model identifier returned by a real provider request."""

from alembic import op
import sqlalchemy as sa


revision = "0018_provider_actual_model"
down_revision = "0017_telegram_translation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_providers",
        sa.Column("last_actual_model", sa.String(length=160), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("ai_providers", "last_actual_model")

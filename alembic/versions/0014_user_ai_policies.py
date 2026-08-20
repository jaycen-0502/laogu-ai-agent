"""Per-user AI feature and model assignments."""

from alembic import op
import sqlalchemy as sa


revision = "0014_user_ai_policies"
down_revision = "0013_remote_licenses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_ai_policies",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("feature", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("provider_id", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("updated_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_providers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "feature", name="uq_user_ai_policy_feature"),
    )
    for column in ("user_id", "workspace_id", "feature", "provider_id", "updated_by"):
        op.create_index(f"ix_user_ai_policies_{column}", "user_ai_policies", [column])


def downgrade() -> None:
    op.drop_table("user_ai_policies")

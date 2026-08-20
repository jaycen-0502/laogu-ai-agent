"""Remote license registration, device checks and revocation metadata."""

from alembic import op
import sqlalchemy as sa


revision = "0013_remote_licenses"
down_revision = "0012_user_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "licenses",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("license_id", sa.String(length=120), nullable=False),
        sa.Column("customer", sa.String(length=200), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("activation_code_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("offline_grace_days", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("license_id"),
        sa.UniqueConstraint("activation_code_hash"),
    )
    for column in ("license_id", "activation_code_hash", "expires_at", "status", "created_by"):
        op.create_index(f"ix_licenses_{column}", "licenses", [column], unique=column in {"license_id", "activation_code_hash"})

    op.create_table(
        "license_devices",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("license_id", sa.String(length=32), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("install_public_key_hash", sa.String(length=64), nullable=False),
        sa.Column("app_version", sa.String(length=80), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_ip", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("license_id", "device_id", name="uq_license_device"),
    )
    for column in ("license_id", "device_id", "last_seen_at", "status"):
        op.create_index(f"ix_license_devices_{column}", "license_devices", [column])

    op.create_table(
        "license_checks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("license_id", sa.String(length=32), nullable=True),
        sa.Column("external_license_id", sa.String(length=120), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("app_version", sa.String(length=80), nullable=False),
        sa.Column("result", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("license_id", "external_license_id", "device_id", "result", "checked_at"):
        op.create_index(f"ix_license_checks_{column}", "license_checks", [column])

    op.create_table(
        "license_revocations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("license_id", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("revoked_by", sa.String(length=32), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("license_id"),
    )
    for column in ("license_id", "revoked_by", "revoked_at"):
        op.create_index(f"ix_license_revocations_{column}", "license_revocations", [column], unique=column == "license_id")


def downgrade() -> None:
    op.drop_table("license_revocations")
    op.drop_table("license_checks")
    op.drop_table("license_devices")
    op.drop_table("licenses")

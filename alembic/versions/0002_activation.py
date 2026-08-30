"""Add pending-device activation code, challenge, and expiry.

Revision ID: 0002_activation
Revises: 0001_devices
Create Date: 2026-08-24
"""

revision = "0002_activation"
down_revision = "0001_devices"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("devices", sa.Column("activation_code", sa.String(length=8), nullable=True))
    op.add_column("devices", sa.Column("activation_challenge", sa.String(length=64), nullable=True))
    op.add_column(
        "devices",
        sa.Column("activation_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_devices_activation_code", "devices", ["activation_code"])


def downgrade() -> None:
    op.drop_index("ix_devices_activation_code", table_name="devices")
    op.drop_column("devices", "activation_expires_at")
    op.drop_column("devices", "activation_challenge")
    op.drop_column("devices", "activation_code")

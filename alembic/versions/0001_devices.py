revision = "0001_devices"
down_revision = None
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.String(length=32), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("serial_number", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_user_agent", sa.String(length=256), nullable=True),
        sa.UniqueConstraint("device_id", "client_id", name="uq_device_client"),
    )
    op.create_index("ix_devices_device_id", "devices", ["device_id"])
    op.create_index("ix_devices_client_id", "devices", ["client_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=512), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_index("ix_devices_client_id", table_name="devices")
    op.drop_index("ix_devices_device_id", table_name="devices")
    op.drop_table("devices")

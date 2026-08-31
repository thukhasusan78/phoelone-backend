"""Owner memory, care meters, and achievements.

Revision ID: 0004_companion_memory
Revises: 0003_token_ciphertext
Create Date: 2026-08-31
"""

revision = "0004_companion_memory"
down_revision = "0003_token_ciphertext"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.create_table(
        "owner_memory",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=32), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("owner_name", sa.String(length=48), nullable=False, server_default=""),
        sa.Column("nickname", sa.String(length=48), nullable=False, server_default=""),
        sa.Column("likes", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="my-MM"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "client_id", name="uq_owner_memory_device"),
    )
    op.create_index("ix_owner_memory_device_id", "owner_memory", ["device_id"])
    op.create_index("ix_owner_memory_client_id", "owner_memory", ["client_id"])

    op.create_table(
        "care_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=32), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("happiness", sa.Integer(), nullable=False, server_default="55"),
        sa.Column("energy", sa.Integer(), nullable=False, server_default="70"),
        sa.Column("bond", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("streak_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chat_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_touch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_streak_on", sa.String(length=16), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "client_id", name="uq_care_state_device"),
    )
    op.create_index("ix_care_state_device_id", "care_state", ["device_id"])
    op.create_index("ix_care_state_client_id", "care_state", ["client_id"])

    op.create_table(
        "achievements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=32), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "client_id", "code", name="uq_achievement_code"),
    )
    op.create_index("ix_achievements_device_id", "achievements", ["device_id"])
    op.create_index("ix_achievements_client_id", "achievements", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_achievements_client_id", table_name="achievements")
    op.drop_index("ix_achievements_device_id", table_name="achievements")
    op.drop_table("achievements")
    op.drop_index("ix_care_state_client_id", table_name="care_state")
    op.drop_index("ix_care_state_device_id", table_name="care_state")
    op.drop_table("care_state")
    op.drop_index("ix_owner_memory_client_id", table_name="owner_memory")
    op.drop_index("ix_owner_memory_device_id", table_name="owner_memory")
    op.drop_table("owner_memory")

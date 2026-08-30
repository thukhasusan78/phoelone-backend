"""Store wrapped WebSocket tokens so OTA can echo a stable bearer.

Revision ID: 0003_token_ciphertext
Revises: 0002_activation
Create Date: 2026-08-25
"""

revision = "0003_token_ciphertext"
down_revision = "0002_activation"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("devices", sa.Column("token_ciphertext", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "token_ciphertext")

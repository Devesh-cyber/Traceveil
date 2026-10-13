"""takedown tracking

Revision ID: a7c3e91d5b20
Revises: 7037f52600ac
Create Date: 2026-10-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a7c3e91d5b20"
down_revision: Union[str, Sequence[str], None] = "7037f52600ac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("takedown_requests", sa.Column("contact_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("takedown_requests", sa.Column("contact_source", sa.String(length=500), nullable=True))
    op.add_column("takedown_requests", sa.Column("sent_via", sa.String(length=32), nullable=True))
    op.add_column("takedown_requests", sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("takedown_requests", "history")
    op.drop_column("takedown_requests", "sent_via")
    op.drop_column("takedown_requests", "contact_source")
    op.drop_column("takedown_requests", "contact_verified")

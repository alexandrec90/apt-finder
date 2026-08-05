"""create listings

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-08-04

The one lake table this project has adopted (see `ADOPTED_LAKE_TABLES` in
`migrations/env.py`). The model is declared in `data_lake/db/models.py` — scraped public
listings are ordinary shared content, and the lake's boundary is account-shape, not
subject matter. The lake owns no migrations, so the DDL lives here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"  # pragma: allowlist secret - Alembic revision id, not a credential
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "listings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_cad", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("price_period", sa.String(length=16), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("province", sa.String(length=8), nullable=True),
        sa.Column("postal_prefix", sa.String(length=3), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("rooms", sa.Float(), nullable=True),
        sa.Column("bedrooms", sa.Float(), nullable=True),
        sa.Column("unit_type", sa.String(length=32), nullable=True),
        sa.Column("is_room_rental", sa.Boolean(), nullable=True),
        sa.Column("washer", sa.String(length=8), nullable=True),
        sa.Column("dryer", sa.String(length=8), nullable=True),
        sa.Column("fridge", sa.String(length=8), nullable=True),
        sa.Column("stove", sa.String(length=8), nullable=True),
        sa.Column("dishwasher", sa.String(length=8), nullable=True),
        sa.Column("verdict", sa.String(length=8), nullable=False),
        sa.Column("verdict_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seller_hash", sa.String(length=64), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "external_id"),
    )
    op.create_index("ix_listings_verdict_seen", "listings", ["verdict", "last_seen_at"])
    op.create_index("ix_listings_posted_at", "listings", ["posted_at"])


def downgrade() -> None:
    op.drop_index("ix_listings_posted_at", table_name="listings")
    op.drop_index("ix_listings_verdict_seen", table_name="listings")
    op.drop_table("listings")

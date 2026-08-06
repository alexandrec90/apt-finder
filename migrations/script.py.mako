"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

## Alembic revision ids are 12 hex characters, which detect-secrets scores as a
## HexHighEntropyString on every single migration. Allowlisting here, in the producer,
## is what stops each new revision from failing its own first commit.
revision: str = ${repr(up_revision)}  # pragma: allowlist secret - Alembic revision id, not a credential
down_revision: str | None = ${repr(down_revision)}  # pragma: allowlist secret - Alembic revision id, not a credential
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}

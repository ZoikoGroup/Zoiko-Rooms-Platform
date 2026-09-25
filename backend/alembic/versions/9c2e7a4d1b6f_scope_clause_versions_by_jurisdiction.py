"""scope agreement clause versions by jurisdiction and agreement class

Revision ID: 9c2e7a4d1b6f
Revises: f48724cd179e
Create Date: 2026-09-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9c2e7a4d1b6f'
down_revision: Union[str, None] = 'f48724cd179e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # (clause_id, version) was globally unique, so a second jurisdiction could
    # never have its own v1 of a clause England already defines.
    with op.batch_alter_table('agreement_clause_definitions') as batch_op:
        batch_op.drop_constraint('uq_agreement_clause_definitions_clause_id_version', type_='unique')
        batch_op.create_unique_constraint(
            'uq_agreement_clause_definitions_clause_scope_version',
            ['clause_id', 'jurisdiction_scope', 'agreement_class', 'version'],
        )


def downgrade() -> None:
    with op.batch_alter_table('agreement_clause_definitions') as batch_op:
        batch_op.drop_constraint('uq_agreement_clause_definitions_clause_scope_version', type_='unique')
        batch_op.create_unique_constraint(
            'uq_agreement_clause_definitions_clause_id_version', ['clause_id', 'version'],
        )

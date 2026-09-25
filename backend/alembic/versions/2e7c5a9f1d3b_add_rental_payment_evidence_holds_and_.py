"""add rental payment evidence holds and self-correction actors

Revision ID: 2e7c5a9f1d3b
Revises: 9d4f7b2e6a3c
Create Date: 2026-09-21 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2e7c5a9f1d3b'
down_revision: Union[str, None] = '9d4f7b2e6a3c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-PAY-002 Section 11: 'Append corrective event -- Tenant: Controlled,
    # Landlord/Agent: Controlled' -- a correction may now be made by a
    # non-admin actor, so actor_admin_id becomes optional and gains two
    # siblings; exactly one of the three is set per row.
    op.alter_column('rental_payment_corrections', 'actor_admin_id', existing_type=sa.Integer(), existing_nullable=False, nullable=True)
    op.add_column('rental_payment_corrections', sa.Column('actor_guest_id', sa.String(length=20), sa.ForeignKey('guests.id'), nullable=True))
    op.add_column('rental_payment_corrections', sa.Column('actor_party_id', sa.Integer(), sa.ForeignKey('parties.id'), nullable=True))

    # ZR-PAY-002 Section 10/13: 'legal hold and deletion exceptions.'
    op.create_table(
        'rental_payment_evidence_holds',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('artifact_id', sa.Integer(), sa.ForeignKey('evidence_artifacts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='ACTIVE'),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('placed_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=False),
        sa.Column('placed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('released_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_rental_payment_evidence_holds_artifact_id', 'rental_payment_evidence_holds', ['artifact_id'])


def downgrade() -> None:
    op.drop_index('ix_rental_payment_evidence_holds_artifact_id', table_name='rental_payment_evidence_holds')
    op.drop_table('rental_payment_evidence_holds')

    op.drop_column('rental_payment_corrections', 'actor_party_id')
    op.drop_column('rental_payment_corrections', 'actor_guest_id')
    op.alter_column('rental_payment_corrections', 'actor_admin_id', existing_type=sa.Integer(), existing_nullable=True, nullable=False)

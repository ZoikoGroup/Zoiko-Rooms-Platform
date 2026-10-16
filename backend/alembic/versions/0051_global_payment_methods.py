"""Global payment methods (ZR-PAY-METHODS-GLOBAL-001)

Payment records and returns get a separate rail/provider subtype, an "Other"
method description and a label/config-version snapshot. The category column
widens for the longer canonical codes, and existing values move onto the new
taxonomy: UPI becomes INSTANT_BANK_PAYMENT with rail UPI, CARD becomes
PAYMENT_CARD. Every existing row gets the label it now reads as.

Payment instructions move onto the same categories (method_subtype holds the
rail), and sublet arrangements' accepted methods drop the bare "UPI".

Overpayments: records carry an excess amount/status, and each decision about
an excess (applied to another payment, or sent back) is an append-only
rental_payment_excess_allocations row.

Revision ID: 0051_global_payment_methods
Revises: 0050_admin_password_reset
Create Date: 2026-10-15 00:00:00.000000

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0051_global_payment_methods"
down_revision: Union[str, None] = "0050_admin_password_reset"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("rental_payment_records", "rental_payment_returns")
_LABELS = {
    ("BANK_TRANSFER", ""): "Bank Transfer",
    ("INSTANT_BANK_PAYMENT", "UPI"): "Instant Bank Payment / Pay by Bank · UPI",
    ("PAYMENT_CARD", ""): "Payment Card",
    ("CASH", ""): "Cash",
    ("OTHER", ""): "Other",
}


def _rewrite_accepted_methods(mapping: dict[str, str]) -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, accepted_methods FROM sublet_payment_arrangements")).fetchall()
    for row_id, raw in rows:
        methods = json.loads(raw) if isinstance(raw, str) else (raw or [])
        rewritten = list(dict.fromkeys(mapping.get(m, m) for m in methods))
        if rewritten != methods:
            bind.execute(
                sa.text("UPDATE sublet_payment_arrangements SET accepted_methods = :m WHERE id = :id").bindparams(
                    sa.bindparam("m", type_=sa.JSON), id=row_id, m=rewritten,
                )
            )


def upgrade() -> None:
    op.add_column("rental_payment_records", sa.Column("note", sa.String(500), nullable=False, server_default=""))
    op.add_column(
        "rental_payment_records", sa.Column("excess_amount", sa.Numeric(12, 2), nullable=False, server_default="0")
    )
    op.add_column("rental_payment_records", sa.Column("excess_status", sa.String(20), nullable=False, server_default=""))
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column("payment_method_category", existing_type=sa.String(20), type_=sa.String(32))
            batch.add_column(sa.Column("payment_method_subtype", sa.String(40), nullable=False, server_default=""))
            batch.add_column(sa.Column("payment_method_description", sa.String(200), nullable=False, server_default=""))
            batch.add_column(sa.Column("payment_method_label", sa.String(300), nullable=False, server_default=""))
            batch.add_column(
                sa.Column("payment_method_config_version", sa.String(20), nullable=False, server_default="")
            )
        op.execute(
            f"UPDATE {table} SET payment_method_category = 'INSTANT_BANK_PAYMENT', payment_method_subtype = 'UPI' "
            "WHERE payment_method_category = 'UPI'"
        )
        op.execute(f"UPDATE {table} SET payment_method_category = 'PAYMENT_CARD' WHERE payment_method_category = 'CARD'")
        for (category, subtype), label in _LABELS.items():
            op.execute(
                sa.text(
                    f"UPDATE {table} SET payment_method_label = :label "
                    "WHERE payment_method_category = :category AND payment_method_subtype = :subtype"
                ).bindparams(label=label, category=category, subtype=subtype)
            )

    with op.batch_alter_table("rental_payment_instructions") as batch:
        batch.alter_column("method", existing_type=sa.String(20), type_=sa.String(32))
        batch.add_column(sa.Column("method_subtype", sa.String(40), nullable=False, server_default=""))
    op.execute(
        "UPDATE rental_payment_instructions SET method = 'INSTANT_BANK_PAYMENT', method_subtype = 'UPI' "
        "WHERE method = 'UPI'"
    )
    _rewrite_accepted_methods({"UPI": "INSTANT_BANK_PAYMENT"})

    op.create_table(
        "rental_payment_excess_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "record_id", sa.Integer(), sa.ForeignKey("rental_payment_records.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "target_obligation_id", sa.Integer(),
            sa.ForeignKey("rental_payment_obligations.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "target_record_id", sa.Integer(), sa.ForeignKey("rental_payment_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("returned_date", sa.Date(), nullable=True),
        sa.Column("payment_method_category", sa.String(32), nullable=False, server_default=""),
        sa.Column("payment_method_subtype", sa.String(40), nullable=False, server_default=""),
        sa.Column("payment_method_label", sa.String(300), nullable=False, server_default=""),
        sa.Column("external_reference", sa.String(255), nullable=False, server_default=""),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        sa.Column("actor_party_id", sa.Integer(), sa.ForeignKey("parties.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_rental_payment_excess_allocations_record_id", "rental_payment_excess_allocations", ["record_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_rental_payment_excess_allocations_record_id", table_name="rental_payment_excess_allocations")
    op.drop_table("rental_payment_excess_allocations")

    _rewrite_accepted_methods({"INSTANT_BANK_PAYMENT": "UPI"})
    op.execute(
        "UPDATE rental_payment_instructions SET method = 'UPI' "
        "WHERE method = 'INSTANT_BANK_PAYMENT' AND method_subtype = 'UPI'"
    )
    # Instructions in categories the old vocabulary lacks can't be kept.
    op.execute(
        "UPDATE rental_payment_instructions SET status = 'SUPERSEDED' "
        "WHERE method NOT IN ('BANK_TRANSFER', 'UPI', 'CASH', 'CARD', 'OTHER')"
    )
    op.execute(
        "UPDATE rental_payment_instructions SET method = 'OTHER' "
        "WHERE method NOT IN ('BANK_TRANSFER', 'UPI', 'CASH', 'CARD', 'OTHER')"
    )
    with op.batch_alter_table("rental_payment_instructions") as batch:
        batch.drop_column("method_subtype")
        batch.alter_column("method", existing_type=sa.String(32), type_=sa.String(20))

    for table in _TABLES:
        op.execute(
            f"UPDATE {table} SET payment_method_category = 'UPI' "
            "WHERE payment_method_category = 'INSTANT_BANK_PAYMENT' AND payment_method_subtype = 'UPI'"
        )
        op.execute(f"UPDATE {table} SET payment_method_category = 'CARD' WHERE payment_method_category = 'PAYMENT_CARD'")
        # Anything else new doesn't fit the old vocabulary or column width.
        op.execute(
            f"UPDATE {table} SET payment_method_category = 'OTHER' "
            "WHERE payment_method_category NOT IN ('BANK_TRANSFER', 'UPI', 'CASH', 'CARD', 'OTHER')"
        )
        with op.batch_alter_table(table) as batch:
            batch.drop_column("payment_method_config_version")
            batch.drop_column("payment_method_label")
            batch.drop_column("payment_method_description")
            batch.drop_column("payment_method_subtype")
            batch.alter_column("payment_method_category", existing_type=sa.String(32), type_=sa.String(20))
    with op.batch_alter_table("rental_payment_records") as batch:
        batch.drop_column("excess_status")
        batch.drop_column("excess_amount")
        batch.drop_column("note")

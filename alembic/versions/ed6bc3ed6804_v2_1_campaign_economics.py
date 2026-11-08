"""v2.1 campaign economics

Revision ID: ed6bc3ed6804
Revises: a162cab389d6
Create Date: 2026-11-09

Adds the Campaign Hunter domain (campaigns, campaign_requirements,
campaign_assessments, campaign_publications), the Phase 8 content-debt
intake columns on opportunities, and the revenue linkage for campaign
payouts: a nullable ``campaign_id`` on revenue_records and the
``CAMPAIGN_PAYOUT`` member of the revenue_kind enum.

PostgreSQL enum note: ``CAMPAIGN_PAYOUT`` is added with ALTER TYPE ...
ADD VALUE and is never used inside this migration, which PostgreSQL
requires when the migration runs in a transaction. The downgrade
cannot remove an enum value on PostgreSQL, so it leaves the (then
unused) value in place — documented behaviour, matching how enum
extensions are normally downgraded. On SQLite the enum is a plain
VARCHAR, so no type work is needed on either direction.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "ed6bc3ed6804"
down_revision: Union[str, None] = "a162cab389d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _portable_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name).with_variant(
        postgresql.ENUM(*values, name=name, create_type=False), "postgresql"
    )


CAMPAIGN_SOURCE_KIND = _portable_enum("campaign_source_kind", "MANUAL", "SEED_IMPORT")
CAMPAIGN_RIGHTS_BASIS = _portable_enum(
    "campaign_rights_basis", "AUTHORIZED", "LICENSED", "PUBLIC_DOMAIN", "UNKNOWN"
)
CAMPAIGN_STATUS = _portable_enum(
    "campaign_status",
    "CANDIDATE",
    "QUALIFIED",
    "ACTIVE",
    "SUBMITTED",
    "COMPLETED",
    "REJECTED",
    "EXPIRED",
)
CAMPAIGN_ASSESSMENT_STATE = _portable_enum(
    "campaign_assessment_state", "ASSESSABLE", "UNASSESSABLE"
)

_POSTGRES_ENUMS = (
    ("campaign_source_kind", ("MANUAL", "SEED_IMPORT")),
    ("campaign_rights_basis", ("AUTHORIZED", "LICENSED", "PUBLIC_DOMAIN", "UNKNOWN")),
    (
        "campaign_status",
        ("CANDIDATE", "QUALIFIED", "ACTIVE", "SUBMITTED", "COMPLETED", "REJECTED", "EXPIRED"),
    ),
    ("campaign_assessment_state", ("ASSESSABLE", "UNASSESSABLE")),
)

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, values in _POSTGRES_ENUMS:
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)
        op.execute("ALTER TYPE revenue_kind ADD VALUE IF NOT EXISTS 'CAMPAIGN_PAYOUT'")

    op.create_table(
        "campaigns",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("platform", sa.String(length=80), nullable=False),
        sa.Column("sponsor_label", sa.String(length=300), nullable=True),
        sa.Column("source_kind", CAMPAIGN_SOURCE_KIND, nullable=False),
        sa.Column("source_reference", sa.String(length=1000), nullable=True),
        sa.Column("cpm_rate_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("max_payout_minor", sa.BigInteger(), nullable=True),
        sa.Column("budget_minor", sa.BigInteger(), nullable=True),
        sa.Column("minimum_views", sa.BigInteger(), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_restrictions", sa.Text(), nullable=True),
        sa.Column("account_requirements", sa.Text(), nullable=True),
        sa.Column("rights_basis", CAMPAIGN_RIGHTS_BASIS, nullable=False),
        sa.Column("submission_process", sa.Text(), nullable=True),
        sa.Column("status", CAMPAIGN_STATUS, nullable=False),
        sa.Column("expected_views", sa.BigInteger(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("crm_account_id", sa.Uuid(), nullable=True),
        sa.Column("qualification_override_reason", sa.String(length=1000), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "budget_minor IS NULL OR budget_minor >= 0",
            name=op.f("ck_campaigns_budget_nonnegative"),
        ),
        sa.CheckConstraint(
            "cpm_rate_minor IS NULL OR cpm_rate_minor >= 0",
            name=op.f("ck_campaigns_cpm_nonnegative"),
        ),
        sa.CheckConstraint(
            "expected_views IS NULL OR expected_views >= 0",
            name=op.f("ck_campaigns_expected_views_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_payout_minor IS NULL OR max_payout_minor >= 0",
            name=op.f("ck_campaigns_max_payout_nonnegative"),
        ),
        sa.CheckConstraint(
            "minimum_views IS NULL OR minimum_views >= 0",
            name=op.f("ck_campaigns_minimum_views_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_campaigns_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["crm_account_id"],
            ["crm_accounts.id"],
            name=op.f("fk_campaigns_crm_account_id_crm_accounts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_campaigns_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_campaign_tenant_idempotency_key"
        ),
    )
    op.create_index("ix_campaigns_tenant_platform", "campaigns", ["tenant_id", "platform"])
    op.create_index("ix_campaigns_tenant_status", "campaigns", ["tenant_id", "status"])

    op.create_table(
        "campaign_requirements",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_campaign_requirements_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_campaign_requirements_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_requirements")),
    )
    op.create_index(
        "ix_campaign_requirements_campaign", "campaign_requirements", ["campaign_id"]
    )

    op.create_table(
        "campaign_assessments",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("state", CAMPAIGN_ASSESSMENT_STATE, nullable=False),
        sa.Column("missing_inputs", JSON_DOCUMENT, nullable=False),
        sa.Column("expected_views", sa.BigInteger(), nullable=True),
        sa.Column("cpm_rate_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("gross_revenue_minor", sa.BigInteger(), nullable=True),
        sa.Column("capped_by_max_payout", sa.Boolean(), nullable=False),
        sa.Column("editing_cost_minor", sa.BigInteger(), nullable=False),
        sa.Column("model_cost_minor", sa.BigInteger(), nullable=False),
        sa.Column("distribution_cost_minor", sa.BigInteger(), nullable=False),
        sa.Column("risk_percent", sa.Integer(), nullable=False),
        sa.Column("risk_adjustment_minor", sa.BigInteger(), nullable=True),
        sa.Column("expected_profit_minor", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "editing_cost_minor >= 0 AND model_cost_minor >= 0 AND distribution_cost_minor >= 0",
            name=op.f("ck_campaign_assessments_assessment_costs_nonnegative"),
        ),
        sa.CheckConstraint(
            "risk_percent >= 0 AND risk_percent <= 100",
            name=op.f("ck_campaign_assessments_risk_percent_range"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_campaign_assessments_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_campaign_assessments_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_campaign_assessments_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_assessments")),
    )
    op.create_index(
        "ix_campaign_assessments_campaign",
        "campaign_assessments",
        ["tenant_id", "campaign_id", "created_at"],
    )

    op.create_table(
        "campaign_publications",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_campaign_publications_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_campaign_publications_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publications.id"],
            name=op.f("fk_campaign_publications_publication_id_publications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_campaign_publications_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_publications")),
        sa.UniqueConstraint(
            "campaign_id", "publication_id", name="uq_campaign_publication_pair"
        ),
    )
    op.create_index(
        "ix_campaign_publications_publication", "campaign_publications", ["publication_id"]
    )
    op.create_index(
        "ix_campaign_publications_tenant_campaign",
        "campaign_publications",
        ["tenant_id", "campaign_id"],
    )

    # revenue_records: campaign payout linkage.
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("revenue_records") as batch_op:
            # SQLite stores the enum as VARCHAR sized by its longest
            # member; widen it for CAMPAIGN_PAYOUT so the migrated
            # schema matches the models exactly.
            batch_op.alter_column(
                "kind",
                existing_type=sa.Enum(
                    "QUOTE",
                    "CONTRACT",
                    "INVOICE",
                    "PAYMENT",
                    "REFUND",
                    "RECURRING",
                    name="revenue_kind",
                ),
                type_=sa.Enum(
                    "QUOTE",
                    "CONTRACT",
                    "INVOICE",
                    "PAYMENT",
                    "REFUND",
                    "RECURRING",
                    "CAMPAIGN_PAYOUT",
                    name="revenue_kind",
                ),
                existing_nullable=False,
            )
            batch_op.add_column(sa.Column("campaign_id", sa.Uuid(), nullable=True))
            batch_op.create_foreign_key(
                op.f("fk_revenue_records_campaign_id_campaigns"),
                "campaigns",
                ["campaign_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        op.add_column(
            "revenue_records", sa.Column("campaign_id", sa.Uuid(), nullable=True)
        )
        op.create_foreign_key(
            op.f("fk_revenue_records_campaign_id_campaigns"),
            "revenue_records",
            "campaigns",
            ["campaign_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_revenue_records_tenant_campaign",
        "revenue_records",
        ["tenant_id", "campaign_id"],
    )

    # opportunities: v2.1 content-debt intake columns.
    op.add_column(
        "opportunities", sa.Column("library_episode_count", sa.Integer(), nullable=True)
    )
    op.add_column(
        "opportunities", sa.Column("library_long_form_hours", sa.Float(), nullable=True)
    )
    op.add_column(
        "opportunities", sa.Column("existing_shorts_count", sa.Integer(), nullable=True)
    )
    op.add_column(
        "opportunities",
        sa.Column("moments_per_episode_assumption", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("opportunities", "moments_per_episode_assumption")
    op.drop_column("opportunities", "existing_shorts_count")
    op.drop_column("opportunities", "library_long_form_hours")
    op.drop_column("opportunities", "library_episode_count")

    op.drop_index("ix_revenue_records_tenant_campaign", table_name="revenue_records")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("revenue_records") as batch_op:
            batch_op.drop_constraint(
                op.f("fk_revenue_records_campaign_id_campaigns"), type_="foreignkey"
            )
            batch_op.drop_column("campaign_id")
            batch_op.alter_column(
                "kind",
                existing_type=sa.Enum(
                    "QUOTE",
                    "CONTRACT",
                    "INVOICE",
                    "PAYMENT",
                    "REFUND",
                    "RECURRING",
                    "CAMPAIGN_PAYOUT",
                    name="revenue_kind",
                ),
                type_=sa.Enum(
                    "QUOTE",
                    "CONTRACT",
                    "INVOICE",
                    "PAYMENT",
                    "REFUND",
                    "RECURRING",
                    name="revenue_kind",
                ),
                existing_nullable=False,
            )
    else:
        op.drop_constraint(
            op.f("fk_revenue_records_campaign_id_campaigns"),
            "revenue_records",
            type_="foreignkey",
        )
        op.drop_column("revenue_records", "campaign_id")
    # PostgreSQL: the CAMPAIGN_PAYOUT enum value is intentionally left in
    # place (enum values cannot be removed); see the module docstring.

    op.drop_index(
        "ix_campaign_publications_tenant_campaign", table_name="campaign_publications"
    )
    op.drop_index("ix_campaign_publications_publication", table_name="campaign_publications")
    op.drop_table("campaign_publications")
    op.drop_index("ix_campaign_assessments_campaign", table_name="campaign_assessments")
    op.drop_table("campaign_assessments")
    op.drop_index("ix_campaign_requirements_campaign", table_name="campaign_requirements")
    op.drop_table("campaign_requirements")
    op.drop_index("ix_campaigns_tenant_status", table_name="campaigns")
    op.drop_index("ix_campaigns_tenant_platform", table_name="campaigns")
    op.drop_table("campaigns")

    if bind.dialect.name == "postgresql":
        for name, _values in reversed(_POSTGRES_ENUMS):
            postgresql.ENUM(name=name).drop(bind, checkfirst=True)

"""phase 9 sales crm onboarding revenue

Revision ID: e37c610043af
Revises: 0b728736cdd4
Create Date: 2026-11-08 16:47:01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e37c610043af"
down_revision: Union[str, None] = "0b728736cdd4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _portable_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name).with_variant(
        postgresql.ENUM(*values, name=name, create_type=False), "postgresql"
    )


# Types introduced by this migration.
CRM_STAGE = _portable_enum(
    "crm_stage",
    "PROSPECT",
    "QUALIFIED_LEAD",
    "CONVERSATION",
    "DISCOVERY_CALL",
    "PROPOSAL",
    "CONTRACT_REVIEW",
    "CLIENT",
    "CAMPAIGN",
    "DELIVERABLE",
    "INVOICED",
    "RETAINED",
    "REJECTED",
    "LOST",
)
CRM_TASK_STATE = _portable_enum("crm_task_state", "OPEN", "DONE", "CANCELLED")
SALES_ASSET_KIND = _portable_enum(
    "sales_asset_kind",
    "OUTREACH_MESSAGE",
    "FOLLOW_UP",
    "PROPOSAL_OUTLINE",
    "DISCOVERY_AGENDA",
    "ONBOARDING_CHECKLIST",
)
SALES_ASSET_STATE = _portable_enum(
    "sales_asset_state", "DRAFT", "IN_REVIEW", "APPROVED", "REJECTED", "ARCHIVED"
)
REVENUE_KIND = _portable_enum(
    "revenue_kind", "QUOTE", "CONTRACT", "INVOICE", "PAYMENT", "REFUND", "RECURRING"
)
REVENUE_STATE = _portable_enum("revenue_state", "DRAFT", "ISSUED", "PAID", "VOID", "OVERDUE")
ONBOARDING_STATE = _portable_enum(
    "onboarding_state", "NOT_STARTED", "IN_PROGRESS", "BLOCKED", "COMPLETED"
)

_POSTGRES_ENUMS = (
    (
        "crm_stage",
        (
            "PROSPECT",
            "QUALIFIED_LEAD",
            "CONVERSATION",
            "DISCOVERY_CALL",
            "PROPOSAL",
            "CONTRACT_REVIEW",
            "CLIENT",
            "CAMPAIGN",
            "DELIVERABLE",
            "INVOICED",
            "RETAINED",
            "REJECTED",
            "LOST",
        ),
    ),
    ("crm_task_state", ("OPEN", "DONE", "CANCELLED")),
    (
        "sales_asset_kind",
        (
            "OUTREACH_MESSAGE",
            "FOLLOW_UP",
            "PROPOSAL_OUTLINE",
            "DISCOVERY_AGENDA",
            "ONBOARDING_CHECKLIST",
        ),
    ),
    (
        "sales_asset_state",
        ("DRAFT", "IN_REVIEW", "APPROVED", "REJECTED", "ARCHIVED"),
    ),
    (
        "revenue_kind",
        ("QUOTE", "CONTRACT", "INVOICE", "PAYMENT", "REFUND", "RECURRING"),
    ),
    ("revenue_state", ("DRAFT", "ISSUED", "PAID", "VOID", "OVERDUE")),
    (
        "onboarding_state",
        ("NOT_STARTED", "IN_PROGRESS", "BLOCKED", "COMPLETED"),
    ),
)

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, values in _POSTGRES_ENUMS:
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "crm_contacts",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=300), nullable=False),
        sa.Column("organisation", sa.String(length=300), nullable=True),
        sa.Column("role_title", sa.String(length=200), nullable=True),
        sa.Column("contact_route_label", sa.String(length=300), nullable=True),
        sa.Column("source_opportunity_id", sa.Uuid(), nullable=True),
        sa.Column("dedup_key", sa.String(length=600), nullable=False),
        sa.Column("possible_duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column("suppressed", sa.Boolean(), nullable=False),
        sa.Column("suppression_reason", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_crm_contacts_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["possible_duplicate_of_id"],
            ["crm_contacts.id"],
            name=op.f("fk_crm_contacts_possible_duplicate_of_id_crm_contacts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_crm_contacts_source_opportunity_id_opportunities"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_crm_contacts_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crm_contacts")),
    )
    op.create_index("ix_crm_contacts_tenant_dedup", "crm_contacts", ["tenant_id", "dedup_key"])
    op.create_index(
        "ix_crm_contacts_tenant_suppressed", "crm_contacts", ["tenant_id", "suppressed"]
    )

    op.create_table(
        "crm_accounts",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("stage", CRM_STAGE, nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("primary_contact_id", sa.Uuid(), nullable=True),
        sa.Column("source_opportunity_id", sa.Uuid(), nullable=True),
        sa.Column("source_handoff_id", sa.Uuid(), nullable=True),
        sa.Column("dedup_key", sa.String(length=600), nullable=False),
        sa.Column("possible_duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column("next_action", sa.String(length=500), nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes_summary", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_crm_accounts_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_crm_accounts_owner_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["possible_duplicate_of_id"],
            ["crm_accounts.id"],
            name=op.f("fk_crm_accounts_possible_duplicate_of_id_crm_accounts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["primary_contact_id"],
            ["crm_contacts.id"],
            name=op.f("fk_crm_accounts_primary_contact_id_crm_contacts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_handoff_id"],
            ["opportunity_handoffs.id"],
            name=op.f("fk_crm_accounts_source_handoff_id_opportunity_handoffs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_crm_accounts_source_opportunity_id_opportunities"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_crm_accounts_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crm_accounts")),
        sa.UniqueConstraint("source_handoff_id", name="uq_crm_account_source_handoff"),
        sa.UniqueConstraint(
            "tenant_id", "source_opportunity_id", name="uq_crm_account_source_opportunity"
        ),
    )
    op.create_index("ix_crm_accounts_tenant_dedup", "crm_accounts", ["tenant_id", "dedup_key"])
    op.create_index("ix_crm_accounts_tenant_stage", "crm_accounts", ["tenant_id", "stage"])

    op.create_table(
        "crm_pipeline_events",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("event_kind", sa.String(length=80), nullable=False),
        sa.Column("from_stage", CRM_STAGE, nullable=True),
        sa.Column("to_stage", CRM_STAGE, nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["crm_accounts.id"],
            name=op.f("fk_crm_pipeline_events_account_id_crm_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_crm_pipeline_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["crm_contacts.id"],
            name=op.f("fk_crm_pipeline_events_contact_id_crm_contacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_crm_pipeline_events_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crm_pipeline_events")),
    )
    op.create_index(
        "ix_crm_pipeline_events_tenant_account",
        "crm_pipeline_events",
        ["tenant_id", "account_id"],
    )
    op.create_index(
        "ix_crm_pipeline_events_tenant_contact",
        "crm_pipeline_events",
        ["tenant_id", "contact_id"],
    )

    op.create_table(
        "crm_tasks",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", CRM_TASK_STATE, nullable=False),
        sa.Column("assignee_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["crm_accounts.id"],
            name=op.f("fk_crm_tasks_account_id_crm_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_user_id"],
            ["users.id"],
            name=op.f("fk_crm_tasks_assignee_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_crm_tasks_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_crm_tasks_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crm_tasks")),
    )
    op.create_index("ix_crm_tasks_tenant_account", "crm_tasks", ["tenant_id", "account_id"])
    op.create_index("ix_crm_tasks_tenant_status", "crm_tasks", ["tenant_id", "status"])

    op.create_table(
        "onboarding_records",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("checklist", JSON_DOCUMENT, nullable=False),
        sa.Column("milestones", JSON_DOCUMENT, nullable=False),
        sa.Column("status", ONBOARDING_STATE, nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["crm_accounts.id"],
            name=op.f("fk_onboarding_records_account_id_crm_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_onboarding_records_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_onboarding_records_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_onboarding_records")),
        sa.UniqueConstraint("account_id", name="uq_onboarding_record_account"),
    )
    op.create_index(
        "ix_onboarding_records_tenant_status",
        "onboarding_records",
        ["tenant_id", "status"],
    )

    op.create_table(
        "revenue_records",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("kind", REVENUE_KIND, nullable=False),
        sa.Column("amount_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", REVENUE_STATE, nullable=False),
        sa.Column("external_reference", sa.String(length=200), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_minor_units >= 0", name=op.f("ck_revenue_records_amount_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["crm_accounts.id"],
            name=op.f("fk_revenue_records_account_id_crm_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_revenue_records_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_revenue_records_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_revenue_records")),
    )
    op.create_index(
        "ix_revenue_records_tenant_account", "revenue_records", ["tenant_id", "account_id"]
    )
    op.create_index(
        "ix_revenue_records_tenant_kind_status",
        "revenue_records",
        ["tenant_id", "kind", "status"],
    )

    op.create_table(
        "sales_asset_drafts",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("kind", SALES_ASSET_KIND, nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("evidence", JSON_DOCUMENT, nullable=False),
        sa.Column("status", SALES_ASSET_STATE, nullable=False),
        sa.Column("template_version", sa.String(length=80), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["crm_accounts.id"],
            name=op.f("fk_sales_asset_drafts_account_id_crm_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["crm_contacts.id"],
            name=op.f("fk_sales_asset_drafts_contact_id_crm_contacts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name=op.f("fk_sales_asset_drafts_reviewed_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_sales_asset_drafts_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales_asset_drafts")),
    )
    op.create_index(
        "ix_sales_asset_drafts_tenant_account",
        "sales_asset_drafts",
        ["tenant_id", "account_id"],
    )
    op.create_index(
        "ix_sales_asset_drafts_tenant_status",
        "sales_asset_drafts",
        ["tenant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_sales_asset_drafts_tenant_status", table_name="sales_asset_drafts")
    op.drop_index("ix_sales_asset_drafts_tenant_account", table_name="sales_asset_drafts")
    op.drop_table("sales_asset_drafts")
    op.drop_index("ix_revenue_records_tenant_kind_status", table_name="revenue_records")
    op.drop_index("ix_revenue_records_tenant_account", table_name="revenue_records")
    op.drop_table("revenue_records")
    op.drop_index("ix_onboarding_records_tenant_status", table_name="onboarding_records")
    op.drop_table("onboarding_records")
    op.drop_index("ix_crm_tasks_tenant_status", table_name="crm_tasks")
    op.drop_index("ix_crm_tasks_tenant_account", table_name="crm_tasks")
    op.drop_table("crm_tasks")
    op.drop_index("ix_crm_pipeline_events_tenant_contact", table_name="crm_pipeline_events")
    op.drop_index("ix_crm_pipeline_events_tenant_account", table_name="crm_pipeline_events")
    op.drop_table("crm_pipeline_events")
    op.drop_index("ix_crm_accounts_tenant_stage", table_name="crm_accounts")
    op.drop_index("ix_crm_accounts_tenant_dedup", table_name="crm_accounts")
    op.drop_table("crm_accounts")
    op.drop_index("ix_crm_contacts_tenant_suppressed", table_name="crm_contacts")
    op.drop_index("ix_crm_contacts_tenant_dedup", table_name="crm_contacts")
    op.drop_table("crm_contacts")

    if op.get_bind().dialect.name == "postgresql":
        for name, _values in reversed(_POSTGRES_ENUMS):
            postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)

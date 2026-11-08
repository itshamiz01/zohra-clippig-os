"""phase 8 opportunity hunter commercial fit

Revision ID: 0b728736cdd4
Revises: c7d40e02a0b4
Create Date: 2026-11-08 16:16:56
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0b728736cdd4"
down_revision: Union[str, None] = "c7d40e02a0b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _portable_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name).with_variant(
        postgresql.ENUM(*values, name=name, create_type=False), "postgresql"
    )


# Types created by earlier migrations; referenced here without recreating.
PROVIDER_MODE = _portable_enum(
    "provider_mode", "REAL_TESTED", "MOCKED", "DISABLED", "BLOCKED", "UNVERIFIED"
)
# Types introduced by this migration.
OPPORTUNITY_SOURCE_KIND = _portable_enum(
    "opportunity_source_kind", "SEED_LIST", "RSS_FEED", "PUBLIC_SITE", "SEARCH_PROVIDER"
)
OPPORTUNITY_SOURCE_STATUS = _portable_enum("opportunity_source_status", "ENABLED", "DISABLED")
OPPORTUNITY_SYNC_STATE = _portable_enum(
    "opportunity_sync_state",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "PARTIALLY_SUCCEEDED",
    "FAILED",
)
OPPORTUNITY_STATE = _portable_enum(
    "opportunity_state", "NEW", "REVIEWING", "SHORTLISTED", "REJECTED", "SUPPRESSED"
)
SUPPRESSION_MATCH_TYPE = _portable_enum(
    "suppression_match_type", "CREATOR_HANDLE", "DOMAIN", "KEYWORD"
)
OPPORTUNITY_HANDOFF_STATE = _portable_enum("opportunity_handoff_state", "DRAFT", "READY")

_POSTGRES_ENUMS = (
    (
        "opportunity_source_kind",
        ("SEED_LIST", "RSS_FEED", "PUBLIC_SITE", "SEARCH_PROVIDER"),
    ),
    ("opportunity_source_status", ("ENABLED", "DISABLED")),
    (
        "opportunity_sync_state",
        ("QUEUED", "RUNNING", "SUCCEEDED", "PARTIALLY_SUCCEEDED", "FAILED"),
    ),
    (
        "opportunity_state",
        ("NEW", "REVIEWING", "SHORTLISTED", "REJECTED", "SUPPRESSED"),
    ),
    ("suppression_match_type", ("CREATOR_HANDLE", "DOMAIN", "KEYWORD")),
    ("opportunity_handoff_state", ("DRAFT", "READY")),
)

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, values in _POSTGRES_ENUMS:
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "opportunity_sources",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", OPPORTUNITY_SOURCE_KIND, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("config", JSON_DOCUMENT, nullable=False),
        sa.Column("status", OPPORTUNITY_SOURCE_STATUS, nullable=False),
        sa.Column("adapter_mode", PROVIDER_MODE, nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_opportunity_sources_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_opportunity_sources_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunity_sources")),
    )
    op.create_index(
        "ix_opportunity_sources_tenant_kind",
        "opportunity_sources",
        ["tenant_id", "kind"],
    )

    op.create_table(
        "opportunity_sync_runs",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("background_job_id", sa.Uuid(), nullable=True),
        sa.Column("state", OPPORTUNITY_SYNC_STATE, nullable=False),
        sa.Column("adapter_mode", PROVIDER_MODE, nullable=False),
        sa.Column("records_received", sa.Integer(), nullable=False),
        sa.Column("records_created", sa.Integer(), nullable=False),
        sa.Column("records_merged", sa.Integer(), nullable=False),
        sa.Column("duplicates_flagged", sa.Integer(), nullable=False),
        sa.Column("suppressed_skipped", sa.Integer(), nullable=False),
        sa.Column("failure_category", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "records_received >= 0 AND records_created >= 0 AND records_merged >= 0 "
            "AND duplicates_flagged >= 0 AND suppressed_skipped >= 0",
            name=op.f("ck_opportunity_sync_runs_ck_opportunity_sync_runs_counts_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["background_job_id"],
            ["background_jobs.id"],
            ondelete="SET NULL",
            name=op.f("fk_opportunity_sync_runs_background_job_id_background_jobs"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name=op.f("fk_opportunity_sync_runs_requested_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["opportunity_sources.id"],
            ondelete="CASCADE",
            name=op.f("fk_opportunity_sync_runs_source_id_opportunity_sources"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_opportunity_sync_runs_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunity_sync_runs")),
        sa.UniqueConstraint(
            "background_job_id", name=op.f("uq_opportunity_sync_runs_background_job_id")
        ),
    )
    op.create_index(
        "ix_opportunity_sync_runs_tenant_state",
        "opportunity_sync_runs",
        ["tenant_id", "state"],
    )

    op.create_table(
        "opportunities",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("sync_run_id", sa.Uuid(), nullable=True),
        sa.Column("external_key", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("creator_name", sa.String(length=300), nullable=True),
        sa.Column("creator_handle", sa.String(length=200), nullable=True),
        sa.Column("canonical_url", sa.String(length=1000), nullable=True),
        sa.Column("platform", sa.String(length=80), nullable=True),
        sa.Column("contact_route_label", sa.String(length=300), nullable=True),
        sa.Column("evidence", JSON_DOCUMENT, nullable=False),
        sa.Column("niche", sa.String(length=120), nullable=True),
        sa.Column("language", sa.String(length=35), nullable=True),
        sa.Column("audience_band", sa.String(length=40), nullable=True),
        sa.Column("posting_cadence", sa.String(length=40), nullable=True),
        sa.Column("dedup_key", sa.String(length=600), nullable=False),
        sa.Column("possible_duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column("state", OPPORTUNITY_STATE, nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("score_version", sa.String(length=80), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)",
            name=op.f("ck_opportunities_ck_opportunities_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["possible_duplicate_of_id"],
            ["opportunities.id"],
            ondelete="SET NULL",
            name=op.f("fk_opportunities_possible_duplicate_of_id_opportunities"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["opportunity_sources.id"],
            ondelete="CASCADE",
            name=op.f("fk_opportunities_source_id_opportunity_sources"),
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"],
            ["opportunity_sync_runs.id"],
            ondelete="SET NULL",
            name=op.f("fk_opportunities_sync_run_id_opportunity_sync_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_opportunities_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunities")),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "external_key",
            name="uq_opportunity_source_external_key",
        ),
    )
    op.create_index(
        "ix_opportunities_tenant_dedup", "opportunities", ["tenant_id", "dedup_key"]
    )
    op.create_index(
        "ix_opportunities_tenant_state", "opportunities", ["tenant_id", "state"]
    )

    op.create_table(
        "opportunity_score_components",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("meaning", sa.String(length=500), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("weighted_score", sa.Float(), nullable=False),
        sa.Column("applicable", sa.Boolean(), nullable=False),
        sa.Column("evidence", JSON_DOCUMENT, nullable=False),
        sa.Column("missing_behavior", sa.String(length=160), nullable=False),
        sa.Column("scoring_version", sa.String(length=80), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)",
            name=op.f("ck_opportunity_score_components_score_range"),
        ),
        sa.CheckConstraint(
            "weight > 0", name=op.f("ck_opportunity_score_components_weight_positive")
        ),
        sa.CheckConstraint(
            "weighted_score >= 0",
            name=op.f("ck_opportunity_score_components_weighted_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            ondelete="CASCADE",
            name=op.f("fk_opportunity_score_components_opportunity_id_opportunities"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_opportunity_score_components_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunity_score_components")),
        sa.UniqueConstraint(
            "opportunity_id", "key", name="uq_opportunity_score_component_key"
        ),
    )
    op.create_index(
        "ix_opportunity_score_tenant_opportunity",
        "opportunity_score_components",
        ["tenant_id", "opportunity_id"],
    )

    op.create_table(
        "opportunity_suppressions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("match_type", SUPPRESSION_MATCH_TYPE, nullable=False),
        sa.Column("match_value", sa.String(length=500), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_opportunity_suppressions_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_opportunity_suppressions_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunity_suppressions")),
        sa.UniqueConstraint(
            "tenant_id",
            "match_type",
            "match_value",
            name="uq_opportunity_suppression_match",
        ),
    )
    op.create_index(
        "ix_opportunity_suppressions_tenant_type",
        "opportunity_suppressions",
        ["tenant_id", "match_type"],
    )

    op.create_table(
        "opportunity_handoffs",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("payload", JSON_DOCUMENT, nullable=False),
        sa.Column("payload_version", sa.String(length=80), nullable=False),
        sa.Column("state", OPPORTUNITY_HANDOFF_STATE, nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_opportunity_handoffs_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            ondelete="CASCADE",
            name=op.f("fk_opportunity_handoffs_opportunity_id_opportunities"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_opportunity_handoffs_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunity_handoffs")),
        sa.UniqueConstraint("opportunity_id", name="uq_opportunity_handoff_opportunity"),
    )
    op.create_index(
        "ix_opportunity_handoffs_tenant_state",
        "opportunity_handoffs",
        ["tenant_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_opportunity_handoffs_tenant_state", table_name="opportunity_handoffs"
    )
    op.drop_table("opportunity_handoffs")
    op.drop_index(
        "ix_opportunity_suppressions_tenant_type", table_name="opportunity_suppressions"
    )
    op.drop_table("opportunity_suppressions")
    op.drop_index(
        "ix_opportunity_score_tenant_opportunity",
        table_name="opportunity_score_components",
    )
    op.drop_table("opportunity_score_components")
    op.drop_index("ix_opportunities_tenant_state", table_name="opportunities")
    op.drop_index("ix_opportunities_tenant_dedup", table_name="opportunities")
    op.drop_table("opportunities")
    op.drop_index(
        "ix_opportunity_sync_runs_tenant_state", table_name="opportunity_sync_runs"
    )
    op.drop_table("opportunity_sync_runs")
    op.drop_index("ix_opportunity_sources_tenant_kind", table_name="opportunity_sources")
    op.drop_table("opportunity_sources")

    if op.get_bind().dialect.name == "postgresql":
        for name, _values in reversed(_POSTGRES_ENUMS):
            postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)

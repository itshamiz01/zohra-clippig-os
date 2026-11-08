"""phase 7 analytics experiments learning

Revision ID: c7d40e02a0b4
Revises: d4a9a9290b9a
Create Date: 2026-11-08 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c7d40e02a0b4"
down_revision: Union[str, None] = "d4a9a9290b9a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _portable_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name).with_variant(
        postgresql.ENUM(*values, name=name, create_type=False), "postgresql"
    )


# Types created by earlier migrations; referenced here without recreating.
PUBLISHING_PROVIDER = _portable_enum(
    "publishing_provider", "YOUTUBE", "META", "TIKTOK", "LINKEDIN"
)
PROVIDER_MODE = _portable_enum(
    "provider_mode", "REAL_TESTED", "MOCKED", "DISABLED", "BLOCKED", "UNVERIFIED"
)
# Types introduced by this migration.
ANALYTICS_COLLECTION_STATE = _portable_enum(
    "analytics_collection_state",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "PARTIALLY_SUCCEEDED",
    "FAILED",
)
METRIC_AVAILABILITY = _portable_enum(
    "metric_availability", "REPORTED", "MISSING", "UNAVAILABLE", "PERMISSION_DENIED", "STALE"
)
EXPERIMENT_STATE = _portable_enum(
    "experiment_state", "DRAFT", "RUNNING", "PAUSED", "COMPLETED", "CANCELLED"
)
RECOMMENDATION_KIND = _portable_enum(
    "recommendation_kind", "HOOK", "DURATION", "CAPTIONS", "POSTING_TIME", "FORMAT"
)
RECOMMENDATION_STATE = _portable_enum(
    "recommendation_state", "PROPOSED", "APPROVED", "REJECTED", "APPLIED"
)

_POSTGRES_ENUMS = (
    (
        "analytics_collection_state",
        ("QUEUED", "RUNNING", "SUCCEEDED", "PARTIALLY_SUCCEEDED", "FAILED"),
    ),
    (
        "metric_availability",
        ("REPORTED", "MISSING", "UNAVAILABLE", "PERMISSION_DENIED", "STALE"),
    ),
    ("experiment_state", ("DRAFT", "RUNNING", "PAUSED", "COMPLETED", "CANCELLED")),
    ("recommendation_kind", ("HOOK", "DURATION", "CAPTIONS", "POSTING_TIME", "FORMAT")),
    ("recommendation_state", ("PROPOSED", "APPROVED", "REJECTED", "APPLIED")),
)

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, values in _POSTGRES_ENUMS:
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "analytics_collection_runs",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("background_job_id", sa.Uuid(), nullable=True),
        sa.Column("provider", PUBLISHING_PROVIDER, nullable=True),
        sa.Column("state", ANALYTICS_COLLECTION_STATE, nullable=False),
        sa.Column("adapter_mode", PROVIDER_MODE, nullable=False),
        sa.Column("publications_considered", sa.Integer(), nullable=False),
        sa.Column("snapshots_written", sa.Integer(), nullable=False),
        sa.Column("metrics_reported", sa.Integer(), nullable=False),
        sa.Column("metrics_unavailable", sa.Integer(), nullable=False),
        sa.Column("failure_category", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "publications_considered >= 0 AND snapshots_written >= 0 "
            "AND metrics_reported >= 0 AND metrics_unavailable >= 0",
            name=op.f("ck_analytics_collection_runs_ck_analytics_runs_counts_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["background_job_id"],
            ["background_jobs.id"],
            ondelete="SET NULL",
            name=op.f("fk_analytics_collection_runs_background_job_id_background_jobs"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name=op.f("fk_analytics_collection_runs_requested_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_analytics_collection_runs_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analytics_collection_runs")),
        sa.UniqueConstraint(
            "background_job_id", name=op.f("uq_analytics_collection_runs_background_job_id")
        ),
    )
    op.create_index(
        "ix_analytics_runs_tenant_state",
        "analytics_collection_runs",
        ["tenant_id", "state"],
    )

    op.create_table(
        "analytics_metric_snapshots",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("collection_run_id", sa.Uuid(), nullable=True),
        sa.Column("provider", PUBLISHING_PROVIDER, nullable=False),
        sa.Column("provider_publication_id", sa.String(length=500), nullable=False),
        sa.Column("metric", sa.String(length=80), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("availability", METRIC_AVAILABILITY, nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("metric_definition_version", sa.String(length=80), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(availability IN ('REPORTED', 'STALE') AND value IS NOT NULL) "
            "OR (availability NOT IN ('REPORTED', 'STALE') AND value IS NULL)",
            name=op.f(
                "ck_analytics_metric_snapshots_ck_analytics_snapshot_value_matches_availability"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["analytics_collection_runs.id"],
            ondelete="SET NULL",
            name=op.f("fk_analytics_metric_snapshots_collection_run_id_analytics_collection_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publications.id"],
            ondelete="CASCADE",
            name=op.f("fk_analytics_metric_snapshots_publication_id_publications"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_analytics_metric_snapshots_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analytics_metric_snapshots")),
        sa.UniqueConstraint(
            "publication_id",
            "metric",
            "window_start",
            "window_end",
            "source",
            name="uq_analytics_snapshot_dedup",
        ),
    )
    op.create_index(
        "ix_analytics_snapshots_tenant_publication",
        "analytics_metric_snapshots",
        ["tenant_id", "publication_id"],
    )
    op.create_index(
        "ix_analytics_snapshots_metric_window",
        "analytics_metric_snapshots",
        ["metric", "window_end"],
    )

    op.create_table(
        "experiments",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("state", EXPERIMENT_STATE, nullable=False),
        sa.Column("eligibility", JSON_DOCUMENT, nullable=False),
        sa.Column("success_metric", sa.String(length=80), nullable=False),
        sa.Column("success_criteria", JSON_DOCUMENT, nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_experiments_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_experiments_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiments")),
    )
    op.create_index("ix_experiments_tenant_state", "experiments", ["tenant_id", "state"])

    op.create_table(
        "experiment_variants",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("publication_id", sa.Uuid(), nullable=True),
        sa.Column("config", JSON_DOCUMENT, nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "candidate_id IS NOT NULL OR publication_id IS NOT NULL",
            name=op.f("ck_experiment_variants_ck_experiment_variant_linked"),
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["clip_candidates.id"],
            ondelete="SET NULL",
            name=op.f("fk_experiment_variants_candidate_id_clip_candidates"),
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            ondelete="CASCADE",
            name=op.f("fk_experiment_variants_experiment_id_experiments"),
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publications.id"],
            ondelete="SET NULL",
            name=op.f("fk_experiment_variants_publication_id_publications"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_experiment_variants_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_variants")),
        sa.UniqueConstraint("experiment_id", "name", name="uq_experiment_variant_name"),
    )

    op.create_table(
        "recommendations",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", RECOMMENDATION_KIND, nullable=False),
        sa.Column("state", RECOMMENDATION_STATE, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", JSON_DOCUMENT, nullable=False),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name=op.f("fk_recommendations_reviewed_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_recommendations_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendations")),
    )
    op.create_index(
        "ix_recommendations_tenant_state", "recommendations", ["tenant_id", "state"]
    )


def downgrade() -> None:
    op.drop_index("ix_recommendations_tenant_state", table_name="recommendations")
    op.drop_table("recommendations")
    op.drop_table("experiment_variants")
    op.drop_index("ix_experiments_tenant_state", table_name="experiments")
    op.drop_table("experiments")
    op.drop_index("ix_analytics_snapshots_metric_window", table_name="analytics_metric_snapshots")
    op.drop_index(
        "ix_analytics_snapshots_tenant_publication", table_name="analytics_metric_snapshots"
    )
    op.drop_table("analytics_metric_snapshots")
    op.drop_index("ix_analytics_runs_tenant_state", table_name="analytics_collection_runs")
    op.drop_table("analytics_collection_runs")

    if op.get_bind().dialect.name == "postgresql":
        for name, _values in reversed(_POSTGRES_ENUMS):
            postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)

"""phase 6 gated youtube publishing

Revision ID: d4a9a9290b9a
Revises: 43b5bc652f2e
Create Date: 2026-11-01 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d4a9a9290b9a"
down_revision: Union[str, None] = "43b5bc652f2e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _portable_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name).with_variant(
        postgresql.ENUM(*values, name=name, create_type=False), "postgresql"
    )


PUBLISHING_PROVIDER = _portable_enum(
    "publishing_provider", "YOUTUBE", "META", "TIKTOK", "LINKEDIN"
)
OAUTH_CONNECTION_STATE = _portable_enum(
    "oauth_connection_state",
    "PENDING",
    "CONNECTED",
    "EXPIRED",
    "RECONNECT_REQUIRED",
    "REVOKED",
    "ERROR",
)
PUBLISH_STATE = _portable_enum(
    "publish_state",
    "QUEUED",
    "VALIDATING",
    "UPLOADING",
    "PROCESSING",
    "SUCCEEDED",
    "FAILED",
    "NEEDS_RECONCILIATION",
    "CANCELLED",
)
PUBLISH_VISIBILITY = _portable_enum(
    "publish_visibility", "PRIVATE", "UNLISTED", "PUBLIC"
)
PUBLISH_EVENT_KIND = _portable_enum(
    "publish_event_kind",
    "CREATED",
    "VALIDATION_STARTED",
    "GATE_VERIFIED",
    "TOKEN_REFRESHED",
    "UPLOAD_INITIATED",
    "UPLOAD_PROGRESS",
    "PROVIDER_PROCESSING",
    "RECONCILIATION",
    "SUCCEEDED",
    "FAILED",
    "CANCELLATION_REQUESTED",
    "CANCELLED",
    "RETRY_QUEUED",
)

_POSTGRES_ENUMS = (
    ("publishing_provider", ("YOUTUBE", "META", "TIKTOK", "LINKEDIN")),
    (
        "oauth_connection_state",
        ("PENDING", "CONNECTED", "EXPIRED", "RECONNECT_REQUIRED", "REVOKED", "ERROR"),
    ),
    (
        "publish_state",
        (
            "QUEUED",
            "VALIDATING",
            "UPLOADING",
            "PROCESSING",
            "SUCCEEDED",
            "FAILED",
            "NEEDS_RECONCILIATION",
            "CANCELLED",
        ),
    ),
    ("publish_visibility", ("PRIVATE", "UNLISTED", "PUBLIC")),
    (
        "publish_event_kind",
        (
            "CREATED",
            "VALIDATION_STARTED",
            "GATE_VERIFIED",
            "TOKEN_REFRESHED",
            "UPLOAD_INITIATED",
            "UPLOAD_PROGRESS",
            "PROVIDER_PROCESSING",
            "RECONCILIATION",
            "SUCCEEDED",
            "FAILED",
            "CANCELLATION_REQUESTED",
            "CANCELLED",
            "RETRY_QUEUED",
        ),
    ),
)

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, values in _POSTGRES_ENUMS:
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "publishing_oauth_states",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", PUBLISHING_PROVIDER, nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("pkce_verifier_envelope", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.String(length=1000), nullable=False),
        sa.Column("requested_scopes", JSON_DOCUMENT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("denial_reason", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_publishing_oauth_states_publishing_oauth_expiry_order")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE", name=op.f("fk_publishing_oauth_states_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name=op.f("fk_publishing_oauth_states_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publishing_oauth_states")),
        sa.UniqueConstraint("state_hash", name="uq_publishing_oauth_state_hash"),
    )
    op.create_index(
        "ix_publishing_oauth_tenant_user",
        "publishing_oauth_states",
        ["tenant_id", "user_id", "created_at"],
    )

    op.create_table(
        "publishing_connections",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", PUBLISHING_PROVIDER, nullable=False),
        sa.Column("state", OAUTH_CONNECTION_STATE, nullable=False),
        sa.Column("provider_destination_id", sa.String(length=300), nullable=False),
        sa.Column("destination_display_name", sa.String(length=500), nullable=False),
        sa.Column("granted_scopes", JSON_DOCUMENT, nullable=False),
        sa.Column("token_envelope", sa.Text(), nullable=False),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("has_refresh_token", sa.Boolean(), nullable=False),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconnect_required_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_category", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE", name=op.f("fk_publishing_connections_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT", name=op.f("fk_publishing_connections_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publishing_connections")),
        sa.UniqueConstraint(
            "tenant_id", "provider", "provider_destination_id", name="uq_publishing_connection_destination"
        ),
    )
    op.create_index(
        "ix_publishing_connection_tenant_state",
        "publishing_connections",
        ["tenant_id", "state"],
    )

    op.create_table(
        "publications",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("render_job_id", sa.Uuid(), nullable=False),
        sa.Column("render_output_id", sa.Uuid(), nullable=False),
        sa.Column("quality_evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("background_job_id", sa.Uuid(), nullable=False),
        sa.Column("public_approval_id", sa.Uuid(), nullable=True),
        sa.Column("acceptance_gate_verification_id", sa.Uuid(), nullable=False),
        sa.Column("upload_gate_verification_id", sa.Uuid(), nullable=True),
        sa.Column("provider", PUBLISHING_PROVIDER, nullable=False),
        sa.Column("provider_destination_id", sa.String(length=300), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("state", PUBLISH_STATE, nullable=False),
        sa.Column("visibility", PUBLISH_VISIBILITY, nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tags", JSON_DOCUMENT, nullable=False),
        sa.Column("made_for_kids", sa.Boolean(), nullable=False),
        sa.Column("category_id", sa.String(length=10), nullable=False),
        sa.Column("territory", sa.String(length=32), nullable=False),
        sa.Column("campaign_reference", sa.String(length=160), nullable=True),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("content_type", sa.String(length=160), nullable=False),
        sa.Column("content_length", sa.BigInteger(), nullable=False),
        sa.Column("committed_bytes", sa.BigInteger(), nullable=False),
        sa.Column("upload_session_envelope", sa.Text(), nullable=True),
        sa.Column("provider_publication_id", sa.String(length=500), nullable=True),
        sa.Column("provider_url", sa.String(length=2000), nullable=True),
        sa.Column("provider_processing_status", sa.String(length=100), nullable=True),
        sa.Column("sanitized_provider_response", JSON_DOCUMENT, nullable=False),
        sa.Column("analytics_handoff", JSON_DOCUMENT, nullable=True),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("uncertain_remote_result", sa.Boolean(), nullable=False),
        sa.Column("failure_category", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("manual_next_step", sa.String(length=1000), nullable=True),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upload_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("progress_percent >= 0 AND progress_percent <= 100", name=op.f("ck_publications_progress_range")),
        sa.CheckConstraint("committed_bytes >= 0", name=op.f("ck_publications_committed_bytes_nonnegative")),
        sa.CheckConstraint("content_length > 0", name=op.f("ck_publications_content_length_positive")),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_publications_attempt_count_nonnegative")),
        sa.CheckConstraint("max_attempts >= 1", name=op.f("ck_publications_max_attempts_positive")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT", name=op.f("fk_publications_tenant_id_tenants")),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT", name=op.f("fk_publications_requested_by_user_id_users")),
        sa.ForeignKeyConstraint(["connection_id"], ["publishing_connections.id"], ondelete="RESTRICT", name=op.f("fk_publications_connection_id_publishing_connections")),
        sa.ForeignKeyConstraint(["render_job_id"], ["render_jobs.id"], ondelete="RESTRICT", name=op.f("fk_publications_render_job_id_render_jobs")),
        sa.ForeignKeyConstraint(["render_output_id"], ["render_outputs.id"], ondelete="RESTRICT", name=op.f("fk_publications_render_output_id_render_outputs")),
        sa.ForeignKeyConstraint(["quality_evaluation_id"], ["quality_evaluations.id"], ondelete="RESTRICT", name=op.f("fk_publications_quality_evaluation_id_quality_evaluations")),
        sa.ForeignKeyConstraint(["background_job_id"], ["background_jobs.id"], ondelete="RESTRICT", name=op.f("fk_publications_background_job_id_background_jobs")),
        sa.ForeignKeyConstraint(["public_approval_id"], ["approval_requests.id"], ondelete="RESTRICT", name=op.f("fk_publications_public_approval_id_approval_requests")),
        sa.ForeignKeyConstraint(["acceptance_gate_verification_id"], ["gate_verifications.id"], ondelete="RESTRICT", name=op.f("fk_publications_acceptance_gate_verification_id_gate_verifications")),
        sa.ForeignKeyConstraint(["upload_gate_verification_id"], ["gate_verifications.id"], ondelete="RESTRICT", name=op.f("fk_publications_upload_gate_verification_id_gate_verifications")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publications")),
        sa.UniqueConstraint("background_job_id", name="uq_publication_background_job"),
        sa.UniqueConstraint("tenant_id", "provider", "idempotency_key", name="uq_publication_idempotency"),
    )
    op.create_index("ix_publications_tenant_state_created", "publications", ["tenant_id", "state", "created_at"])
    op.create_index("ix_publications_provider_remote", "publications", ["provider", "provider_publication_id"])

    op.create_table(
        "publication_events",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", PUBLISH_EVENT_KIND, nullable=False),
        sa.Column("from_state", PUBLISH_STATE, nullable=True),
        sa.Column("to_state", PUBLISH_STATE, nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("event_data", JSON_DOCUMENT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_publication_events_sequence_positive")),
        sa.CheckConstraint("progress_percent >= 0 AND progress_percent <= 100", name=op.f("ck_publication_events_progress_range")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT", name=op.f("fk_publication_events_tenant_id_tenants")),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE", name=op.f("fk_publication_events_publication_id_publications")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publication_events")),
        sa.UniqueConstraint("publication_id", "sequence", name="uq_publication_event_sequence"),
    )
    op.create_index("ix_publication_events_tenant_publication", "publication_events", ["tenant_id", "publication_id"])


def downgrade() -> None:
    op.drop_index("ix_publication_events_tenant_publication", table_name="publication_events")
    op.drop_table("publication_events")
    op.drop_index("ix_publications_provider_remote", table_name="publications")
    op.drop_index("ix_publications_tenant_state_created", table_name="publications")
    op.drop_table("publications")
    op.drop_index("ix_publishing_connection_tenant_state", table_name="publishing_connections")
    op.drop_table("publishing_connections")
    op.drop_index("ix_publishing_oauth_tenant_user", table_name="publishing_oauth_states")
    op.drop_table("publishing_oauth_states")

    if op.get_bind().dialect.name == "postgresql":
        for name, _values in reversed(_POSTGRES_ENUMS):
            postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)

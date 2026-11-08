"""phase 10 executive orchestration

Revision ID: a162cab389d6
Revises: e37c610043af
Create Date: 2026-11-08 17:16:32
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a162cab389d6"
down_revision: Union[str, None] = "e37c610043af"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _portable_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name).with_variant(
        postgresql.ENUM(*values, name=name, create_type=False), "postgresql"
    )


# Types introduced by this migration.
AGENT_ROLE = _portable_enum(
    "agent_role", "RESEARCHER", "PLANNER", "ANALYST", "REVIEWER", "EXECUTOR"
)
AGENT_DEFINITION_STATUS = _portable_enum("agent_definition_status", "ENABLED", "DISABLED")
AGENT_RUN_STATE = _portable_enum(
    "agent_run_state", "RUNNING", "SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"
)
EXECUTIVE_PLAN_STATE = _portable_enum(
    "executive_plan_state",
    "DRAFT",
    "PENDING_APPROVAL",
    "APPROVED",
    "EXECUTING",
    "COMPLETED",
    "FAILED",
    "PAUSED",
    "CANCELLED",
)
EXECUTIVE_ACTION_KIND = _portable_enum(
    "executive_action_kind",
    "GENERATE_RECOMMENDATIONS",
    "RUN_ANALYTICS_COLLECTION",
    "SYNC_OPPORTUNITY_SOURCE",
    "CREATE_CRM_DRAFT",
    "CREATE_INTERNAL_TASK",
    "NOOP_REPORT",
)
AUTONOMY_CLASS = _portable_enum("autonomy_class", "AUTO_INTERNAL", "APPROVAL_REQUIRED")
EXECUTIVE_ACTION_STATE = _portable_enum(
    "executive_action_state",
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "SKIPPED",
    "BLOCKED",
)
EXECUTIVE_CONTROL_MODE = _portable_enum(
    "executive_control_mode", "NORMAL", "HUMAN_APPROVAL_ALL", "PAUSED"
)
EXECUTIVE_PROPOSAL_KIND = _portable_enum(
    "executive_proposal_kind", "SCORING_WEIGHTS", "PROMPT_TEMPLATE", "POLICY", "OTHER"
)
EXECUTIVE_PROPOSAL_STATE = _portable_enum(
    "executive_proposal_state", "PROPOSED", "APPROVED", "REJECTED"
)

_POSTGRES_ENUMS = (
    ("agent_role", ("RESEARCHER", "PLANNER", "ANALYST", "REVIEWER", "EXECUTOR")),
    ("agent_definition_status", ("ENABLED", "DISABLED")),
    ("agent_run_state", ("RUNNING", "SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED")),
    (
        "executive_plan_state",
        (
            "DRAFT",
            "PENDING_APPROVAL",
            "APPROVED",
            "EXECUTING",
            "COMPLETED",
            "FAILED",
            "PAUSED",
            "CANCELLED",
        ),
    ),
    (
        "executive_action_kind",
        (
            "GENERATE_RECOMMENDATIONS",
            "RUN_ANALYTICS_COLLECTION",
            "SYNC_OPPORTUNITY_SOURCE",
            "CREATE_CRM_DRAFT",
            "CREATE_INTERNAL_TASK",
            "NOOP_REPORT",
        ),
    ),
    ("autonomy_class", ("AUTO_INTERNAL", "APPROVAL_REQUIRED")),
    (
        "executive_action_state",
        ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED", "BLOCKED"),
    ),
    ("executive_control_mode", ("NORMAL", "HUMAN_APPROVAL_ALL", "PAUSED")),
    (
        "executive_proposal_kind",
        ("SCORING_WEIGHTS", "PROMPT_TEMPLATE", "POLICY", "OTHER"),
    ),
    ("executive_proposal_state", ("PROPOSED", "APPROVED", "REJECTED")),
)

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, values in _POSTGRES_ENUMS:
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "agent_definitions",
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("role", AGENT_ROLE, nullable=False),
        sa.Column("contract", JSON_DOCUMENT, nullable=False),
        sa.Column("status", AGENT_DEFINITION_STATUS, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_agent_definitions_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_agent_definitions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_definitions")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_agent_definition_tenant_name"),
    )
    op.create_index(
        "ix_agent_definitions_tenant_role",
        "agent_definitions",
        ["tenant_id", "role", "status"],
    )

    op.create_table(
        "executive_controls",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("mode", EXECUTIVE_CONTROL_MODE, nullable=False),
        sa.Column("daily_cost_ceiling_minor", sa.BigInteger(), nullable=True),
        sa.Column("per_action_cost_ceiling_minor", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "daily_cost_ceiling_minor IS NULL OR daily_cost_ceiling_minor >= 0",
            name=op.f("ck_executive_controls_ck_executive_control_daily_ceiling_nonnegative"),
        ),
        sa.CheckConstraint(
            "per_action_cost_ceiling_minor IS NULL OR per_action_cost_ceiling_minor >= 0",
            name=op.f("ck_executive_controls_ck_executive_control_per_action_ceiling_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_executive_controls_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_executive_controls_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_executive_controls")),
        sa.UniqueConstraint("tenant_id", name="uq_executive_control_tenant"),
    )

    op.create_table(
        "executive_proposals",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", EXECUTIVE_PROPOSAL_KIND, nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("change_payload", JSON_DOCUMENT, nullable=False),
        sa.Column("state", EXECUTIVE_PROPOSAL_STATE, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_executive_proposals_ck_executive_proposal_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_executive_proposals_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name=op.f("fk_executive_proposals_reviewed_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_executive_proposals_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_executive_proposals")),
    )
    op.create_index(
        "ix_executive_proposals_tenant_state",
        "executive_proposals",
        ["tenant_id", "state"],
    )

    op.create_table(
        "executive_plans",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", EXECUTIVE_PLAN_STATE, nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", JSON_DOCUMENT, nullable=False),
        sa.Column("total_estimated_cost_minor", sa.BigInteger(), nullable=False),
        sa.Column("approval_request_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_job_id", sa.Uuid(), nullable=True),
        sa.Column("execution_requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_category", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "total_estimated_cost_minor >= 0",
            name=op.f("ck_executive_plans_ck_executive_plan_cost_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["approval_requests.id"],
            name=op.f("fk_executive_plans_approval_request_id_approval_requests"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["users.id"],
            name=op.f("fk_executive_plans_approved_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_executive_plans_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["execution_job_id"],
            ["background_jobs.id"],
            name=op.f("fk_executive_plans_execution_job_id_background_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["execution_requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_executive_plans_execution_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_executive_plans_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_executive_plans")),
    )
    op.create_index("ix_executive_plans_tenant_status", "executive_plans", ["tenant_id", "status"])

    op.create_table(
        "executive_actions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", EXECUTIVE_ACTION_KIND, nullable=False),
        sa.Column("payload", JSON_DOCUMENT, nullable=False),
        sa.Column("autonomy_class", AUTONOMY_CLASS, nullable=False),
        sa.Column("status", EXECUTIVE_ACTION_STATE, nullable=False),
        sa.Column("cost_estimate_minor", sa.BigInteger(), nullable=False),
        sa.Column("cost_incurred_minor", sa.BigInteger(), nullable=False),
        sa.Column("result", JSON_DOCUMENT, nullable=True),
        sa.Column("failure_category", sa.String(length=100), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("agent_definition_id", sa.Uuid(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cost_estimate_minor >= 0 AND cost_incurred_minor >= 0",
            name=op.f("ck_executive_actions_ck_executive_action_costs_nonnegative"),
        ),
        sa.CheckConstraint(
            "sequence >= 1",
            name=op.f("ck_executive_actions_ck_executive_action_sequence_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_definition_id"],
            ["agent_definitions.id"],
            name=op.f("fk_executive_actions_agent_definition_id_agent_definitions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["executive_plans.id"],
            name=op.f("fk_executive_actions_plan_id_executive_plans"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_executive_actions_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_executive_actions")),
        sa.UniqueConstraint("plan_id", "sequence", name="uq_executive_action_plan_sequence"),
    )
    op.create_index(
        "ix_executive_actions_tenant_plan", "executive_actions", ["tenant_id", "plan_id"]
    )

    op.create_table(
        "agent_runs",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("agent_name", sa.String(length=120), nullable=False),
        sa.Column("agent_role", AGENT_ROLE, nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("action_id", sa.Uuid(), nullable=True),
        sa.Column("status", AGENT_RUN_STATE, nullable=False),
        sa.Column("steps_used", sa.Integer(), nullable=False),
        sa.Column("cost_incurred_minor", sa.BigInteger(), nullable=False),
        sa.Column("usage_cost_event_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_category", sa.String(length=100), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "cost_incurred_minor >= 0",
            name=op.f("ck_agent_runs_ck_agent_run_cost_nonnegative"),
        ),
        sa.CheckConstraint(
            "steps_used >= 0", name=op.f("ck_agent_runs_ck_agent_run_steps_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["executive_actions.id"],
            name=op.f("fk_agent_runs_action_id_executive_actions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_definitions.id"],
            name=op.f("fk_agent_runs_agent_id_agent_definitions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["executive_plans.id"],
            name=op.f("fk_agent_runs_plan_id_executive_plans"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_agent_runs_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["usage_cost_event_id"],
            ["usage_cost_events.id"],
            name=op.f("fk_agent_runs_usage_cost_event_id_usage_cost_events"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index("ix_agent_runs_tenant_agent", "agent_runs", ["tenant_id", "agent_id"])
    op.create_index("ix_agent_runs_tenant_plan", "agent_runs", ["tenant_id", "plan_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_tenant_plan", table_name="agent_runs")
    op.drop_index("ix_agent_runs_tenant_agent", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_executive_actions_tenant_plan", table_name="executive_actions")
    op.drop_table("executive_actions")
    op.drop_index("ix_executive_plans_tenant_status", table_name="executive_plans")
    op.drop_table("executive_plans")
    op.drop_index("ix_executive_proposals_tenant_state", table_name="executive_proposals")
    op.drop_table("executive_proposals")
    op.drop_table("executive_controls")
    op.drop_index("ix_agent_definitions_tenant_role", table_name="agent_definitions")
    op.drop_table("agent_definitions")

    if op.get_bind().dialect.name == "postgresql":
        for name, _values in reversed(_POSTGRES_ENUMS):
            postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)

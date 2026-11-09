from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_session, require_role
from clipos.campaign_schemas import (
    AssessmentList,
    AssessmentResponse,
    AssessRequest,
    CampaignCreate,
    CampaignDetailResponse,
    CampaignImport,
    CampaignImportResponse,
    CampaignList,
    CampaignPublicationResponse,
    CampaignResponse,
    CampaignSummaryResponse,
    CampaignTransition,
    CampaignUpdate,
    LinkPublicationRequest,
    PayoutRequest,
    RequirementResponse,
)
from clipos.core.enums import CampaignStatus, Role
from clipos.core.security import Principal
from clipos.crm_schemas import RevenueResponse
from clipos.models import Campaign
from clipos.services.campaigns import CampaignService, campaign_is_expired

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _response(campaign: Campaign) -> CampaignResponse:
    response = CampaignResponse.model_validate(campaign)
    response.expired = campaign_is_expired(campaign)
    return response


@router.get("/summary", response_model=CampaignSummaryResponse)
async def get_summary(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CampaignSummaryResponse:
    result = await CampaignService.summary(session, principal.tenant_id)
    return CampaignSummaryResponse.model_validate(result)


@router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignCreate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> CampaignResponse:
    campaign, _created = await CampaignService.create_campaign(
        session,
        principal=principal,
        fields=payload.model_dump(exclude={"requirements", "idempotency_key"}),
        requirements=[item.model_dump() for item in payload.requirements],
        idempotency_key=payload.idempotency_key,
    )
    return _response(campaign)


@router.post("/import", response_model=CampaignImportResponse)
async def import_campaigns(
    payload: CampaignImport,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> CampaignImportResponse:
    result = await CampaignService.import_campaigns(
        session,
        principal=principal,
        records=[record.model_dump() for record in payload.records],
        idempotency_key=payload.idempotency_key,
    )
    return CampaignImportResponse.model_validate(result)


@router.get("", response_model=CampaignList)
async def list_campaigns(
    status_filter: CampaignStatus | None = Query(default=None, alias="status"),
    platform: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CampaignList:
    campaigns = await CampaignService.list_campaigns(
        session,
        principal.tenant_id,
        status=status_filter,
        platform=platform,
        limit=limit,
    )
    return CampaignList(items=[_response(item) for item in campaigns], total=len(campaigns))


@router.get("/{campaign_id}", response_model=CampaignDetailResponse)
async def get_campaign(
    campaign_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CampaignDetailResponse:
    campaign = await CampaignService.get_campaign(
        session, principal.tenant_id, campaign_id, detail=True
    )
    response = CampaignDetailResponse.model_validate(campaign)
    response.expired = campaign_is_expired(campaign)
    response.requirements = [
        RequirementResponse.model_validate(item) for item in campaign.requirements
    ]
    response.publication_ids = [link.publication_id for link in campaign.publication_links]
    if campaign.assessments:
        latest = max(campaign.assessments, key=lambda item: item.created_at)
        response.latest_assessment = AssessmentResponse.model_validate(latest)
    return response


@router.patch("/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
    campaign_id: UUID,
    payload: CampaignUpdate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> CampaignResponse:
    campaign = await CampaignService.update_campaign(
        session,
        principal=principal,
        campaign_id=campaign_id,
        updates=payload.model_dump(exclude_unset=True),
    )
    return _response(campaign)


@router.post("/{campaign_id}/transitions", response_model=CampaignResponse)
async def transition_campaign(
    campaign_id: UUID,
    payload: CampaignTransition,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> CampaignResponse:
    campaign = await CampaignService.transition_campaign(
        session,
        principal=principal,
        campaign_id=campaign_id,
        target_status=payload.target_status,
        override_reason=payload.override_reason,
    )
    return _response(campaign)


@router.post(
    "/{campaign_id}/assessments",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def assess_campaign(
    campaign_id: UUID,
    payload: AssessRequest,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> AssessmentResponse:
    assessment = await CampaignService.assess_profitability(
        session,
        principal=principal,
        settings=request.app.state.settings,
        campaign_id=campaign_id,
        expected_views=payload.expected_views,
        editing_cost_minor=payload.editing_cost_minor,
        model_cost_minor=payload.model_cost_minor,
        distribution_cost_minor=payload.distribution_cost_minor,
        risk_percent=payload.risk_percent,
    )
    return AssessmentResponse.model_validate(assessment)


@router.get("/{campaign_id}/assessments", response_model=AssessmentList)
async def list_assessments(
    campaign_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AssessmentList:
    assessments = await CampaignService.list_assessments(
        session, principal.tenant_id, campaign_id, limit=limit
    )
    return AssessmentList(
        items=[AssessmentResponse.model_validate(item) for item in assessments],
        total=len(assessments),
    )


@router.post(
    "/{campaign_id}/publications",
    response_model=CampaignPublicationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def link_publication(
    campaign_id: UUID,
    payload: LinkPublicationRequest,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> CampaignPublicationResponse:
    link = await CampaignService.link_publication(
        session,
        principal=principal,
        campaign_id=campaign_id,
        publication_id=payload.publication_id,
    )
    return CampaignPublicationResponse.model_validate(link)


@router.post(
    "/{campaign_id}/payouts",
    response_model=RevenueResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_payout(
    campaign_id: UUID,
    payload: PayoutRequest,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> RevenueResponse:
    record = await CampaignService.record_payout(
        session,
        principal=principal,
        campaign_id=campaign_id,
        amount_minor_units=payload.amount_minor_units,
        currency=payload.currency,
        account_id=payload.account_id,
        external_reference=payload.external_reference,
        occurred_at=payload.occurred_at,
        notes=payload.notes,
    )
    return RevenueResponse.model_validate(record)

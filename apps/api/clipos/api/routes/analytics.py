from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.analytics_schemas import (
    AnalyticsCapabilityResponse,
    CampaignSummaryResponse,
    CollectionCreate,
    CollectionRunList,
    CollectionRunResponse,
    CostSummaryResponse,
    ExperimentCreate,
    ExperimentList,
    ExperimentResponse,
    ExperimentTransition,
    ExperimentVariantCreate,
    ExperimentVariantResponse,
    PerformanceList,
    PredictedVsActualResponse,
    PublicationPerformanceResponse,
    PublicationPerformanceSummary,
    RecommendationList,
    RecommendationResponse,
    RecommendationReview,
)
from clipos.api.deps import get_principal, get_session, require_role
from clipos.core.enums import RecommendationState, Role
from clipos.core.security import Principal
from clipos.queue import QueueMessage
from clipos.services.analytics import (
    AnalyticsService,
    PublicationPerformanceResult,
    analytics_capabilities,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _performance_response(result: PublicationPerformanceResult) -> PublicationPerformanceResponse:
    publication = result["publication"]
    return PublicationPerformanceResponse(
        publication_id=publication.id,
        provider=publication.provider,
        provider_publication_id=publication.provider_publication_id,
        provider_url=publication.provider_url,
        campaign_reference=publication.campaign_reference,
        title=publication.title,
        metrics=result["metrics"],
        availability_counts=result["availability_counts"],
    )


@router.get("/capabilities", response_model=list[AnalyticsCapabilityResponse])
async def list_capabilities(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> list[AnalyticsCapabilityResponse]:
    del principal
    return [
        AnalyticsCapabilityResponse.model_validate(item)
        for item in analytics_capabilities(request.app.state.analytics_adapters)
    ]


@router.post(
    "/collections",
    response_model=CollectionRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_collection(
    payload: CollectionCreate,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> CollectionRunResponse:
    run, job, created = await AnalyticsService.queue_collection(
        session,
        principal=principal,
        provider=payload.provider,
        idempotency_key=payload.idempotency_key,
    )
    await session.commit()
    if created:
        await request.app.state.queue.enqueue(
            QueueMessage(job_id=job.id, tenant_id=job.tenant_id, kind=job.kind)
        )
    await session.refresh(run)
    return CollectionRunResponse.model_validate(run)


@router.get("/collection-runs", response_model=CollectionRunList)
async def list_collection_runs(
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CollectionRunList:
    runs = await AnalyticsService.list_collection_runs(session, principal.tenant_id, limit=limit)
    return CollectionRunList(
        items=[CollectionRunResponse.model_validate(run) for run in runs],
        total=len(runs),
    )


@router.get("/collection-runs/{run_id}", response_model=CollectionRunResponse)
async def get_collection_run(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CollectionRunResponse:
    run = await AnalyticsService.get_collection_run(session, principal.tenant_id, run_id)
    return CollectionRunResponse.model_validate(run)


@router.get("/publications/performance", response_model=PerformanceList)
async def list_performance(
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> PerformanceList:
    results = await AnalyticsService.list_performance(session, principal.tenant_id, limit=limit)
    items = []
    for result in results:
        publication = result["publication"]
        items.append(
            PublicationPerformanceSummary(
                publication_id=publication.id,
                provider=publication.provider,
                provider_publication_id=publication.provider_publication_id,
                campaign_reference=publication.campaign_reference,
                title=publication.title,
                views=result["views"],
                views_availability=result["views_availability"],
                metrics=result["metrics"],
            )
        )
    return PerformanceList(items=items, total=len(items))


@router.get(
    "/publications/{publication_id}/performance",
    response_model=PublicationPerformanceResponse,
)
async def get_publication_performance(
    publication_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> PublicationPerformanceResponse:
    result = await AnalyticsService.publication_performance(
        session, principal.tenant_id, publication_id
    )
    return _performance_response(result)


@router.get(
    "/publications/{publication_id}/predicted-vs-actual",
    response_model=PredictedVsActualResponse,
)
async def get_predicted_vs_actual(
    publication_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> PredictedVsActualResponse:
    result = await AnalyticsService.predicted_vs_actual(
        session, principal.tenant_id, publication_id
    )
    return PredictedVsActualResponse.model_validate(result)


@router.get("/campaigns/summary", response_model=CampaignSummaryResponse)
async def get_campaign_summary(
    campaign_reference: str = Query(min_length=1, max_length=160),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CampaignSummaryResponse:
    result = await AnalyticsService.campaign_summary(
        session, principal.tenant_id, campaign_reference
    )
    gaps: list[str] = []
    if result["publication_count"] == 0:
        gaps.append("no succeeded publications carry this campaign reference")
    return CampaignSummaryResponse.model_validate({**result, "gaps": gaps})


@router.get("/costs", response_model=CostSummaryResponse)
async def get_costs(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CostSummaryResponse:
    result = await AnalyticsService.cost_summary(session, principal.tenant_id)
    return CostSummaryResponse.model_validate(result)


@router.post(
    "/experiments",
    response_model=ExperimentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_experiment(
    payload: ExperimentCreate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> ExperimentResponse:
    experiment = await AnalyticsService.create_experiment(
        session,
        principal=principal,
        name=payload.name,
        hypothesis=payload.hypothesis,
        success_metric=payload.success_metric,
        eligibility=payload.eligibility,
        success_criteria=payload.success_criteria,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        notes=payload.notes,
    )
    return ExperimentResponse.model_validate(experiment)


@router.get("/experiments", response_model=ExperimentList)
async def list_experiments(
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ExperimentList:
    experiments = await AnalyticsService.list_experiments(session, principal.tenant_id, limit=limit)
    return ExperimentList(
        items=[ExperimentResponse.model_validate(item) for item in experiments],
        total=len(experiments),
    )


@router.get("/experiments/{experiment_id}", response_model=ExperimentResponse)
async def get_experiment(
    experiment_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ExperimentResponse:
    experiment = await AnalyticsService.get_experiment(session, principal.tenant_id, experiment_id)
    return ExperimentResponse.model_validate(experiment)


@router.post("/experiments/{experiment_id}/transitions", response_model=ExperimentResponse)
async def transition_experiment(
    experiment_id: UUID,
    payload: ExperimentTransition,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> ExperimentResponse:
    experiment = await AnalyticsService.transition_experiment(
        session,
        principal=principal,
        experiment_id=experiment_id,
        target_state=payload.target_state,
    )
    return ExperimentResponse.model_validate(experiment)


@router.post(
    "/experiments/{experiment_id}/variants",
    response_model=ExperimentVariantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_experiment_variant(
    experiment_id: UUID,
    payload: ExperimentVariantCreate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> ExperimentVariantResponse:
    variant = await AnalyticsService.add_variant(
        session,
        principal=principal,
        experiment_id=experiment_id,
        name=payload.name,
        description=payload.description,
        candidate_id=payload.candidate_id,
        publication_id=payload.publication_id,
        config=payload.config,
    )
    return ExperimentVariantResponse.model_validate(variant)


@router.get("/recommendations", response_model=RecommendationList)
async def list_recommendations(
    state: RecommendationState | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> RecommendationList:
    recommendations = await AnalyticsService.list_recommendations(
        session, principal.tenant_id, state=state, limit=limit
    )
    return RecommendationList(
        items=[RecommendationResponse.model_validate(item) for item in recommendations],
        total=len(recommendations),
    )


@router.post(
    "/recommendations/generate",
    response_model=RecommendationList,
    status_code=status.HTTP_201_CREATED,
)
async def generate_recommendations(
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> RecommendationList:
    created = await AnalyticsService.generate_recommendations(session, principal=principal)
    return RecommendationList(
        items=[RecommendationResponse.model_validate(item) for item in created],
        total=len(created),
    )


@router.post("/recommendations/{recommendation_id}/review", response_model=RecommendationResponse)
async def review_recommendation(
    recommendation_id: UUID,
    payload: RecommendationReview,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> RecommendationResponse:
    recommendation = await AnalyticsService.review_recommendation(
        session,
        principal=principal,
        recommendation_id=recommendation_id,
        approve=payload.decision == "approve",
        note=payload.note,
    )
    return RecommendationResponse.model_validate(recommendation)

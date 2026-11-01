from fastapi import APIRouter, Request, Response

from clipos.schemas import HealthResponse
from clipos.services.health import health_response, readiness_response

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def liveness(request: Request) -> HealthResponse:
    return await health_response(request.app.state.settings)


@router.get("/readyz", response_model=HealthResponse)
async def readiness(request: Request, response: Response) -> HealthResponse:
    report, status_code = await readiness_response(
        settings=request.app.state.settings,
        engine=request.app.state.engine,
        queue=request.app.state.queue,
        storage_root=getattr(request.app.state, "storage_root", None),
        media_status=request.app.state.media_tools.status,
    )
    response.status_code = status_code
    return report

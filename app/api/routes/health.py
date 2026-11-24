from fastapi import APIRouter, Response, status

from app.api.dependencies import HealthServiceDep, SettingsDep
from app.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(settings: SettingsDep) -> HealthResponse:
    """Returns 200 while the process is running. Does not touch dependencies."""
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env.value,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
async def ready(service: HealthServiceDep, response: Response) -> ReadinessResponse:
    """Checks PostgreSQL (incl. pgvector) and Redis. Returns 503 if any check fails."""
    result = await service.readiness()
    if result.status != "ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result

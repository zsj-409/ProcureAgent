"""ProcureAgent FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .agent.context import ContextBuilder
from .agent.interpreter import ProcurementInterpreter
from .agent.model import ModelClient, OpenAICompatibleModelClient
from .agent.orchestrator import ProcurementOrchestrator, RecommendationFinalizer
from .agent.planner import LLMPlanner, RuleBasedPlanner
from .agent.runtime import ExecutionBudget, TaskRuntime
from .agent.validator import ExecutionValidator, PlanValidator
from .api.agent import router as agent_router
from .api.approvals import router as approvals_router
from .api.tasks import router as tasks_router
from .executors.router import ExecutorRouter
from .infrastructure.database import TaskRepository, build_session_factory
from .infrastructure.settings import Settings, get_settings
from .observability.trace import TraceRecorder
from .procurement.normalizer import QuoteNormalizer
from .procurement.policy import ProcurementPolicy
from .procurement.scorer import ProcurementScorer
from .suppliers.base import SupplierRegistry


def build_model_client(settings: Settings, trace: TraceRecorder) -> ModelClient | None:
    """Build the optional model client. ``None`` means deterministic fallback."""

    if not settings.llm_api_key:
        return None
    return OpenAICompatibleModelClient(settings, trace)


def build_components(
    settings: Settings,
    *,
    model_client: ModelClient | None = None,
    registry: SupplierRegistry | None = None,
) -> dict:
    """Assemble all application collaborators."""

    session_factory = build_session_factory(settings.database_url)
    repository = TaskRepository(session_factory)
    trace = TraceRecorder(repository)
    registry = registry or SupplierRegistry(config_path=settings.suppliers_config_path)
    router = ExecutorRouter(settings, registry)
    model = model_client if model_client is not None else build_model_client(settings, trace)
    context_builder = ContextBuilder()

    fallback_planner = RuleBasedPlanner()
    plan_validator = PlanValidator(max_steps=settings.agent_max_steps)
    planner = (
        LLMPlanner(
            model=model,
            context_builder=context_builder,
            validator=plan_validator,
            fallback=fallback_planner,
        )
        if model is not None
        else fallback_planner
    )

    runtime = TaskRuntime(
        repository,
        ExecutionBudget(
            max_steps=settings.agent_max_steps,
            max_attempts=settings.agent_max_attempts,
            max_task_seconds=settings.agent_max_task_seconds,
            max_replans=settings.agent_max_replans,
        ),
    )
    finalizer = RecommendationFinalizer(model)
    orchestrator = ProcurementOrchestrator(
        planner=planner,
        router=router,
        normalizer=QuoteNormalizer(),
        scorer=ProcurementScorer(),
        policy=ProcurementPolicy(threshold=settings.approval_threshold),
        repository=repository,
        trace=trace,
        runtime=runtime,
        validator=ExecutionValidator(),
        context_builder=context_builder,
        finalizer=finalizer,
    )
    interpreter = ProcurementInterpreter(model)
    return {
        "repository": repository,
        "trace": trace,
        "orchestrator": orchestrator,
        "interpreter": interpreter,
        "model": model,
    }


def create_app(
    settings: Settings | None = None,
    *,
    model_client: ModelClient | None = None,
    registry: SupplierRegistry | None = None,
) -> FastAPI:
    """Create the FastAPI application."""

    settings = settings or get_settings()
    components = build_components(settings, model_client=model_client, registry=registry)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for key, value in components.items():
            setattr(app.state, key, value)
        yield

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="A single-agent enterprise procurement system.",
        lifespan=lifespan,
    )
    for key, value in components.items():
        setattr(app.state, key, value)
    app.include_router(tasks_router, prefix="/api/v1/tasks")
    app.include_router(approvals_router, prefix="/api/v1/tasks")
    app.include_router(agent_router, prefix="/api/v1/agent")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    return app


app = create_app()

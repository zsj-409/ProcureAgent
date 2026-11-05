"""ProcureAgent FastAPI application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .agent.background import BackgroundTaskRunner
from .agent.context import ContextBuilder
from .agent.interpreter import ProcurementInterpreter
from .agent.model import ModelClient, OpenAICompatibleModelClient
from .agent.orchestrator import ProcurementOrchestrator, RecommendationFinalizer
from .agent.planner import LLMPlanner, RuleBasedPlanner
from .agent.runtime import ExecutionBudget, TaskRuntime
from .agent.validator import ExecutionValidator, PlanValidator
from .api.agent import router as agent_router
from .api.approvals import router as approvals_router
from .api.observatory import router as observatory_router
from .api.tasks import router as tasks_router
from .api.workbench import router as workbench_router
from .executors.router import ExecutorRouter
from .infrastructure.database import TaskRepository, build_session_factory
from .infrastructure.settings import Settings, get_settings
from .observability.trace import TraceRecorder
from .procurement.normalizer import QuoteNormalizer
from .procurement.policy import ProcurementPolicy
from .procurement.scorer import ProcurementScorer
from .security.approval_guard import ApprovalGuard
from .suppliers.base import SupplierRegistry


def build_model_client(settings: Settings, trace: TraceRecorder | None) -> ModelClient | None:
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
    background = BackgroundTaskRunner(None, repository)
    registry = registry or SupplierRegistry(config_path=settings.suppliers_config_path)
    model = model_client if model_client is not None else build_model_client(settings, trace)
    router = ExecutorRouter(settings, registry, trace=trace, model=model)
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
        settings=settings,
    )
    orchestrator_ready = orchestrator
    background.bind(orchestrator_ready)
    interpreter = ProcurementInterpreter(model)
    approval_guard = ApprovalGuard(settings)
    # Phase 10: OpenTelemetry AgentOps handle (in-memory by default; OTLP
    # optional). Exporter failure never breaks procurement tasks.
    # NOTE: OTel forbids re-setting the global TracerProvider, so the handle
    # is created ONCE per process and cached.
    otel_handle = getattr(build_components, "_otel_handle", None)
    if getattr(settings, "otel_enabled", True):
        if otel_handle is None:
            try:
                from .observability.otel import setup_otel

                otel_handle = setup_otel(
                    settings,
                    in_memory=not getattr(settings, "otel_export_enabled", False),
                )
                build_components._otel_handle = otel_handle  # type: ignore[attr-defined]
            except Exception:
                otel_handle = None
    else:
        otel_handle = None
    orchestrator.otel_handle = otel_handle  # type: ignore[attr-defined]
    return {
        "repository": repository,
        "trace": trace,
        "orchestrator": orchestrator,
        "interpreter": interpreter,
        "model": model,
        "registry": registry,
        "background": background,
        "approval_guard": approval_guard,
        "otel_handle": otel_handle,
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
    components["settings"] = settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for key, value in components.items():
            setattr(app.state, key, value)
        # Start the MCP session manager (owns the anyio task group).
        mcp_mgr = getattr(app.state, "mcp_session_manager", None)
        if mcp_mgr is not None:
            async with mcp_mgr.run():
                yield
        else:
            yield
        components["background"].shutdown()

    app = FastAPI(
        title=settings.app_name,
        version="2.0.0",
        description="A single-agent enterprise procurement system with an adaptive portal runtime.",
        lifespan=lifespan,
    )
    for key, value in components.items():
        setattr(app.state, key, value)
    app.include_router(tasks_router, prefix="/api/v1/tasks")
    app.include_router(approvals_router, prefix="/api/v1/tasks")
    app.include_router(agent_router, prefix="/api/v1/agent")
    app.include_router(workbench_router, prefix="/api/v1")
    app.include_router(observatory_router, prefix="/api/v1")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    # /portal-frames must be mounted BEFORE the catch-all "/" SPA mount,
    # otherwise the static mount swallows every portal-frame request.
    frames_root = Path(settings.portal_frames_dir)
    frames_root.mkdir(parents=True, exist_ok=True)
    app.mount("/portal-frames", StaticFiles(directory=str(frames_root)), name="portal-frames")

    # Phase 10: Secure MCP Tool Gateway — mounted BEFORE the catch-all "/"
    # static mount, otherwise StaticFiles (GET/HEAD only) would swallow the
    # MCP POST requests with 405.
    if getattr(settings, "mcp_enabled", True):
        from .mcp_gateway.server import build_mcp_asgi_app, build_mcp_server

        mcp_server = build_mcp_server(
            settings=settings,
            registry=components["registry"],
            repository=components["repository"],
            guard=components["approval_guard"],
            task_owner=lambda task_id: (
                "demo-approver" if components["repository"].get(task_id) else None
            ),
        )
        mcp_asgi, mcp_session_manager = build_mcp_asgi_app(mcp_server, path="/mcp")
        # Phase 10: attach the OTel tracer to the gateway for tool spans.
        gw = getattr(mcp_server, "_policy_gateway", None)
        if gw is not None:
            otel_h = components.get("otel_handle")
            if otel_h and otel_h.get("tracer") is not None:
                gw._tracer = otel_h["tracer"]  # type: ignore[attr-defined]
        app.mount("/mcp/", mcp_asgi, name="mcp")
        app.state.mcp_server = mcp_server
        app.state.mcp_session_manager = mcp_session_manager

    static_dir = Path(__file__).parent / "webui" / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()

"""The core procurement execution flow."""

import time
from datetime import datetime, timezone

from ..errors import SupplierUnavailableError
from ..executors.base import ExecutorTask
from ..executors.router import ExecutorRouter
from ..infrastructure.database import TaskRepository
from ..observability.trace import TraceRecorder
from ..procurement.normalizer import QuoteNormalizer
from ..procurement.policy import ProcurementPolicy
from ..procurement.schemas import (
    PlanAction,
    ProcurementPlan,
    ProcurementRecommendation,
    ProcurementRequest,
    SupplierQuote,
)
from ..procurement.scorer import ProcurementScorer
from ..suppliers.base import SupplierProfile
from .context import ContextBuilder
from .model import ModelClient
from .planner import Planner
from .prompts import FINALIZER_SYSTEM
from .runtime import ExecutionBudget, TaskRuntime
from .state import TaskState, TaskStatus
from .validator import ExecutionValidator, ValidationDecision


class RecommendationFinalizer:
    """Generate a recommendation explanation from structured facts only."""

    def __init__(self, model: ModelClient | None = None):
        self.model = model

    async def generate(
        self,
        recommendation: ProcurementRecommendation,
        request: ProcurementRequest,
        task_id: str | None = None,
    ) -> str:
        """Return an LLM explanation, or a deterministic template on failure."""

        if self.model is None:
            return self._template(recommendation)

        facts = (
            f"Recommendation: {recommendation.model_dump_json()}\n"
            f"Request: {request.model_dump_json()}"
        )
        try:
            return await self.model.generate_text(
                system=FINALIZER_SYSTEM,
                user=facts,
                task_id=task_id,
                step="finalize",
                component="RecommendationFinalizer",
            )
        except Exception:
            return self._template(recommendation)

    @staticmethod
    def _template(recommendation: ProcurementRecommendation) -> str:
        return (
            f"Recommended supplier: {recommendation.recommended_supplier}. "
            f"Score: {recommendation.score}. "
            f"Estimated total: {recommendation.estimated_total} USD. "
            f"Reason: {recommendation.reason}"
        )


class ProcurementOrchestrator:
    """Execute a procurement task against abstract interfaces only."""

    def __init__(
        self,
        *,
        planner: Planner,
        router: ExecutorRouter,
        normalizer: QuoteNormalizer,
        scorer: ProcurementScorer,
        policy: ProcurementPolicy,
        repository: TaskRepository,
        trace: TraceRecorder,
        runtime: TaskRuntime,
        validator: ExecutionValidator,
        context_builder: ContextBuilder,
        finalizer: RecommendationFinalizer,
    ):
        self.planner = planner
        self.router = router
        self.normalizer = normalizer
        self.scorer = scorer
        self.policy = policy
        self.repository = repository
        self.trace = trace
        self.runtime = runtime
        self.validator = validator
        self.context_builder = context_builder
        self.finalizer = finalizer

    async def run(self, task_id: str, request: ProcurementRequest) -> TaskState:
        """Run a new procurement task."""

        now = datetime.now(timezone.utc)
        state = TaskState(
            task_id=task_id,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        self.repository.save_state(state)

        state.status = TaskStatus.PLANNING
        self._save(state)
        suppliers = self.router.suppliers()
        plan = await self.planner.plan(request, suppliers, "")
        self.repository.save_plan(task_id, plan)

        state.status = TaskStatus.COLLECTING
        state.current_step = "collect"
        self._save(state)
        return await self._execute_plan(task_id, request, plan, state)

    async def resume(self, task_id: str) -> TaskState:
        """Resume a FAILED task using the same task id and plan."""

        record = self.repository.get(task_id)
        if record is None:
            raise ValueError(f"Task {task_id} does not exist")
        state = self.repository.to_state(record)
        request = self.repository.get_request(task_id)
        plan = self.repository.get_plan(task_id)
        if request is None:
            raise ValueError(f"Task {task_id} has no persisted request")
        suppliers = self.router.suppliers()
        if plan is None:
            plan = await self.planner.plan(request, suppliers, "")
            self.repository.save_plan(task_id, plan)

        state.status = TaskStatus.COLLECTING
        state.error = None
        state.current_step = "collect"
        self.trace.record(
            task_id=task_id,
            step="resume",
            component="ProcurementOrchestrator",
            action="resume_started",
            status="SUCCESS",
            duration_ms=0,
        )
        self._save(state)
        return await self._execute_plan(task_id, request, plan, state, resume=True)

    async def _execute_plan(
        self,
        task_id: str,
        request: ProcurementRequest,
        plan: ProcurementPlan,
        state: TaskState,
        *,
        resume: bool = False,
    ) -> TaskState:
        suppliers = self.router.suppliers()
        supplier_map: dict[str, SupplierProfile] = {s.supplier_id: s for s in suppliers}
        quotes: list[SupplierQuote] = []
        failures: dict[str, str] = {}
        executed_steps = 0
        started_at = time.perf_counter()
        replans = 0

        await self._collect_suppliers(
            task_id,
            request,
            plan,
            state,
            supplier_map,
            quotes,
            failures,
            resume=resume,
        )
        executed_steps = len(quotes) + len(failures)

        decision = await self._validate(task_id, request, plan, state, quotes, failures)

        if decision.decision == ValidationDecision.REPLAN and replans < self.runtime.budget.max_replans:
            replans += 1
            context = self.context_builder.build(
                original_goal=f"Purchase {request.quantity} {request.product_name}",
                request=request,
                plan=plan,
                state=state,
                quotes=quotes,
                failures=failures,
            )
            new_plan = await self.planner.plan(request, suppliers, context)
            self.repository.save_plan(task_id, new_plan)
            await self._collect_suppliers(
                task_id,
                request,
                new_plan,
                state,
                supplier_map,
                quotes,
                failures,
                resume=True,
            )
            decision = await self._validate(task_id, request, new_plan, state, quotes, failures)

        if decision.decision == ValidationDecision.FAIL or not quotes:
            return self._fail(state, failures, "No usable supplier quotes after validation")

        state.status = TaskStatus.SCORING
        state.current_step = "score"
        self._save(state)
        scored_started = time.perf_counter()
        recommendation = self.scorer.score(quotes, request.quantity, request.preference)
        recommendation.partial_result = bool(failures)
        self._trace_success(
            task_id, "score", "ProcurementScorer", "score",
            int((time.perf_counter() - scored_started) * 1000),
        )

        state.current_step = "check_policy"
        self._save(state)
        policy_decision = self.policy.evaluate(recommendation.estimated_total, request.max_budget)
        recommendation.approval_required = policy_decision.requires_approval
        self._trace_success(task_id, "check_policy", "ProcurementPolicy", "evaluate", 0)

        recommendation.summary = await self.finalizer.generate(recommendation, request, task_id)
        self.repository.save_recommendation(task_id, recommendation)

        state.partial_result = recommendation.partial_result
        if policy_decision.status == TaskStatus.APPROVED:
            state.status = TaskStatus.COMPLETED
        else:
            state.status = TaskStatus.WAITING_APPROVAL
        state.current_step = "finalize"
        self._save(state)
        if state.status == TaskStatus.COMPLETED:
            self.trace.record(
                task_id=task_id,
                step="finalize",
                component="ProcurementOrchestrator",
                action="task_completed",
                status="SUCCESS",
                duration_ms=0,
            )
        return state

    async def _collect_suppliers(
        self,
        task_id: str,
        request: ProcurementRequest,
        plan: ProcurementPlan,
        state: TaskState,
        supplier_map: dict[str, SupplierProfile],
        quotes: list[SupplierQuote],
        failures: dict[str, str],
        *,
        resume: bool,
    ) -> None:
        for step in plan.steps:
            if step.action != PlanAction.COLLECT_SUPPLIER or not step.supplier_id:
                continue
            supplier_id = step.supplier_id
            step_name = f"collect_{supplier_id}"
            state.current_step = step_name
            self._save(state)

            supplier = supplier_map.get(supplier_id)
            if supplier is None:
                failures[supplier_id] = "Unknown supplier"
                self._trace_error(task_id, step_name, "ExecutorRouter", "route", failures[supplier_id], 0)
                continue

            if resume:
                cached = self.repository.get_completed_step_result(task_id, step_name, supplier_id)
                if cached is not None:
                    self._add_quote_from_raw(task_id, request, supplier, cached, quotes, step_name)
                    self.trace.record(
                        task_id=task_id,
                        step=step_name,
                        component="TaskRuntime",
                        action="checkpoint_reused",
                        status="SUCCESS",
                        duration_ms=0,
                    )
                    continue

            async def collect() -> dict:
                executor = self.router.route(supplier)
                result = await executor.execute(
                    ExecutorTask(
                        task_id=task_id,
                        supplier=supplier,
                        product_name=request.product_name,
                        quantity=request.quantity,
                    )
                )
                if not result.success or not result.quote:
                    raise SupplierUnavailableError(result.error or "No quote returned")
                return result.quote

            started = time.perf_counter()
            runtime_result = await self.runtime.execute(
                task_id=task_id,
                step_name=step_name,
                supplier_id=supplier_id,
                timeout_seconds=self._step_timeout(),
                action=collect,
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            if runtime_result.success and runtime_result.result:
                self._add_quote_from_raw(
                    task_id, request, supplier, runtime_result.result, quotes, step_name
                )
                self._trace_success(task_id, step_name, supplier.source_type, "execute", duration_ms)
            else:
                failures[supplier_id] = runtime_result.error or "No quote returned"
                self._trace_error(task_id, step_name, supplier.source_type, "execute", failures[supplier_id], duration_ms)

    def _add_quote_from_raw(
        self,
        task_id: str,
        request: ProcurementRequest,
        supplier: SupplierProfile,
        raw: dict,
        quotes: list[SupplierQuote],
        step_name: str,
    ) -> None:
        try:
            quote = self.normalizer.normalize(
                raw,
                supplier_id=supplier.supplier_id,
                product_name=request.product_name,
                source_type=supplier.source_type,
            )
            quotes.append(quote)
        except Exception as exc:  # noqa: BLE001
            self._trace_error(task_id, step_name, "QuoteNormalizer", "normalize", str(exc), 0)

    async def _validate(
        self,
        task_id: str,
        request: ProcurementRequest,
        plan: ProcurementPlan,
        state: TaskState,
        quotes: list[SupplierQuote],
        failures: dict[str, str],
    ):
        context = self.context_builder.build(
            original_goal=f"Purchase {request.quantity} {request.product_name}",
            request=request,
            plan=plan,
            state=state,
            quotes=quotes,
            failures=failures,
        )
        return await self.validator.decide(
            quotes=quotes,
            request=request,
            context=context,
            model=self.finalizer.model if isinstance(self.finalizer, RecommendationFinalizer) else None,
        )

    def _step_timeout(self) -> float:
        return self.runtime.budget.max_task_seconds / max(self.runtime.budget.max_steps, 1)

    def _fail(self, state: TaskState, failures: dict[str, str], message: str) -> TaskState:
        state.status = TaskStatus.FAILED
        state.error = message
        if failures:
            state.current_step = f"collect_{next(reversed(failures))}"
        self._save(state)
        return state

    def _save(self, state: TaskState) -> None:
        state.updated_at = datetime.now(timezone.utc)
        self.repository.save_state(state)

    def _trace_success(self, task_id, step, component, action, duration_ms) -> None:
        self.trace.record(
            task_id=task_id,
            step=step,
            component=component,
            action=action,
            status="SUCCESS",
            duration_ms=duration_ms,
        )

    def _trace_error(self, task_id, step, component, action, error, duration_ms) -> None:
        self.trace.record(
            task_id=task_id,
            step=step,
            component=component,
            action=action,
            status="ERROR",
            duration_ms=duration_ms,
            error=error,
        )

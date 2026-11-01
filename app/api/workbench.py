"""Workbench API: task list, rejection, supplier health, portal replay."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from ..agent.state import TaskStatus
from ..security.approval_guard import ApprovalGuard
from ..security.principal import Role

router = APIRouter(tags=["workbench"])


def _approval_guard(request: Request) -> ApprovalGuard:
    guard = getattr(request.app.state, "approval_guard", None)
    return guard if guard is not None else ApprovalGuard()


@router.get("/tasks")
async def list_tasks(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
) -> list[dict]:
    """Return the most recent tasks with request and recommendation summaries."""

    repository = request.app.state.repository
    items = []
    for record in repository.list_tasks(limit=limit):
        if status and record.status != status:
            continue
        state = repository.to_state(record)
        recommendation = repository.get_recommendation(record.id)
        request_data = repository.get_request(record.id)
        plan = repository.get_plan(record.id)
        items.append(
            {
                "task_id": record.id,
                "status": record.status,
                "current_step": record.current_step,
                "error": record.error,
                "partial_result": record.partial_result,
                "created_at": record.created_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
                "request": request_data.model_dump(mode="json") if request_data else None,
                "plan": plan.model_dump(mode="json") if plan else None,
                "recommended_supplier": recommendation.recommended_supplier if recommendation else None,
                "estimated_total": (
                    str(recommendation.estimated_total) if recommendation else None
                ),
                "score": str(recommendation.score) if recommendation else None,
                "approval_required": recommendation.approval_required if recommendation else False,
                "has_split": bool(recommendation and recommendation.award_split),
                "state": state.model_dump(mode="json"),
            }
        )
    return items


@router.post("/tasks/{task_id}/reject")
async def reject_task(task_id: str, request: Request) -> dict:
    """Reject a task that is waiting for human approval.

    Phase 1: guarded exactly like approve — default deny for unauthenticated
    callers; identity never trusted from the request body.
    """

    guard = _approval_guard(request)
    decision = guard.check(request, required_role=Role.APPROVER)
    if not decision.allowed:
        raise HTTPException(
            status_code=decision.http_status,
            detail={
                "reason_code": decision.reason_code or "DENIED",
                "detail": f"Rejection denied for task {task_id}",
            },
        )

    repository = request.app.state.repository
    record = repository.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # Phase 3 CAS: reject only wins if the task is still WAITING_APPROVAL.
    # Concurrent approve/reject: exactly one transition succeeds.
    changed = repository.cas_transition(
        task_id,
        from_status=TaskStatus.WAITING_APPROVAL.value,
        to_status=TaskStatus.REJECTED.value,
    )
    if not changed:
        current = repository.get(task_id)
        raise HTTPException(
            status_code=409,
            detail={
                "reason_code": "APPROVAL_RACE_LOST",
                "detail": f"Task cannot be rejected from status {current.status if current else '?'}",
            },
        )
    approver_id = decision.principal.id if decision.principal else "unknown"
    repository.create_approval(
        task_id,
        approver=approver_id,
        decision="rejected",
        recommendation_version=None,
        reason_code=None,
    )
    now = datetime.now(UTC)
    return {
        "task_id": task_id,
        "status": TaskStatus.REJECTED.value,
        "rejected_at": now.isoformat(),
        "reason_code": "OK",
    }


@router.get("/tasks/{task_id}/approvals")
async def list_approvals(task_id: str, request: Request) -> list[dict]:
    """Return the approval history for a task."""

    repository = request.app.state.repository
    if repository.get(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return [
        {
            "approver": row.approver,
            "decision": row.decision,
            "recommendation_version": row.recommendation_version,
            "reason_code": row.reason_code,
            "created_at": row.created_at.isoformat(),
        }
        for row in repository.list_approvals(task_id)
    ]


@router.get("/suppliers")
async def list_suppliers(request: Request) -> list[dict]:
    """Return configured suppliers with a live health probe per supplier."""

    registry = request.app.state.registry

    async def probe(supplier: dict) -> dict:
        enriched = {**supplier, "healthy": None, "latency_ms": None}
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                response = await client.get(supplier["base_url"].rstrip("/") + "/health")
            enriched["healthy"] = response.status_code == 200
            enriched["latency_ms"] = int(response.elapsed.total_seconds() * 1000)
        except (httpx.HTTPError, OSError):
            enriched["healthy"] = False
        return enriched

    suppliers = [
        {
            "supplier_id": profile.supplier_id,
            "display_name": profile.display_name,
            "source_type": profile.source_type,
            "base_url": profile.base_url,
            "search_endpoint": profile.search_endpoint,
            "search_path": profile.search_path,
            "priority": profile.priority,
            "adaptive": profile.source_type == "portal"
            and profile.search_input_selector is None,
            "fixed_script": profile.source_type == "portal"
            and profile.search_input_selector is not None,
        }
        for profile in registry.enabled_profiles()
    ]
    return await asyncio.gather(*(probe(item) for item in suppliers))


@router.get("/tasks/{task_id}/portal-steps")
async def portal_steps(task_id: str, request: Request) -> dict:
    """Return the adaptive-portal replay manifest (per supplier) for a task."""

    settings = request.app.state.settings
    root = Path(settings.portal_frames_dir) / task_id
    if not root.is_dir():
        return {"task_id": task_id, "suppliers": {}}
    suppliers: dict[str, dict] = {}
    for manifest_path in sorted(root.glob("*.steps.json")):
        supplier_id = manifest_path.name.replace(".steps.json", "")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # frame_path stays relative (data/portal_frames/...); the UI maps it
        # onto the /portal-frames mount itself.
        suppliers[supplier_id] = payload
    return {"task_id": task_id, "suppliers": suppliers}


@router.get("/overview")
async def overview(request: Request) -> dict:
    """Aggregate counters for the workbench landing page."""

    repository = request.app.state.repository
    settings = request.app.state.settings
    records = repository.list_tasks(limit=200)
    by_status: dict[str, int] = {}
    for record in records:
        by_status[record.status] = by_status.get(record.status, 0) + 1
    suppliers = request.app.state.registry.enabled_profiles()
    llm_configured = bool(settings.llm_api_key)
    return {
        "tasks_total": len(records),
        "by_status": by_status,
        "suppliers_total": len(suppliers),
        "portal_suppliers": sum(1 for item in suppliers if item.source_type == "portal"),
        "api_suppliers": sum(1 for item in suppliers if item.source_type == "api"),
        "llm_configured": llm_configured,
        "portal_adaptive_enabled": settings.portal_adaptive,
        "approval_threshold": str(settings.approval_threshold),
    }

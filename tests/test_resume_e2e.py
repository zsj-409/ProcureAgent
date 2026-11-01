"""Cross-execution-cycle Resume E2E test.

The test proves that recovery depends on persisted SQLite checkpoints rather
than in-memory Python state: it runs task to FAILED, releases the app, rebuilds
the repository/runtime/orchestrator, and resumes with the same task id.
"""

import socket
import threading
import time
from decimal import Decimal

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import create_app
from app.suppliers.base import SupplierProfile, SupplierRegistry

CALLS = {"A": 0, "B": 0, "C": 0}
A_BAD_PAYLOAD = True
B_SHOULD_FAIL = True
C_BAD_PAYLOAD = True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app) -> str:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}"


def _build_supplier_app() -> FastAPI:
    app = FastAPI()

    def _quote(supplier_id: str, price: str, stock: int) -> dict:
        return {
            "product_name": "Resume Mouse",
            "unit_price": price,
            "currency": "USD",
            "stock": stock,
            "delivery_days": 1,
            "supplier_id": supplier_id,
        }

    @app.get("/a")
    def supplier_a(product_name: str = Query(...), quantity: int = Query(...)):
        CALLS["A"] += 1
        if A_BAD_PAYLOAD:
            # Valid HTTP response with an unparseable quote: the step completes
            # (and is checkpointed) but the normalizer drops the payload.
            return {
                "product_name": "Resume Mouse",
                "unit_price": "",
                "stock": 1,
                "delivery_days": 1,
                "supplier_id": "supplier_a",
            }
        return _quote("supplier_a", "5.00", 1)

    @app.get("/b")
    def supplier_b(product_name: str = Query(...), quantity: int = Query(...)):
        CALLS["B"] += 1
        if B_SHOULD_FAIL:
            raise HTTPException(status_code=500, detail="supplier B down")
        return _quote("supplier_b", "1.00", 30)

    @app.get("/c")
    def supplier_c(product_name: str = Query(...), quantity: int = Query(...)):
        CALLS["C"] += 1
        if C_BAD_PAYLOAD:
            return {
                "product_name": "Resume Mouse",
                "unit_price": "not-a-price",
                "stock": 1,
                "delivery_days": 1,
                "supplier_id": "supplier_c",
            }
        return _quote("supplier_c", "4.00", 1)

    return app


def _registry(server_url: str) -> SupplierRegistry:
    return SupplierRegistry(
        profiles=[
            SupplierProfile(
                supplier_id="supplier_a",
                display_name="Supplier A",
                source_type="api",
                base_url=server_url,
                search_endpoint="/a",
                priority=10,
            ),
            SupplierProfile(
                supplier_id="supplier_b",
                display_name="Supplier B",
                source_type="api",
                base_url=server_url,
                search_endpoint="/b",
                priority=20,
            ),
            SupplierProfile(
                supplier_id="supplier_c",
                display_name="Supplier C",
                source_type="api",
                base_url=server_url,
                search_endpoint="/c",
                priority=30,
            ),
        ]
    )


def test_resume_restores_from_sqlite_checkpoint(tmp_path):
    global A_BAD_PAYLOAD, B_SHOULD_FAIL, C_BAD_PAYLOAD
    server_url = _serve(_build_supplier_app())
    db_path = tmp_path / "resume.db"
    settings = Settings(
        database_url=f"sqlite:///{db_path}",
        approval_threshold=Decimal(1000),
        agent_max_attempts=2,
        agent_max_task_seconds=60,
        agent_max_replans=1,
        supplier_api_timeout_seconds=5,
    )

    # First execution cycle: A and C return unparseable payloads, B is down.
    # Normalization drops A and C, so the quote set is empty -> FAILED while
    # their step executions still reached COMPLETED (checkpointed).
    A_BAD_PAYLOAD = True
    B_SHOULD_FAIL = True
    C_BAD_PAYLOAD = True
    with TestClient(create_app(settings, registry=_registry(server_url))) as client:
        response = client.post(
            "/api/v1/tasks",
            json={"product_name": "Resume Mouse", "quantity": 20, "preference": "BALANCED"},
        )
        assert response.status_code == 201, response.text
        task_id = response.json()["task_id"]
        assert response.json()["status"] == "FAILED"
        repository = client.app.state.repository
        first_steps = repository.list_step_executions(task_id)
        assert {(s.step_name, s.status) for s in first_steps} == {
            ("collect_supplier_a", "COMPLETED"),
            ("collect_supplier_b", "FAILED"),
            ("collect_supplier_c", "COMPLETED"),
        }

    assert CALLS["A"] == 1
    assert CALLS["B"] == 2
    assert CALLS["C"] == 1

    # Second execution cycle: rebuild the whole app after suppliers recover.
    A_BAD_PAYLOAD = False
    B_SHOULD_FAIL = False
    C_BAD_PAYLOAD = False
    with TestClient(create_app(settings, registry=_registry(server_url))) as client:
        resume = client.post(f"/api/v1/tasks/{task_id}/resume")
        assert resume.status_code == 200, resume.text
        assert resume.json()["task_id"] == task_id
        assert resume.json()["status"] == "COMPLETED"

    # A and C had completed steps (bad payloads, but HTTP-successful), so
    # resume reuses their checkpoints and never contacts them again; only B
    # is retried.
    assert CALLS["A"] == 1
    assert CALLS["B"] == 3
    assert CALLS["C"] == 1

    # Verify StepExecution history preserves attempt 1 failure and adds attempt 2.
    repository = client.app.state.repository
    step_executions = repository.list_step_executions(task_id)
    b_attempts = [s for s in step_executions if s.step_name == "collect_supplier_b"]
    assert {(s.attempt, s.status) for s in b_attempts} == {
        (1, "FAILED"),
        (2, "FAILED"),
        (3, "COMPLETED"),
    }

    trace_actions = [row.action for row in repository.list_trace(task_id)]
    assert "resume_started" in trace_actions
    assert "checkpoint_reused" in trace_actions

"""Shared test fixtures."""

import socket
import threading
import time

import pytest
import uvicorn


def free_port() -> int:
    """Return an available local TCP port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(app) -> str:
    """Start a FastAPI app in a background thread and return its base URL."""

    port = free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("Server did not start in time")
        time.sleep(0.05)

    return f"http://127.0.0.1:{port}"


@pytest.fixture(scope="session")
def supplier_api_url():
    from mock_services.supplier_api.main import app

    return serve(app)


@pytest.fixture(scope="session")
def supplier_portal_url():
    from mock_services.supplier_portal.main import app

    return serve(app)

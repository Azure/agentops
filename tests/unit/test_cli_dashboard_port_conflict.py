"""Tests for the dashboard CLI port-conflict friendly fallback."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Generator

import pytest

from agentops.cli.app import _existing_agentops_dashboard, _port_in_use


@pytest.fixture
def free_port() -> int:
    """Return a port that was free at fixture time (race-prone but ok for tests)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_port_in_use_returns_false_for_unbound_port(free_port: int) -> None:
    assert _port_in_use("127.0.0.1", free_port) is False


def test_port_in_use_returns_true_for_listening_port(free_port: int) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", free_port))
    listener.listen(1)
    try:
        assert _port_in_use("127.0.0.1", free_port) is True
    finally:
        listener.close()


# ---------------------------------------------------------------------------
# /healthz heuristic
# ---------------------------------------------------------------------------


class _OkHealthzHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))

    def log_message(self, *_a):  # silence noisy stderr in tests
        pass


class _SomethingElseHandler(BaseHTTPRequestHandler):
    """A 200 response that does NOT match the AgentOps /healthz contract."""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"hello from a different service")

    def log_message(self, *_a):
        pass


def _start_server(handler_cls, free_port: int):
    server = HTTPServer(("127.0.0.1", free_port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_existing_dashboard_detected_when_healthz_matches(free_port: int) -> None:
    server, _t = _start_server(_OkHealthzHandler, free_port)
    try:
        assert _existing_agentops_dashboard("127.0.0.1", free_port) is True
    finally:
        server.shutdown()


def test_existing_dashboard_false_when_different_service(free_port: int) -> None:
    server, _t = _start_server(_SomethingElseHandler, free_port)
    try:
        assert _existing_agentops_dashboard("127.0.0.1", free_port) is False
    finally:
        server.shutdown()


def test_existing_dashboard_false_when_nothing_listening(free_port: int) -> None:
    assert _existing_agentops_dashboard("127.0.0.1", free_port) is False

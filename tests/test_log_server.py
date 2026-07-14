import importlib.util
import json
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Generator
from pathlib import Path
from types import ModuleType

import pytest

SERVER_FILE = Path(__file__).parents[1] / "deploy" / "log-server" / "server.py"


@pytest.fixture
def log_server(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[ModuleType, str]]:
    spec = importlib.util.spec_from_file_location("ottobot_log_server", SERVER_FILE)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "HOST", "127.0.0.1")

    httpd = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}
    )
    thread.start()

    try:
        host, port = httpd.server_address
        yield module, f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def assert_json_error(
    request: str | urllib.request.Request,
    status: int,
    expected: dict[str, object],
) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(request)

    with exc_info.value as response:
        assert response.code == status
        assert response.headers["Content-Type"] == "application/json; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-store"
        assert json.load(response) == expected


def test_index_endpoint_returns_the_web_ui(
    log_server: tuple[ModuleType, str],
) -> None:
    module, base_url = log_server

    with urllib.request.urlopen(f"{base_url}/") as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "text/html; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.read() == Path(module.HTML_FILE).read_bytes()


@pytest.mark.parametrize("path", ["/favicon.ico", "/favicon.svg"])
def test_favicon_endpoints_return_the_embedded_svg(
    path: str, log_server: tuple[ModuleType, str]
) -> None:
    module, base_url = log_server

    with urllib.request.urlopen(f"{base_url}{path}") as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "image/svg+xml"
        assert response.headers["Cache-Control"] == "public, max-age=86400"
        assert response.read() == module.FAVICON_SVG


@pytest.mark.parametrize(
    ("allowed_proxy_ip", "headers"),
    [
        pytest.param("192.0.2.1", {}, id="client-is-not-allowed-proxy"),
        pytest.param(
            "127.0.0.1",
            {"X-Forwarded-For": "198.51.100.1"},
            id="required-forwarded-headers-are-missing",
        ),
    ],
)
def test_requests_are_forbidden_when_proxy_checks_fail(
    allowed_proxy_ip: str,
    headers: dict[str, str],
    log_server: tuple[ModuleType, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, base_url = log_server
    monkeypatch.setattr(module, "HOST", "0.0.0.0")
    monkeypatch.setattr(module, "ALLOWED_PROXY_IP", allowed_proxy_ip)
    request = urllib.request.Request(f"{base_url}/", headers=headers)

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(request)

    with exc_info.value as response:
        assert response.code == 403
        assert response.reason == "Forbidden"
        assert response.headers.get_content_type() == "text/html"
        assert b"Forbidden" in response.read()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unknown_routes_return_not_found(
    method: str, log_server: tuple[ModuleType, str]
) -> None:
    _, base_url = log_server
    request = urllib.request.Request(
        f"{base_url}/not-an-endpoint",
        data=b"" if method == "POST" else None,
        method=method,
    )

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(request)

    with exc_info.value as response:
        assert response.code == 404
        assert response.reason == "Not Found"
        assert response.headers.get_content_type() == "text/html"
        assert b"Error code: 404" in response.read()


def test_services_endpoint_lists_compose_services(
    log_server: tuple[ModuleType, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module, base_url = log_server
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="ottobot\nworker\n")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with urllib.request.urlopen(f"{base_url}/services") as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/json; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-store"
        assert json.load(response) == ["ottobot", "worker"]

    assert calls == [
        (
            ["docker", "compose", "config", "--services"],
            {
                "cwd": module.BASE_DIR,
                "text": True,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.DEVNULL,
                "check": True,
            },
        )
    ]


def test_container_status_endpoint_returns_the_start_time(
    log_server: tuple[ModuleType, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module, base_url = log_server
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((command, kwargs))
        stdout = (
            "container-id\n"
            if command[0] == "docker" and command[1] == "compose"
            else "2026-07-13T12:34:56Z\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with urllib.request.urlopen(
        f"{base_url}/container/status?service=ottobot"
    ) as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/json; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-store"
        assert json.load(response) == {"started_at": "2026-07-13T12:34:56Z"}

    assert calls == [
        (
            ["docker", "compose", "ps", "-q", "ottobot"],
            {
                "cwd": module.BASE_DIR,
                "text": True,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.DEVNULL,
                "check": True,
            },
        ),
        (
            ["docker", "inspect", "--format={{.State.StartedAt}}", "container-id"],
            {
                "text": True,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.DEVNULL,
                "check": True,
            },
        ),
    ]


@pytest.mark.parametrize("endpoint", ["/container/status", "/events"])
def test_log_endpoints_reject_invalid_service_names(
    endpoint: str, log_server: tuple[ModuleType, str]
) -> None:
    _, base_url = log_server

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}{endpoint}?service=--help")

    with exc_info.value as response:
        assert response.code == 400
        assert response.reason == "Invalid service name"
        assert response.headers.get_content_type() == "text/html"
        assert b"Invalid service name" in response.read()


def test_container_status_returns_not_found_without_a_running_container(
    log_server: tuple[ModuleType, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module, base_url = log_server

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert_json_error(
        f"{base_url}/container/status?service=ottobot",
        404,
        {"error": "No running container found"},
    )


def test_container_status_returns_unavailable_when_docker_fails(
    log_server: tuple[ModuleType, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module, base_url = log_server

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise OSError("docker is unavailable")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert_json_error(
        f"{base_url}/container/status?service=ottobot",
        503,
        {"error": "Unable to inspect container"},
    )


def test_maintenance_status_endpoint_returns_update_state(
    log_server: tuple[ModuleType, str],
) -> None:
    module, base_url = log_server
    module.UPDATE_STATE.update(running=False, last_exit_code=7)

    with urllib.request.urlopen(f"{base_url}/maintenance/update") as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/json; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-store"
        assert json.load(response) == {"running": False, "last_exit_code": 7}


def test_maintenance_update_endpoint_starts_an_update(
    log_server: tuple[ModuleType, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, base_url = log_server
    update_script = tmp_path / "update"
    update_script.write_text("#!/bin/sh\n")
    update_script.chmod(0o700)
    update_started = threading.Event()

    monkeypatch.setattr(module, "MAINTENANCE_UPDATE_SCRIPT", str(update_script))
    monkeypatch.setattr(module, "run_maintenance_update", update_started.set)
    request = urllib.request.Request(
        f"{base_url}/maintenance/update",
        data=b"",
        headers={"X-Ottobot-Action": "maintenance-update"},
        method="POST",
    )

    with urllib.request.urlopen(request) as response:
        assert response.status == 202
        assert response.headers["Content-Type"] == "application/json; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-store"
        assert json.load(response) == {"running": True, "last_exit_code": None}

    assert update_started.wait(timeout=1)


@pytest.mark.parametrize("action", [None, "wrong-action"])
def test_maintenance_update_rejects_missing_or_invalid_action_header(
    action: str | None, log_server: tuple[ModuleType, str]
) -> None:
    _, base_url = log_server
    headers = {} if action is None else {"X-Ottobot-Action": action}
    request = urllib.request.Request(
        f"{base_url}/maintenance/update",
        data=b"",
        headers=headers,
        method="POST",
    )

    assert_json_error(request, 400, {"error": "Missing update action header"})


def test_maintenance_update_reports_a_missing_script(
    log_server: tuple[ModuleType, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, base_url = log_server
    monkeypatch.setattr(module, "MAINTENANCE_UPDATE_SCRIPT", str(tmp_path / "missing"))
    request = urllib.request.Request(
        f"{base_url}/maintenance/update",
        data=b"",
        headers={"X-Ottobot-Action": "maintenance-update"},
        method="POST",
    )

    assert_json_error(request, 500, {"error": "Maintenance update script not found"})


def test_maintenance_update_rejects_a_non_executable_script(
    log_server: tuple[ModuleType, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, base_url = log_server
    update_script = tmp_path / "update"
    update_script.write_text("#!/bin/sh\n")
    update_script.chmod(0o600)
    monkeypatch.setattr(module, "MAINTENANCE_UPDATE_SCRIPT", str(update_script))
    request = urllib.request.Request(
        f"{base_url}/maintenance/update",
        data=b"",
        headers={"X-Ottobot-Action": "maintenance-update"},
        method="POST",
    )

    assert_json_error(
        request, 500, {"error": "Maintenance update script is not executable"}
    )


def test_maintenance_update_rejects_a_second_update(
    log_server: tuple[ModuleType, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, base_url = log_server
    update_script = tmp_path / "update"
    update_script.write_text("#!/bin/sh\n")
    update_script.chmod(0o700)
    monkeypatch.setattr(module, "MAINTENANCE_UPDATE_SCRIPT", str(update_script))
    module.UPDATE_STATE.update(running=True, last_exit_code=None)
    request = urllib.request.Request(
        f"{base_url}/maintenance/update",
        data=b"",
        headers={"X-Ottobot-Action": "maintenance-update"},
        method="POST",
    )

    assert_json_error(request, 409, {"error": "Maintenance update is already running"})


@pytest.mark.parametrize(
    ("service", "expected_command"),
    [
        (
            "ottobot",
            ["docker", "compose", "logs", "-f", "--tail=200", "ottobot"],
        ),
        (
            "__ottobot_update_log__",
            ["tail", "-n", "200", "-F", "/var/log/ottobot-update.log"],
        ),
    ],
)
def test_events_endpoint_streams_expected_log_source(
    service: str,
    expected_command: list[str],
    log_server: tuple[ModuleType, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, base_url = log_server
    popen_calls: list[tuple[list[str], dict[str, object]]] = []
    killed_process_groups: list[tuple[int, int]] = []
    process_killed = threading.Event()

    class FakeProcess:
        pid = 1234
        stdout = iter(["first line\n", "second line\r\n"])

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        popen_calls.append((command, kwargs))
        return FakeProcess()

    def fake_killpg(pid: int, sig: int) -> None:
        killed_process_groups.append((pid, sig))
        process_killed.set()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(module.os, "killpg", fake_killpg)

    expected_body = b"data: first line\n\ndata: second line\n\n"
    with urllib.request.urlopen(f"{base_url}/events?service={service}") as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "text/event-stream; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Accel-Buffering"] == "no"
        assert response.read(len(expected_body)) == expected_body

    assert popen_calls == [
        (
            expected_command,
            {
                "cwd": module.BASE_DIR,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
                "errors": "replace",
                "start_new_session": True,
            },
        )
    ]
    assert process_killed.wait(timeout=1)
    assert killed_process_groups == [(1234, module.signal.SIGTERM)]

"""The hourly wake must run one growth step. Opening the site must not."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sync.growth_cycle import TickResult
from sync.wake_server import GROWTH_WAKE_PATH, is_growth_wake, serve
from scripts.wake_dashboard import wake_accepted, wake_url

SECRET = "wake-test-secret"


class _Page(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"page"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return


def test_wake_path_is_exact() -> None:
    assert is_growth_wake(b"GET /internal/growth-tick HTTP/1.1\r\n\r\n")
    assert is_growth_wake(b"GET /internal/growth-tick?x=1 HTTP/1.1\r\n\r\n")
    assert not is_growth_wake(b"GET / HTTP/1.1\r\n\r\n")
    assert not is_growth_wake(b"POST /internal/growth-tick HTTP/1.1\r\n\r\n")
    assert wake_url("https://meta-dashboard.onrender.com/") == (
        "https://meta-dashboard.onrender.com" + GROWTH_WAKE_PATH
    )


def test_wake_runs_one_step_and_the_site_does_not() -> None:
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    calls: list[str] = []

    def tick() -> TickResult:
        calls.append("tick")
        return TickResult(action="stored", cycle_id=4, username="theartofliving.online")

    ready = threading.Event()
    bound: dict[str, int] = {}
    threading.Thread(
        target=serve,
        args=("127.0.0.1", 0, upstream.server_address[1], tick),
        kwargs={"ready": ready, "bound": bound, "secret": SECRET},
        daemon=True,
    ).start()
    assert ready.wait(5)
    base = f"http://127.0.0.1:{bound['port']}"

    with urllib.request.urlopen(base + "/", timeout=5) as response:
        assert response.read() == b"page"
    assert calls == []

    request = urllib.request.Request(
        base + GROWTH_WAKE_PATH,
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read().decode("utf-8")
    assert body.startswith("growth-tick=ok\n")
    assert wake_accepted(200, body)
    assert "action=stored" in body
    assert "cycle=4" in body
    assert "account=theartofliving.online" in body
    assert calls == ["tick"]

    without_secret = urllib.request.Request(base + GROWTH_WAKE_PATH)
    try:
        urllib.request.urlopen(without_secret, timeout=5)
        raise AssertionError("expected 401")
    except urllib.error.HTTPError as exc:
        denied = exc.read().decode("utf-8")
        assert exc.code == 401
        assert denied.startswith("growth-tick=unauthorized")
        assert not wake_accepted(exc.code, denied)
    assert calls == ["tick"]

    html = "<!DOCTYPE html><html><body>Streamlit</body></html>"
    assert not wake_accepted(200, html)
    upstream.shutdown()

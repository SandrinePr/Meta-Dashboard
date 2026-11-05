"""Public port in front of Streamlit.

A wake request to /internal/growth-tick runs one due growth step in this
process and returns a plain-text result. The request must carry the shared
secret. Every other request is passed through to Streamlit. Opening the site
does not process the queue, and the Streamlit page is not a successful tick.
"""

from __future__ import annotations

import hmac
import os
import socket
import threading
from typing import Callable

GROWTH_WAKE_PATH = "/internal/growth-tick"
TICK_OK = "growth-tick=ok"


def is_growth_wake(header: bytes) -> bool:
    line = header.split(b"\r\n", 1)[0]
    parts = line.split()
    if len(parts) < 2 or parts[0] != b"GET":
        return False
    path = parts[1].split(b"?", 1)[0]
    return path == GROWTH_WAKE_PATH.encode("ascii")


def header_value(header: bytes, name: str) -> str:
    target = name.lower().encode("ascii")
    for line in header.split(b"\r\n")[1:]:
        if b":" not in line:
            continue
        key, value = line.split(b":", 1)
        if key.strip().lower() == target:
            return value.strip().decode("latin-1")
    return ""


def presented_secret(header: bytes) -> str:
    authorization = header_value(header, "Authorization")
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip()
    return ""


def secret_matches(header: bytes, secret: str) -> bool:
    presented = presented_secret(header)
    if not secret or not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), secret.encode("utf-8"))


def format_tick_body(result) -> str:
    cycle = "" if result.cycle_id is None else str(result.cycle_id)
    account = result.username or ""
    lines = [
        TICK_OK,
        f"action={result.action}",
        f"cycle={cycle}",
        f"account={account}",
    ]
    detail = getattr(result, "detail", None)
    if detail:
        lines.append("detail=" + str(detail).replace("\r", " ").replace("\n", " ")[:300])
    return "\n".join(lines) + "\n"


def _http_response(status: int, reason: str, body: str) -> bytes:
    payload = body.encode("utf-8")
    header = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    return header.encode("ascii") + payload


def _read_header(conn: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _proxy(client: socket.socket, pending: bytes, upstream_port: int) -> None:
    upstream = socket.create_connection(("127.0.0.1", upstream_port), timeout=10)
    upstream.settimeout(None)
    client.settimeout(None)
    upstream.sendall(pending)
    left = threading.Thread(target=_pump, args=(client, upstream), daemon=True)
    right = threading.Thread(target=_pump, args=(upstream, client), daemon=True)
    left.start()
    right.start()
    left.join()
    right.join()
    upstream.close()


def handle_client(
    client: socket.socket,
    upstream_port: int,
    tick: Callable,
    secret: str,
) -> None:
    try:
        client.settimeout(30)
        header = _read_header(client)
        if not header:
            return
        if is_growth_wake(header):
            if not secret:
                body, status, reason = (
                    "growth-tick=unconfigured\naction=unconfigured\n",
                    503,
                    "Service Unavailable",
                )
            elif not secret_matches(header, secret):
                body, status, reason = (
                    "growth-tick=unauthorized\naction=unauthorized\n",
                    401,
                    "Unauthorized",
                )
            else:
                try:
                    body = format_tick_body(tick())
                    status, reason = 200, "OK"
                except Exception as exc:
                    body = f"growth-tick=error\naction=error\ndetail={type(exc).__name__}\n"
                    status, reason = 500, "Internal Server Error"
            client.sendall(_http_response(status, reason, body))
            return
        try:
            _proxy(client, header, upstream_port)
        except OSError:
            client.sendall(_http_response(503, "Service Unavailable", "action=unavailable\n"))
    except OSError:
        return
    finally:
        client.close()


def serve(
    host: str,
    port: int,
    upstream_port: int,
    tick: Callable,
    ready: threading.Event | None = None,
    bound: dict[str, int] | None = None,
    secret: str | None = None,
) -> None:
    if secret is None:
        secret = os.environ.get("GROWTH_TICK_SECRET", "").strip()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(32)
    if bound is not None:
        bound["port"] = int(server.getsockname()[1])
    if ready is not None:
        ready.set()
    try:
        while True:
            client, _addr = server.accept()
            threading.Thread(
                target=handle_client,
                args=(client, upstream_port, tick, secret),
                daemon=True,
            ).start()
    finally:
        server.close()

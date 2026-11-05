"""Start the scheduler, Streamlit, and the public port.

The public port answers /internal/growth-tick in this process and forwards
every other request, including the site itself, to Streamlit on a local port.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_until_open(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    os.environ.setdefault("ENABLE_SCHEDULER", "1")

    from sync.scheduler import run_growth_tick_once, start_scheduler
    from sync.wake_server import serve

    public_port = int(os.environ.get("PORT", "8501"))
    internal_port = _free_port()
    env = os.environ.copy()
    env["DASHBOARD_SCHEDULER"] = "0"
    env["PORT"] = str(internal_port)
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ROOT / "app.py"),
        "--server.port",
        str(internal_port),
        "--server.address",
        "127.0.0.1",
        "--server.headless",
        "true",
        "--server.enableStaticServing",
        "true",
    ]
    child = subprocess.Popen(cmd, env=env)

    def _stop_when_child_exits() -> None:
        code = child.wait()
        os._exit(code if code is not None else 1)

    threading.Thread(target=_stop_when_child_exits, daemon=True).start()
    if not _wait_until_open(internal_port, 90):
        child.terminate()
        return 1

    def _publish_and_stop(signum, _frame) -> None:
        try:
            from config import get_settings
            from db.durable_sqlite import publish_if_configured

            publish_if_configured(get_settings().database_path)
        except Exception:
            logging.getLogger(__name__).exception("SQLite snapshot on shutdown failed")
        child.terminate()
        os._exit(0)

    signal.signal(signal.SIGTERM, _publish_and_stop)
    start_scheduler()
    serve("0.0.0.0", public_port, internal_port, run_growth_tick_once)
    return child.wait()


if __name__ == "__main__":
    raise SystemExit(main())

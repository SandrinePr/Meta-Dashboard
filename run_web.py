"""Start the daily fetch scheduler, then Streamlit.

Used as the Render web start command so jobs run in a process that stays
up independent of a browser WebSocket session, and still share SQLite with
the dashboard child process via the filesystem.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    os.environ.setdefault("ENABLE_SCHEDULER", "1")

    from sync.scheduler import start_scheduler

    start_scheduler()

    port = os.environ.get("PORT", "8501")
    env = os.environ.copy()
    env["DASHBOARD_SCHEDULER"] = "0"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ROOT / "app.py"),
        "--server.port",
        str(port),
        "--server.address",
        "0.0.0.0",
        "--server.headless",
        "true",
    ]
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    raise SystemExit(main())

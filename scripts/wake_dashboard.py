"""HTTP ping that wakes the Render web process and runs one growth step.

The request goes to /internal/growth-tick. A request to the site root only
opens Streamlit and does not process the queue.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Load the path constant from the file itself. Importing the sync package
# pulls in the dashboard dependencies, which the hourly workflow does not install.
_spec = importlib.util.spec_from_file_location(
    "growth_wake_path",
    ROOT / "sync" / "wake_server.py",
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("growth wake path module is missing")
_wake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_wake)
GROWTH_WAKE_PATH = _wake.GROWTH_WAKE_PATH


def wake_url(base: str) -> str:
    return base.strip().rstrip("/") + GROWTH_WAKE_PATH


def wake_accepted(status: int, body: str) -> bool:
    """True only for a real tick response, never for the Streamlit page."""
    if status != 200:
        return False
    lowered = body.lower()
    if "<html" in lowered or "<!doctype" in lowered:
        return False
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines or lines[0] != "growth-tick=ok":
        return False
    actions = [line.split("=", 1)[1] for line in lines if line.startswith("action=")]
    if len(actions) != 1:
        return False
    return actions[0] not in {
        "",
        "unconfigured",
        "unauthorized",
        "publish_failed",
        "storage_unavailable",
        "error",
    }


def main() -> int:
    url = (os.environ.get("DASHBOARD_URL") or "").strip()
    secret = (os.environ.get("GROWTH_TICK_SECRET") or "").strip()
    if not url:
        print("DASHBOARD_URL is not set", file=sys.stderr)
        return 1
    if not secret:
        print("GROWTH_TICK_SECRET is not set", file=sys.stderr)
        return 1
    target = wake_url(url)
    request = urllib.request.Request(
        target,
        headers={
            "User-Agent": "meta-dashboard-wake/1.0",
            "Authorization": f"Bearer {secret}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = response.read(4000).decode("utf-8", "replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read(4000).decode("utf-8", "replace")
        status = int(exc.code)
    except Exception as exc:
        print(f"wake_failed={type(exc).__name__} url={target}", file=sys.stderr)
        return 1
    print(f"wake={status} url={target}")
    print(body)
    if not wake_accepted(status, body):
        print("wake_rejected", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

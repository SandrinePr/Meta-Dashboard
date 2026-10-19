"""HTTP ping to wake the Render web process so in-process jobs can run."""

from __future__ import annotations

import os
import sys
import urllib.request


def main() -> int:
    url = (os.environ.get("DASHBOARD_URL") or "").strip().rstrip("/")
    if not url:
        print("DASHBOARD_URL is not set", file=sys.stderr)
        return 1
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "meta-dashboard-wake/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            print(f"wake={response.status} url={url}")
    except Exception as exc:
        print(f"wake_failed={exc} url={url}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

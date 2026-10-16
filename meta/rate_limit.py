"""Helpers for Meta Graph API rate-limit detection (no extra API calls)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# Application request limit / rate limiting.
RATE_LIMIT_ERROR_CODES = {4, 17, 32, 613}

_RETRY_AFTER_IN_MESSAGE = re.compile(
    r"(?:retry after|wait(?: for)?|try again in)\s+(\d+)\s*(second|minute|hour|sec|min|hr)s?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RateLimitInfo:
    """Safe metadata extracted from a Meta rate-limit response."""

    error_code: int | None
    message: str
    retry_after_seconds: int | None
    blocked_until: str | None
    usage_headers: dict[str, Any]
    fbtrace_id: str | None


def is_rate_limit_error(
    *,
    error_code: int | None = None,
    message: str | None = None,
    status_code: int | None = None,
) -> bool:
    if error_code in RATE_LIMIT_ERROR_CODES:
        return True
    if status_code == 429:
        return True
    text = (message or "").lower()
    return "request limit" in text or "rate limit" in text


def _parse_usage_header(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def extract_usage_headers(headers: Any) -> dict[str, Any]:
    """Collect safe usage/throttle headers from an HTTP response."""
    if headers is None:
        return {}
    keys = (
        "x-app-usage",
        "x-business-use-case-usage",
        "x-ad-account-usage",
        "x-fb-ads-insights-throttle",
        "retry-after",
    )
    out: dict[str, Any] = {}
    for key in keys:
        raw = None
        try:
            raw = headers.get(key)
        except Exception:  # noqa: BLE001
            raw = None
        if raw is None:
            continue
        if key == "retry-after":
            out[key] = str(raw)
        else:
            out[key] = _parse_usage_header(str(raw))
    return out


def parse_retry_after_seconds(
    *,
    headers: Any = None,
    error_payload: dict[str, Any] | None = None,
    message: str | None = None,
) -> int | None:
    """Return Meta-provided wait seconds only. Never invent a reset time."""
    if headers is not None:
        try:
            retry_after = headers.get("Retry-After") or headers.get("retry-after")
        except Exception:  # noqa: BLE001
            retry_after = None
        if retry_after is not None:
            try:
                seconds = int(float(str(retry_after).strip()))
                if seconds > 0:
                    return seconds
            except ValueError:
                pass

    payload = error_payload or {}
    for key in ("error_data", "error_user_msg", "error_user_title", "message"):
        value = payload.get(key)
        if isinstance(value, dict):
            # Rare nested shapes from Graph API.
            for nested_key in ("retry_after", "wait_time", "estimated_time_to_regain_access"):
                nested = value.get(nested_key)
                if nested is not None:
                    try:
                        seconds = int(float(nested))
                        if seconds > 0:
                            return seconds
                    except (TypeError, ValueError):
                        pass
            continue
        if value is None:
            continue
        text = str(value)
        match = _RETRY_AFTER_IN_MESSAGE.search(text)
        if match:
            amount = int(match.group(1))
            unit = match.group(2).lower()
            if unit.startswith("hour") or unit.startswith("hr"):
                return amount * 3600
            if unit.startswith("min"):
                return amount * 60
            return amount

    if message:
        match = _RETRY_AFTER_IN_MESSAGE.search(message)
        if match:
            amount = int(match.group(1))
            unit = match.group(2).lower()
            if unit.startswith("hour") or unit.startswith("hr"):
                return amount * 3600
            if unit.startswith("min"):
                return amount * 60
            return amount
    return None


def build_rate_limit_info(
    *,
    error_code: int | None,
    message: str,
    headers: Any = None,
    error_payload: dict[str, Any] | None = None,
    fbtrace_id: str | None = None,
    now: datetime | None = None,
) -> RateLimitInfo:
    retry_after = parse_retry_after_seconds(
        headers=headers,
        error_payload=error_payload,
        message=message,
    )
    current = now or datetime.now(tz=timezone.utc)
    blocked_until = None
    if retry_after is not None:
        blocked_until = (current + timedelta(seconds=retry_after)).isoformat()
    return RateLimitInfo(
        error_code=error_code,
        message=message,
        retry_after_seconds=retry_after,
        blocked_until=blocked_until,
        usage_headers=extract_usage_headers(headers),
        fbtrace_id=fbtrace_id,
    )


def sanitize_request_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Copy params with secrets removed for safe logging."""
    if not params:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        key_l = str(key).lower()
        if "token" in key_l or key_l in {"access_token", "app_secret", "client_secret"}:
            cleaned[key] = "[redacted]"
        else:
            cleaned[key] = value
    return cleaned

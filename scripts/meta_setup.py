"""Shared helpers for Meta setup scripts (env checks, masking, token exchange)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"

META_ENV_KEYS = (
    "META_APP_ID",
    "META_APP_SECRET",
    "META_USER_ACCESS_TOKEN",
    "META_PAGE_ID",
    "META_PAGE_ACCESS_TOKEN",
    "META_INSTAGRAM_BUSINESS_ACCOUNT_ID",
    "META_GRAPH_API_VERSION",
)


def ensure_project_path() -> None:
    """Allow imports from project root when scripts are run directly."""
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def mask_secret(value: str | None, *, visible: int = 4) -> str:
    """Return masked secret showing only first/last N characters."""
    if not value:
        return "<leeg>"
    cleaned = value.strip()
    if len(cleaned) <= visible * 2:
        return "*" * len(cleaned)
    return f"{cleaned[:visible]}...{cleaned[-visible:]}"


def require_env_file() -> Path:
    """Ensure `.env` exists before setup scripts run."""
    if not ENV_PATH.exists():
        raise FileNotFoundError(
            f".env niet gevonden op {ENV_PATH}. "
            f"Maak deze aan met: copy {ENV_EXAMPLE_PATH.name} .env"
        )
    return ENV_PATH


def load_dotenv_values() -> dict[str, str]:
    """Load environment values from `.env` and process environment."""
    require_env_file()
    load_dotenv(ENV_PATH)
    return {key: os.getenv(key, "").strip() for key in META_ENV_KEYS}


def get_missing_keys(values: dict[str, str], required: tuple[str, ...]) -> list[str]:
    """Return keys that are empty or placeholder-like."""
    placeholders = {
        "",
        "your_meta_app_id",
        "your_app_secret_here",
        "your_meta_app_secret",
        "replace_with_long_lived_user_token",
        "replace_with_page_access_token",
        "your_facebook_page_id",
        "your_instagram_business_account_id",
    }
    missing: list[str] = []
    for key in required:
        if values.get(key, "") in placeholders:
            missing.append(key)
    return missing


def print_env_status(values: dict[str, str], *, required: tuple[str, ...]) -> list[str]:
    """Print masked env status and return missing required keys."""
    missing = get_missing_keys(values, required)
    print("\n.env status:")
    for key in META_ENV_KEYS:
        if key in {"META_APP_SECRET", "META_USER_ACCESS_TOKEN", "META_PAGE_ACCESS_TOKEN"}:
            display = mask_secret(values.get(key))
        else:
            display = values.get(key) or "<leeg>"
        status = "OK" if key not in missing else "MISSING"
        print(f"  - {key}: {display} [{status}]")
    if missing:
        print("\nOntbrekende verplichte waarden:")
        for key in missing:
            print(f"  - {key}")
    return missing


def update_env_file(updates: dict[str, str]) -> None:
    """Update or append keys in `.env` without printing secret values."""
    env_path = require_env_file()
    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated_keys: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue

        key, _, _ = stripped.partition("=")
        key = key.strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def exchange_long_lived_user_token(
    *,
    app_id: str,
    app_secret: str,
    short_lived_token: str,
    graph_api_version: str = "v25.0",
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Exchange short-lived user token for long-lived user token."""
    url = f"https://graph.facebook.com/{graph_api_version}/oauth/access_token"
    response = requests.get(
        url,
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_lived_token,
        },
        timeout=timeout_seconds,
    )
    try:
        payload: dict[str, Any] = response.json()
    except ValueError as exc:
        raise RuntimeError("Meta token exchange returned non-JSON response.") from exc

    if not response.ok:
        error = payload.get("error", {})
        message = error.get("message") if isinstance(error, dict) else response.text
        raise RuntimeError(f"Token exchange failed: {message}")

    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("Token exchange succeeded but no access_token was returned.")
    return payload


def fetch_me_accounts(
    *,
    user_access_token: str,
    graph_api_version: str = "v25.0",
    timeout_seconds: int = 30,
) -> list[dict[str, Any]]:
    """Fetch pages from /me/accounts."""
    url = f"https://graph.facebook.com/{graph_api_version}/me/accounts"
    response = requests.get(
        url,
        params={
            "fields": "id,name,access_token,instagram_business_account{id,username}",
            "access_token": user_access_token,
        },
        timeout=timeout_seconds,
    )
    payload = response.json()
    if not response.ok:
        error = payload.get("error", {})
        message = error.get("message") if isinstance(error, dict) else response.text
        raise RuntimeError(f"/me/accounts failed: {message}")

    data = payload.get("data", [])
    return data if isinstance(data, list) else []


def fetch_page_by_id(
    *,
    page_id: str,
    user_access_token: str,
    graph_api_version: str = "v25.0",
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Fetch a page node directly, including access_token when permitted."""
    url = f"https://graph.facebook.com/{graph_api_version}/{page_id}"
    response = requests.get(
        url,
        params={
            "fields": "id,name,access_token,instagram_business_account{id,username}",
            "access_token": user_access_token,
        },
        timeout=timeout_seconds,
    )
    payload = response.json()
    if not response.ok:
        error = payload.get("error", {})
        message = error.get("message") if isinstance(error, dict) else response.text
        raise RuntimeError(f"Page lookup failed for {page_id}: {message}")
    return payload if isinstance(payload, dict) else {}


def _extract_ig_account_id(page_payload: dict[str, Any]) -> str | None:
    ig_account = page_payload.get("instagram_business_account")
    if isinstance(ig_account, dict):
        ig_id = ig_account.get("id")
        return str(ig_id) if ig_id else None
    return None


def _find_page_in_accounts(pages: list[dict[str, Any]], page_id: str) -> dict[str, Any] | None:
    for page in pages:
        if str(page.get("id")) == page_id:
            return page
    return None


def resolve_page_access_token(
    *,
    page_id: str,
    user_access_token: str,
    graph_api_version: str = "v25.0",
    timeout_seconds: int = 30,
) -> tuple[dict[str, Any], str]:
    """Resolve page payload and source label for a configured page id."""
    pages = fetch_me_accounts(
        user_access_token=user_access_token,
        graph_api_version=graph_api_version,
        timeout_seconds=timeout_seconds,
    )
    page_payload = _find_page_in_accounts(pages, page_id)
    source = "/me/accounts"
    if page_payload is None:
        page_payload = fetch_page_by_id(
            page_id=page_id,
            user_access_token=user_access_token,
            graph_api_version=graph_api_version,
            timeout_seconds=timeout_seconds,
        )
        source = f"/{page_id}"
    return page_payload, source


def refresh_meta_tokens(
    values: dict[str, str],
    *,
    timeout_seconds: int = 30,
) -> dict[str, str]:
    """Exchange user token and refresh page token using values from `.env`."""
    required = ("META_APP_ID", "META_APP_SECRET", "META_USER_ACCESS_TOKEN", "META_PAGE_ID")
    missing = get_missing_keys(values, required)
    if missing:
        raise RuntimeError(f"Ontbrekende .env waarden: {', '.join(missing)}")

    graph_version = values.get("META_GRAPH_API_VERSION") or "v25.0"
    exchange = exchange_long_lived_user_token(
        app_id=values["META_APP_ID"],
        app_secret=values["META_APP_SECRET"],
        short_lived_token=values["META_USER_ACCESS_TOKEN"],
        graph_api_version=graph_version,
        timeout_seconds=timeout_seconds,
    )
    long_lived_token = str(exchange["access_token"])

    page_payload, _source = resolve_page_access_token(
        page_id=values["META_PAGE_ID"],
        user_access_token=long_lived_token,
        graph_api_version=graph_version,
        timeout_seconds=timeout_seconds,
    )
    page_token = page_payload.get("access_token")
    if not page_token:
        raise RuntimeError(
            "Meta gaf geen Page Access Token terug. Controleer META_PAGE_ID en token-permissions."
        )

    updates = {
        "META_USER_ACCESS_TOKEN": long_lived_token,
        "META_PAGE_ACCESS_TOKEN": str(page_token),
    }
    ig_account_id = _extract_ig_account_id(page_payload)
    if ig_account_id:
        updates["META_INSTAGRAM_BUSINESS_ACCOUNT_ID"] = ig_account_id
    return updates

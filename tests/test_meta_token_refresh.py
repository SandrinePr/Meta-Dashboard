"""Tests for Meta token refresh helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import responses

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from meta_setup import refresh_meta_tokens


@responses.activate
def test_refresh_meta_tokens_exchanges_user_and_page_tokens() -> None:
    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/oauth/access_token",
        json={"access_token": "long-lived-user", "token_type": "bearer", "expires_in": 5184000},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/me/accounts",
        json={
            "data": [
                {
                    "id": "page-123",
                    "name": "Test Page",
                    "access_token": "page-token-abc",
                    "instagram_business_account": {"id": "ig-456", "username": "test"},
                }
            ]
        },
        status=200,
    )

    values = {
        "META_APP_ID": "app-id",
        "META_APP_SECRET": "app-secret",
        "META_USER_ACCESS_TOKEN": "short-lived-user",
        "META_PAGE_ID": "page-123",
        "META_GRAPH_API_VERSION": "v25.0",
    }
    updates = refresh_meta_tokens(values)

    assert updates["META_USER_ACCESS_TOKEN"] == "long-lived-user"
    assert updates["META_PAGE_ACCESS_TOKEN"] == "page-token-abc"
    assert updates["META_INSTAGRAM_BUSINESS_ACCOUNT_ID"] == "ig-456"


@responses.activate
def test_refresh_meta_tokens_falls_back_to_page_lookup() -> None:
    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/oauth/access_token",
        json={"access_token": "long-lived-user"},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/me/accounts",
        json={"data": []},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/page-999",
        json={
            "id": "page-999",
            "access_token": "direct-page-token",
        },
        status=200,
    )

    values = {
        "META_APP_ID": "app-id",
        "META_APP_SECRET": "app-secret",
        "META_USER_ACCESS_TOKEN": "short-lived-user",
        "META_PAGE_ID": "page-999",
        "META_GRAPH_API_VERSION": "v25.0",
    }
    updates = refresh_meta_tokens(values)

    assert updates["META_PAGE_ACCESS_TOKEN"] == "direct-page-token"
    assert "META_INSTAGRAM_BUSINESS_ACCOUNT_ID" not in updates

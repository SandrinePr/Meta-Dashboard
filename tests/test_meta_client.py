"""Unit tests for MetaClient without real HTTP calls."""

from __future__ import annotations

import requests
import responses

from meta.client import MetaClient, MetaRequestError, TOKEN_EXPIRED_MESSAGE, format_meta_client_error


def _build_client() -> MetaClient:
    return MetaClient(
        base_url="https://graph.facebook.com/v25.0",
        timeout_seconds=5,
        user_access_token="user-token",
        page_access_token="page-token",
        default_page_id="123",
        default_instagram_business_account_id="456",
    )


@responses.activate
def test_get_paginated_follows_paging_next() -> None:
    client = _build_client()

    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/me/accounts",
        json={
            "data": [{"id": "1"}],
            "paging": {"next": "https://graph.facebook.com/v25.0/me/accounts?after=abc"},
        },
        status=200,
    )
    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/me/accounts?after=abc",
        json={"data": [{"id": "2"}]},
        status=200,
    )

    pages = client.get_pages()
    assert [item["id"] for item in pages] == ["1", "2"]


@responses.activate
def test_retry_then_success_on_transient_server_error() -> None:
    client = _build_client()

    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/123/posts",
        json={"error": {"message": "temporary", "is_transient": True, "code": 2}},
        status=500,
    )
    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/123/posts",
        json={"data": [{"id": "post-1"}]},
        status=200,
    )

    posts = client.get_facebook_page_posts()
    assert posts[0]["id"] == "post-1"
    assert len(responses.calls) == 2


@responses.activate
def test_non_retryable_error_raises_meta_request_error() -> None:
    client = _build_client()

    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/123",
        json={"error": {"message": "permissions error", "code": 200}},
        status=403,
    )

    try:
        client.get_instagram_business_account_id()
        raise AssertionError("Expected MetaRequestError.")
    except MetaRequestError as exc:
        assert exc.status_code == 403
        assert exc.error_code == 200


def test_format_meta_client_error_maps_code_190() -> None:
    exc = MetaRequestError("OAuthException", error_code=190)
    assert format_meta_client_error(exc) == TOKEN_EXPIRED_MESSAGE


def test_format_meta_client_error_passes_through_other_errors() -> None:
    exc = MetaRequestError("permissions error", error_code=200)
    assert format_meta_client_error(exc) == "permissions error"


@responses.activate
def test_network_error_retries_and_raises_when_exhausted() -> None:
    client = _build_client()

    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/123/posts",
        body=requests.ConnectionError("connection down"),
    )

    try:
        client.get_facebook_page_posts()
        raise AssertionError("Expected requests.ConnectionError.")
    except requests.ConnectionError:
        # retry policy attempts up to 5 times
        assert len(responses.calls) == 5

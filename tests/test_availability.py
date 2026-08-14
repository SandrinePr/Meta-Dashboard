"""Tests for unavailable/deleted post detection."""

from __future__ import annotations

import json

from meta.availability import is_post_unavailable, is_unavailable_meta_error
from meta.client import MetaClientError, MetaRequestError


def test_is_unavailable_meta_error_code_100() -> None:
    exc = MetaRequestError(
        "Unsupported get request. Object with ID '123' does not exist.",
        error_code=100,
    )
    assert is_unavailable_meta_error(exc)


def test_is_unavailable_meta_error_message_phrase() -> None:
    exc = MetaRequestError("The post is no longer available on this platform.")
    assert is_unavailable_meta_error(exc)


def test_is_unavailable_meta_error_rejects_facebook_permission() -> None:
    exc = MetaRequestError(
        "(#10) Object does not exist, missing pages_read_engagement permission.",
        error_code=10,
    )
    assert not is_unavailable_meta_error(exc)


def test_is_unavailable_meta_error_rejects_token_error() -> None:
    exc = MetaRequestError("OAuthException", error_code=190)
    assert not is_unavailable_meta_error(exc)


def test_is_unavailable_meta_error_rejects_generic() -> None:
    exc = MetaClientError("Rate limit")
    assert not is_unavailable_meta_error(exc)


def test_is_post_unavailable_reads_raw_json_flag() -> None:
    raw = json.dumps({"rro_unavailable": True, "caption": "Old post"})
    assert is_post_unavailable(raw)


def test_is_post_unavailable_false_when_missing() -> None:
    assert not is_post_unavailable(json.dumps({"caption": "Live post"}))
    assert not is_post_unavailable(None)

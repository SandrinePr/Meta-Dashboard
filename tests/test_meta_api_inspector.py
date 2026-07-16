"""Tests for local helper logic in Meta API Inspector."""

from __future__ import annotations

from scripts.meta_api_inspector import (
    extract_hashtags,
    normalize_facebook_comment,
    normalize_facebook_post,
    normalize_instagram_comment,
    normalize_instagram_post,
)


def test_extract_hashtags_deduplicates_and_sorts() -> None:
    hashtags = extract_hashtags("Hi #CULIMAAT and #test and #CULIMAAT")
    assert hashtags == ["CULIMAAT", "test"]


def test_normalize_instagram_post_maps_expected_fields() -> None:
    payload = {
        "id": "ig1",
        "caption": "Caption #one",
        "media_type": "IMAGE",
        "media_url": "https://media",
        "thumbnail_url": None,
        "permalink": "https://perma",
        "timestamp": "2026-07-01T00:00:00+0000",
        "comments_count": 3,
    }
    post = normalize_instagram_post(payload)
    assert post.id == "ig1"
    assert post.hashtags == ["one"]
    assert "media_url" in post.available_fields
    assert "thumbnail_url" in post.missing_fields


def test_normalize_facebook_post_uses_message_and_attachments() -> None:
    payload = {
        "id": "fb1",
        "message": "Text #hello",
        "created_time": "2026-07-01T00:00:00+0000",
        "permalink_url": "https://fb",
        "full_picture": "https://img",
        "attachments": {
            "data": [
                {
                    "type": "photo",
                    "url": "https://attached",
                }
            ]
        },
    }
    post = normalize_facebook_post(payload)
    assert post.id == "fb1"
    assert post.media_type == "photo"
    assert post.media_url == "https://attached"
    assert post.hashtags == ["hello"]


def test_normalize_instagram_comment_maps_author_text_and_date() -> None:
    payload = {
        "id": "c1",
        "username": "alice",
        "text": "hello",
        "timestamp": "2026-07-01T00:00:00+0000",
    }
    comment = normalize_instagram_comment(payload)
    assert comment.author == "alice"
    assert comment.text == "hello"
    assert comment.date is not None


def test_normalize_facebook_comment_maps_from_name() -> None:
    payload = {
        "id": "c2",
        "from": {"id": "1", "name": "Bob"},
        "message": "great",
        "created_time": "2026-07-01T00:00:00+0000",
    }
    comment = normalize_facebook_comment(payload)
    assert comment.author == "Bob"
    assert comment.text == "great"
